/* Rellena la línea del comprobante con los datos del producto elegido.
 *
 * Es una comodidad: el formulario hace lo mismo en el servidor, así que si el
 * navegador no ejecuta este archivo, la línea se completa igual al guardar.
 */
(function () {
  "use strict";

  /** Ruta del endpoint que devuelve los datos de un producto. */
  function rutaDeDatos(select) {
    // El propio widget de autocompletado sabe dónde está el admin.
    var url = select.getAttribute("data-ajax--url") || "";
    var base = url.indexOf("/autocomplete/") > -1 ? url.split("/autocomplete/")[0] : "/admin";
    return base + "/sri_fe/producto/";
  }

  /** Anota el valor con el que nace el campo, para saber si alguien lo tocó. */
  function recordarValorInicial(entrada) {
    if (entrada.dataset.sriValorInicial === undefined) {
      entrada.dataset.sriValorInicial = entrada.value;
    }
  }

  /** ¿Se puede escribir en el campo sin pisar lo que escribió la persona? */
  function sePuedeEscribir(entrada) {
    return !entrada.value || entrada.value === entrada.dataset.sriValorInicial;
  }

  function rellenar(select) {
    var pk = select.value;
    if (!pk) {
      return;
    }
    var prefijo = select.name.replace(/-producto$/, "");
    fetch(rutaDeDatos(select) + pk + "/datos/", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (respuesta) {
        return respuesta.ok ? respuesta.json() : null;
      })
      .then(function (datos) {
        if (!datos) {
          return;
        }
        Object.keys(datos).forEach(function (campo) {
          var entrada = document.querySelector('[name="' + prefijo + "-" + campo + '"]');
          if (!entrada || !datos[campo]) {
            return;
          }
          recordarValorInicial(entrada);
          if (!sePuedeEscribir(entrada)) {
            return;   // hay algo escrito a mano: se respeta
          }
          entrada.value = datos[campo];
          // Lo puesto por el script se puede volver a sustituir al cambiar de producto.
          entrada.dataset.sriValorInicial = datos[campo];
        });
      })
      .catch(function () {
        /* Sin conexión: el servidor lo completa al guardar. */
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll('select[name$="-producto"]').forEach(function (select) {
      select.addEventListener("change", function () {
        rellenar(select);
      });
    });
  });
})();
