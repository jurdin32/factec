"""Formularios para el admin de Django.

La contraseña del certificado nunca se muestra ni se guarda en claro: el
formulario la pide en un campo de tipo contraseña y la cifra antes de guardarla.
Al validar, además, se abre el ``.p12`` para comprobar que la contraseña es
correcta, que el certificado está vigente y que su RUC coincide con el del emisor.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from django import forms
from django.core.exceptions import ValidationError

from ..excepciones import ErrorFacturacion
from . import conf, sri_datos
from .documentos import LineaDocumento
from .models import ConfiguracionEmisor, extraer_ruc

__all__ = ["ConfiguracionEmisorForm", "LineaDocumentoForm"]


class ConfiguracionEmisorForm(forms.ModelForm):
    """Alta y edición de la configuración del emisor.

    Al escribir el RUC se consulta el catastro del SRI y se rellenan solos la
    razón social, el régimen, la categoría y la obligación de llevar
    contabilidad. Solo hay que aportar lo que **no** consta en el servicio: el
    nombre comercial, las direcciones, el establecimiento y el certificado.
    """

    clave_certificado = forms.CharField(
        label="contraseña del certificado",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=(
            "Contraseña del archivo .p12. Se guarda cifrada. Al editar, déjela vacía "
            "para conservar la actual."
        ),
    )
    consultar_sri = forms.BooleanField(
        label="Consultar los datos del RUC en el SRI al guardar",
        required=False,
        initial=True,
        help_text=(
            "Rellena razón social, régimen, categoría y obligado a llevar "
            "contabilidad con lo que publica el SRI."
        ),
    )

    class Meta:
        model = ConfiguracionEmisor
        fields = "__all__"
        exclude = ("clave_certificado_cifrada",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Avisos de la consulta al SRI (los muestra el admin como mensajes).
        self.avisos_sri: list = []
        self.datos_sri: Any = None
        self._error_sri = ""
        self._clave_previa = bool(
            self.instance and self.instance.pk and self.instance.clave_certificado_cifrada
        )
        if self._clave_previa:
            self.fields["clave_certificado"].help_text = (
                "Ya hay una contraseña guardada. Déjela vacía para conservarla."
            )
        elif not self.instance.pk:
            self.fields["clave_certificado"].required = True

        # Marca los campos que se completan solos desde el SRI.
        for campo in sri_datos.CAMPOS_AUTOMATICOS:
            if campo in self.fields:
                ayuda = self.fields[campo].help_text
                self.fields[campo].help_text = (
                    "Se completa desde el SRI al indicar el RUC. " + (ayuda or "")
                ).strip()
                self.fields[campo].required = False

        if not conf.obtener("CONSULTAR_SRI_AUTOMATICAMENTE", True):
            self.fields["consultar_sri"].initial = False
            self.fields["consultar_sri"].disabled = True
            self.fields["consultar_sri"].help_text = (
                "Desactivado por CONSULTAR_SRI_AUTOMATICAMENTE."
            )

    # ------------------------------------------------------------- consulta

    def _consultar_sri(self, ruc: str) -> Optional[Any]:
        """Consulta el RUC y deja los datos en ``self.datos_sri``."""
        if not conf.obtener("CONSULTAR_SRI_AUTOMATICAMENTE", True):
            return None
        try:
            resultado = sri_datos.consultar_datos(
                ruc, timeout=conf.obtener("TIMEOUT_CONSULTA_SRI")
            )
        except ErrorFacturacion as exc:
            self._error_sri = str(exc)
            return None

        self._error_sri = ""
        self.datos_sri = resultado.datos
        self.avisos_sri = resultado.avisos
        if not resultado.encontrado:
            self._error_sri = f"El SRI no devolvió datos para el RUC {ruc}."
            return None
        return resultado

    def _aplicar_datos_sri(self, resultado: Any) -> None:
        """Vuelca en el formulario lo que publica el SRI.

        Solo rellena los campos que el usuario **no** haya escrito: lo que ya
        viene informado se respeta, que para eso lo escribió.
        """
        for campo, valor in resultado.campos.items():
            if campo not in self.fields:
                continue
            actual = self.cleaned_data.get(campo)
            vacio = actual in (None, "") or (isinstance(actual, str) and not actual.strip())
            if not vacio:
                continue
            self.cleaned_data[campo] = valor
            # Se actualiza el valor inicial para que el formulario se vuelva a
            # pintar ya relleno si hay que mostrar errores.
            self.initial[campo] = valor
            self.fields[campo].initial = valor

    def clean(self) -> dict:
        datos = super().clean()

        # --- comprobaciones de formato ------------------------------------
        ruc: str = datos.get("ruc") or ""
        if ruc and (len(ruc) != 13 or not ruc.isdigit()):
            self.add_error("ruc", "El RUC debe tener 13 dígitos numéricos.")
        for campo, etiqueta in (("estab", "establecimiento"), ("pto_emi", "punto de emisión")):
            valor: str = datos.get(campo) or ""
            if valor and (len(valor) != 3 or not valor.isdigit()):
                self.add_error(campo, f"El {etiqueta} debe tener 3 dígitos (por ejemplo 001).")
        agente: str = datos.get("agente_retencion") or ""
        if agente and not agente.isdigit():
            self.add_error("agente_retencion", "Debe contener solo dígitos.")

        if self.errors:
            return datos

        # --- datos del SRI -------------------------------------------------
        if ruc and datos.get("consultar_sri", False):
            resultado = self._consultar_sri(ruc)
            if resultado is not None:
                self._aplicar_datos_sri(resultado)
            else:
                self._exigir_manuales_si_falta_el_sri()
        elif ruc:
            # Sin consulta al SRI, esos datos hay que escribirlos a mano.
            # Solo se comprueban los de texto: ``obligado_contabilidad`` es
            # booleano y "False" es un valor válido, no un dato que falte.
            for campo in sri_datos.CAMPOS_AUTOMATICOS_TEXTO:
                if not (datos.get(campo) or "").strip() and not getattr(
                    self.instance, campo, None
                ):
                    self.add_error(
                        campo,
                        "Indique este dato o marque «Consultar los datos del RUC en el SRI».",
                    )

        # --- verificación real del certificado ----------------------------
        self._verificar_certificado(datos)
        return datos

    def _exigir_manuales_si_falta_el_sri(self) -> None:
        """Si el SRI no responde, exige a mano los datos que él habría dado."""
        mensaje = self._error_sri or "No se pudo consultar el SRI."
        faltan = [
            campo
            for campo in sri_datos.CAMPOS_AUTOMATICOS_TEXTO
            if not (self.cleaned_data.get(campo) or "").strip()
            and not getattr(self.instance, campo, None)
        ]
        if faltan:
            self.add_error(
                "ruc",
                f"{mensaje} Complete manualmente: {', '.join(faltan)}.",
            )
        else:
            # Hay datos suficientes: solo se avisa.
            self.avisos_sri.append(mensaje)

    # -------------------------------------------------------- certificado

    def _verificar_certificado(self, datos: dict) -> None:
        """Abre el .p12 con la contraseña y comprueba vigencia y titular."""
        clave_nueva = (datos.get("clave_certificado") or "").strip()
        archivo = datos.get("certificado")

        if not archivo and not self._clave_previa:
            self.add_error("certificado", "Debe adjuntar el archivo de firma (.p12 / .pfx).")
            return
        if not archivo:
            return  # se conserva el certificado ya guardado

        if not clave_nueva and not self._clave_previa:
            self.add_error(
                "clave_certificado", "Indique la contraseña del certificado."
            )
            return

        clave = clave_nueva
        if not clave and self._clave_previa:
            try:
                clave = self.instance.obtener_clave()
            except ErrorFacturacion as exc:
                self.add_error(
                    "clave_certificado",
                    f"No se pudo recuperar la contraseña guardada ({exc}). "
                    "Escriba la contraseña de nuevo.",
                )
                return

        # Se construye un candidato en memoria para validar antes de guardar.
        candidato = ConfiguracionEmisor(
            ruc=datos.get("ruc") or "",
            razon_social=datos.get("razon_social") or "",
            dir_matriz=datos.get("dir_matriz") or "",
            certificado=archivo,
        )
        try:
            candidato.establecer_clave(clave)
        except Exception as exc:  # noqa: BLE001 - falta la clave de cifrado, etc.
            raise ValidationError(
                f"No se pudo cifrar la contraseña: {exc}"
            ) from exc

        try:
            certificado = candidato.certificado_obj(validar_vigencia=False)
        except ErrorFacturacion as exc:
            self.add_error(
                "clave_certificado",
                f"No se pudo abrir el certificado: {exc} "
                "Compruebe que la contraseña sea la correcta.",
            )
            return

        if certificado.vencido():
            self.add_error(
                "certificado",
                "El certificado está vencido o aún no es válido. "
                "Renueve su firma electrónica.",
            )

        ruc_emisor = datos.get("ruc") or ""
        ruc_cert = extraer_ruc(certificado)
        if ruc_cert and ruc_emisor and ruc_cert != ruc_emisor:
            self.add_error(
                "certificado",
                f"El RUC del certificado ({ruc_cert}) no coincide con el RUC del emisor "
                f"({ruc_emisor}). El SRI rechaza los comprobantes firmados por otro "
                "contribuyente.",
            )

    def save(self, commit: bool = True) -> ConfiguracionEmisor:
        instancia: ConfiguracionEmisor = super().save(commit=False)
        clave_nueva = (self.cleaned_data.get("clave_certificado") or "").strip()
        if clave_nueva:
            instancia.establecer_clave(clave_nueva)
        if commit:
            instancia.save()
            self.save_m2m()
            conf.limpiar_cache()
        return instancia


class LineaDocumentoForm(forms.ModelForm):
    """Línea de un comprobante: basta con elegir el producto.

    El SRI exige la descripción, la cantidad, el precio unitario y el IVA en cada
    línea. Para no repetir lo que ya está en el catálogo, este formulario:

    * deja **opcionales** los campos que puede aportar el producto;
    * los **completa desde el producto** elegido, sin pisar lo que se haya escrito;
    * solo los exige cuando la línea va **sin** producto (línea suelta).
    """

    #: Campos que se toman del producto cuando la línea no los indica.
    DESDE_EL_PRODUCTO: Tuple[str, ...] = (
        "descripcion",
        "codigo_principal",
        "codigo_auxiliar",
        "unidad_medida",
        "precio_unitario",
        "codigo_porcentaje_iva",
    )

    #: Campos que, si quedan vacíos, se quedan con el valor por omisión del modelo.
    CON_VALOR_POR_OMISION: Tuple[str, ...] = (
        "cantidad",
        "descuento",
        "codigo_porcentaje_iva",
    )

    #: Campos que empiezan **vacíos** en una línea nueva.
    #:
    #: Así una línea sin tocar no trae un precio 0 ni un IVA que no le
    #: corresponda: o los aporta el producto, o los escribe quien factura.
    EN_BLANCO_AL_CREAR: Tuple[str, ...] = ("precio_unitario", "codigo_porcentaje_iva")

    class Meta:
        model = LineaDocumento
        fields = "__all__"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for nombre in (*self.DESDE_EL_PRODUCTO, *self.CON_VALOR_POR_OMISION):
            if nombre in self.fields:
                self.fields[nombre].required = False

        if "codigo_porcentaje_iva" in self.fields:
            # Se puede dejar sin indicar: entonces manda el IVA del producto.
            self.fields["codigo_porcentaje_iva"].choices = [
                ("", "— el del producto —"),
                *self.fields["codigo_porcentaje_iva"].choices,
            ]

        if self.instance.pk is None and not self.is_bound:
            for nombre in self.EN_BLANCO_AL_CREAR:
                if nombre in self.fields:
                    self.initial[nombre] = ""

    def clean(self) -> Dict[str, Any]:
        datos = super().clean()
        producto = datos.get("producto")

        # Lo que falte se toma del producto.
        if producto is not None:
            for nombre in self.DESDE_EL_PRODUCTO:
                if nombre not in self.fields:
                    continue
                if datos.get(nombre) in (None, ""):
                    datos[nombre] = getattr(producto, nombre)

        # Campos con valor por omisión: si vienen vacíos, se quedan con él.
        for nombre in self.CON_VALOR_POR_OMISION:
            if nombre in self.fields and datos.get(nombre) in (None, ""):
                campo = self._meta.model._meta.get_field(nombre)
                if campo.has_default():
                    datos[nombre] = campo.get_default()

        # Sin producto no hay de dónde tomarlos: hay que escribirlos.
        if not datos.get("descripcion"):
            self.add_error(
                "descripcion", "Indique la descripción o elija un producto del catálogo."
            )
        if datos.get("precio_unitario") in (None, ""):
            self.add_error(
                "precio_unitario", "Indique el precio unitario o elija un producto."
            )
        return datos
