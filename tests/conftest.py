"""Fixtures compartidas por la batería de pruebas."""

from __future__ import annotations

import os
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Optional

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from factec.modelos import (
    Detalle,
    DocSustento,
    Emisor,
    Impuesto,
    ImpuestoDocSustento,
    ImpuestoRetencion,
    PagoRetencion,
    Receptor,
)
from factec.catalogos import TarifaIva, TipoIdentificacion


#: Directorio con los XSD oficiales del SRI (ver README para obtenerlos).
XSD_DIR = Path(os.environ.get("SRI_XSD_DIR", "/tmp/sri_xsd"))

CLAVE_CERTIFICADO = "clave-de-pruebas"


@pytest.fixture(scope="session")
def ruta_certificado(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Genera un ``.p12`` autofirmado de pruebas (nunca se versiona)."""
    destino = tmp_path_factory.mktemp("certs") / "firmante_pruebas.p12"
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "EC"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EMPRESA DE PRUEBAS S.A."),
            x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA DE PRUEBAS S.A."),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, "1790012345001"),
        ]
    )
    certificado = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_utcnow() - timedelta(days=1))
        .not_valid_after(_utcnow() + timedelta(days=365))
        .sign(clave, hashes.SHA256())
    )
    datos = pkcs12.serialize_key_and_certificates(
        b"pruebas",
        clave,
        certificado,
        None,
        serialization.BestAvailableEncryption(CLAVE_CERTIFICADO.encode()),
    )
    destino.write_bytes(datos)
    return destino


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


@pytest.fixture(scope="session")
def certificado(ruta_certificado: Path):
    from factec.firma import Certificado

    return Certificado.desde_archivo(ruta_certificado, CLAVE_CERTIFICADO)


@pytest.fixture
def emisor() -> Emisor:
    return Emisor(
        ruc="1790012345001",
        razon_social="EMPRESA DE PRUEBAS S.A.",
        nombre_comercial="PRUEBAS",
        dir_matriz="Av. Amazonas 123, Quito",
        dir_establecimiento="Av. Amazonas 123, Quito",
        estab="001",
        pto_emi="001",
        obligado_contabilidad=True,
    )


@pytest.fixture
def receptor() -> Receptor:
    return Receptor(
        razon_social="CLIENTE DE PRUEBAS",
        identificacion="0703886697001",
        tipo_identificacion=TipoIdentificacion.RUC,
        direccion="Guayaquil",
    )


@pytest.fixture
def detalle() -> Detalle:
    return Detalle(
        descripcion="Servicio de pruebas",
        cantidad=1,
        precio_unitario=Decimal("100.00"),
        codigo_principal="SRV001",
        impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
    )


@pytest.fixture
def doc_sustento() -> DocSustento:
    return DocSustento(
        cod_sustento="01",
        cod_doc_sustento="01",
        num_doc_sustento="001001000000001",
        fecha_emision=date(2026, 9, 15),
        total_sin_impuestos=Decimal("100.00"),
        importe_total=Decimal("115.00"),
        impuestos=[
            ImpuestoDocSustento(
                codigo="2",
                codigo_porcentaje="4",
                base_imponible=Decimal("100.00"),
                tarifa=Decimal("15.00"),
                valor=Decimal("15.00"),
            )
        ],
        retenciones=[
            ImpuestoRetencion(
                codigo="1",
                codigo_retencion="312",
                base_imponible=Decimal("100.00"),
                porcentaje_retener=Decimal("1.75"),
                valor_retenido=Decimal("1.75"),
            )
        ],
        pagos=[PagoRetencion(forma_pago="01", total=Decimal("115.00"))],
    )


def ruta_xsd(nombre: str) -> Optional[Path]:
    """Devuelve la ruta de un XSD oficial, o ``None`` si no está disponible."""
    candidato = XSD_DIR / nombre
    return candidato if candidato.exists() else None


@pytest.fixture(scope="session")
def esquemas():
    """Compilador perezoso de XSD; las pruebas se saltan si no hay esquemas."""
    from lxml import etree

    cache = {}

    def _obtener(nombre: str):
        if nombre not in cache:
            ruta = ruta_xsd(nombre)
            if ruta is None:
                pytest.skip(
                    f"No se encontró {nombre} en {XSD_DIR}. "
                    "Defina SRI_XSD_DIR con el directorio de los XSD del SRI."
                )
            cache[nombre] = etree.XMLSchema(etree.parse(str(ruta)))
        return cache[nombre]

    return _obtener


def _configuracion_emisor(**campos: Any):
    """Crea un ``ConfiguracionEmisor`` sin guardarlo (import diferido)."""
    from factec.django.models import ConfiguracionEmisor

    return ConfiguracionEmisor(**campos)

_TEMPORAL = tempfile.mkdtemp(prefix="sri_fe_tests_")

RUC = "0703886697001"


def _configurar_django() -> None:
    """Configura un proyecto Django mínimo (una sola vez)."""
    from django.conf import settings

    if settings.configured:
        return
    settings.configure(
        DEBUG=False,
        SECRET_KEY="clave-solo-para-pruebas",
        ALLOWED_HOSTS=["testserver", "localhost"],
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.messages",
            "django.contrib.sessions",
            "django.contrib.admin",
            "factec.django",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.path.join(_TEMPORAL, "db.sqlite3"),
            }
        },
        MEDIA_ROOT=os.path.join(_TEMPORAL, "media"),
        MEDIA_URL="/media/",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ]
                },
            }
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        LOGGING_CONFIG=None,
    )


@pytest.fixture(scope="session")
def entorno_django():
    """Configura Django, carga las apps, aplica migraciones y monta el admin."""
    import django

    _configurar_django()
    django.setup()

    from django.conf import settings
    from django.contrib import admin as admin_django
    from django.urls import path

    modulo = types.ModuleType("sri_urls_de_prueba")
    modulo.urlpatterns = [path("admin/", admin_django.site.urls)]
    sys.modules["sri_urls_de_prueba"] = modulo
    settings.ROOT_URLCONF = "sri_urls_de_prueba"

    from django.core.management import call_command

    call_command("migrate", verbosity=0, interactive=False)
    yield


@pytest.fixture
def clave_cifrado(monkeypatch) -> str:
    """Define una clave de cifrado nueva para cada prueba."""
    from factec.django import conf
    from factec.django.crypto import generar_clave

    monkeypatch.setenv("SRI_CLAVE_CIFRADO", generar_clave())
    conf.limpiar_cache()
    yield
    conf.limpiar_cache()


@pytest.fixture
def limpiar_tablas(entorno_django):
    """Deja las tablas vacías antes y después de cada prueba."""
    from factec.django import models

    def _vaciar():
        models.ComprobanteEmitido.objects.all().delete()
        models.Secuencial.objects.all().delete()
        models.ConfiguracionEmisor.objects.all().delete()

    _vaciar()
    yield
    _vaciar()


def _datos_sri(**cambios: Any) -> Any:
    """Datos de contribuyente prefabricados para las pruebas del SRI."""
    from factec.sri.consulta_ruc import DatosRuc

    base = dict(
        ruc=RUC, razon_social="URDIN GONZALEZ JOHNNY EDGAR", estado="ACTIVO",
        tipo_contribuyente="PERSONA NATURAL", regimen="RIMPE",
        categoria="NEGOCIO POPULAR", obligado_contabilidad=False,
        agente_retencion=False, contribuyente_especial=False, encontrado=True,
    )
    base.update(cambios)
    return DatosRuc(**base)


def _crear_p12(ruc: str, clave: str = CLAVE_CERTIFICADO) -> bytes:
    """Genera un ``.p12`` autofirmado en memoria."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "EC"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, ruc),
            x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA DE PRUEBAS S.A."),
        ]
    )
    certificado = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .sign(llave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"pruebas",
        llave,
        certificado,
        None,
        serialization.BestAvailableEncryption(clave.encode()),
    )


