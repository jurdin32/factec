"""La tabla de ajustes de la emisión.

Los ajustes del paquete viven en su propia tabla (``sri_fe_ajustedelsri``), no en
la del proyecto: así no depende de otro paquete para guardar su configuración. Los
valores de serie los deja la señal ``post_migrate`` (ver ``factec.django.ajustes``).
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sri_fe", "0014_ejemplo_del_codigo_de_retencion")]

    operations = [
        migrations.CreateModel(
            name="AjusteDelSri",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "clave",
                    models.SlugField(
                        help_text="Con lo que se lee en el código: sri_ambiente…",
                        max_length=60,
                        unique=True,
                        verbose_name="clave",
                    ),
                ),
                ("valor", models.TextField(blank=True, verbose_name="valor")),
                (
                    "tipo",
                    models.CharField(
                        choices=[
                            ("texto", "Texto"),
                            ("entero", "Número entero"),
                            ("booleano", "Sí o no"),
                            ("lista", "Lista separada por comas"),
                        ],
                        default="texto",
                        max_length=10,
                        verbose_name="tipo",
                    ),
                ),
                ("grupo", models.CharField(blank=True, default="Emisión", max_length=40, verbose_name="grupo")),
                (
                    "descripcion",
                    models.CharField(
                        blank=True,
                        help_text="Para qué es este ajuste.",
                        max_length=220,
                        verbose_name="descripción",
                    ),
                ),
                ("orden", models.PositiveIntegerField(default=0, verbose_name="orden")),
                (
                    "editable",
                    models.BooleanField(
                        default=True,
                        help_text="Los que no lo son sólo los pone el paquete.",
                        verbose_name="editable",
                    ),
                ),
                ("actualizado", models.DateTimeField(auto_now=True, verbose_name="actualizado")),
            ],
            options={
                "verbose_name": "ajuste del SRI",
                "verbose_name_plural": "ajustes del SRI",
                "ordering": ["grupo", "orden", "clave"],
            },
        ),
    ]