def _datos_formulario(**cambios: Any) -> dict:
    datos = {
        "nombre": "Principal",
        "activo": True,
        "ambiente": 1,
        "ruc": RUC,
        "razon_social": "URDIN GONZALEZ JOHNNY EDGAR",
        "nombre_comercial": "ORVIQUE",
        "dir_matriz": "PANAMERICANA Y CARCHI",
        "dir_establecimiento": "",
        "estab": "001",
        "pto_emi": "001",
        "obligado_contabilidad": False,
        "contribuyente_especial": "",
        "agente_retencion": "",
        "regimen": "RIMPE",
        "categoria": "NEGOCIO POPULAR",
        "clave_certificado": CLAVE_CERTIFICADO,
    }
    datos.update(cambios)
    return datos


def _formulario(datos_p12: Any = None, **cambios: Any):
    """Construye el formulario del admin.

    Por omisión no consulta el SRI (``consultar_sri=False``) para que las pruebas
    no dependan de la red; las que lo prueban lo activan explícitamente.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from factec.django.forms import ConfiguracionEmisorForm

    if datos_p12 is None:
        datos_p12 = _crear_p12(RUC)
    cambios.setdefault("consultar_sri", False)
    archivos = (
        {}
        if datos_p12 is False
        else {"certificado": SimpleUploadedFile("firma.p12", datos_p12)}
    )
    return ConfiguracionEmisorForm(data=_datos_formulario(**cambios), files=archivos)


@pytest.fixture
def certificado_p12() -> bytes:
    return _crear_p12(RUC)


@pytest.fixture
def configuracion(limpiar_tablas, clave_cifrado, certificado_p12):
    """Crea la configuración del emisor con el formulario del admin."""
    from factec.django import conf

    formulario = _formulario(certificado_p12)
    assert formulario.is_valid(), dict(formulario.errors)
    instancia = formulario.save()
    conf.limpiar_cache()
    return instancia


class ClienteFalso:
    """Cliente SOAP falso para no tocar la red."""

    def __init__(
        self, estado_recepcion: str = "RECIBIDA", estado_autorizacion: str = "AUTORIZADO"
    ) -> None:
        self.estado_recepcion = estado_recepcion
        self.estado_autorizacion = estado_autorizacion
        self.llamadas: List[str] = []

    def validar_comprobante(self, xml: Any):
        from factec.sri.soap import Mensaje, RespuestaRecepcion

        self.llamadas.append("recepcion")
        if self.estado_recepcion == "RECIBIDA":
            return RespuestaRecepcion(estado="RECIBIDA", clave_acceso=RUC, mensajes=[])
        return RespuestaRecepcion(
            estado="DEVUELTA",
            clave_acceso="N/A",
            mensajes=[
                Mensaje(identificador="35", mensaje="ARCHIVO NO CUMPLE ESTRUCTURA XML")
            ],
        )

    def esperar_autorizacion(self, clave: str, intentos: int = 5, espera: float = 3.0):
        from factec.sri.soap import Autorizacion, RespuestaAutorizacion

        self.llamadas.append("autorizacion")
        if self.estado_autorizacion == "AUTORIZADO":
            autorizacion = Autorizacion(
                estado="AUTORIZADO",
                numero_autorizacion=clave,
                fecha_autorizacion=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
                ambiente="PRUEBAS",
                comprobante="<factura id='comprobante'/>",
            )
        else:
            autorizacion = Autorizacion(estado=self.estado_autorizacion)
        return RespuestaAutorizacion(
            clave_acceso_consultada=clave, numero_comprobantes=1, autorizaciones=[autorizacion]
        )


@pytest.fixture
def cliente_falso(monkeypatch, configuracion):
    from factec.django import conf

    cliente = ClienteFalso()
    monkeypatch.setattr(conf, "cliente", lambda: cliente)
    return cliente


def _detalle():
    from factec.catalogos import TarifaIva
    from factec.modelos import Detalle, Impuesto

    return Detalle(
        descripcion="Servicio de prueba",
        cantidad=Decimal("1"),
        precio_unitario=Decimal("10.00"),
        impuestos=[Impuesto(codigo_porcentaje=TarifaIva.IVA_15)],
    )


def _receptor():
    from factec.catalogos import TipoIdentificacion
    from factec.modelos import Receptor

    return Receptor(
        razon_social="CONSUMIDOR FINAL",
        identificacion="9999999999999",
        tipo_identificacion=TipoIdentificacion.CONSUMIDOR_FINAL,
        direccion="QUITO",
    )



def _superusuario():
    from django.contrib.auth import get_user_model

    usuario, _ = get_user_model().objects.get_or_create(
        username="admin", defaults={"email": "a@b.com", "is_staff": True, "is_superuser": True}
    )
    usuario.is_staff = True
    usuario.is_superuser = True
    usuario.set_password("clave")
    usuario.save()
    return usuario


#: Fecha de referencia de los datos de prueba escritos a mano (ver abajo).
FECHA_DE_LAS_PRUEBAS = date(2026, 10, 9)


@pytest.fixture(autouse=True)
def fecha_de_referencia(monkeypatch):
    """Fija el «hoy» del SRI para que las pruebas no caduquen.

    Muchas pruebas usan fechas escritas a mano (2026-09-15, 2026-10-08…) y el
    paquete rechaza las emisiones de más de 90 días: sin esto, la suite empezaría
    a fallar sola con el paso del tiempo. La referencia es la fecha real (nunca
    anterior a los datos de prueba) y la tolerancia se amplía.

    Las pruebas que comprueban la ventana pasan ``hoy`` explícitamente.
    """
    from factec.sri import fechas

    real = fechas.hoy_en_ecuador
    monkeypatch.setattr(
        fechas,
        "hoy_en_ecuador",
        lambda momento=None: max(real(momento), FECHA_DE_LAS_PRUEBAS),
    )
    monkeypatch.setattr(fechas, "DIAS_TOLERANCIA", 365 * 100)
