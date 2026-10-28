"""Solo una configuración activa: el texto del admin lo dice así.

El comportamiento cambió con la 1.12.4 (activar una apaga las demás); esto es
solo el texto de ayuda que se lee en el admin.

Generada por Django 6.1.2 el 2026-10-28.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sri_fe', '0015_ajustes_del_sri'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracionemisor',
            name='activo',
            field=models.BooleanField(default=True, help_text='Solo hay una activa: al activar esta se apagan las demás. Es la que firma y la que decide si se emite en pruebas o en producción.', verbose_name='activa'),
        ),
    ]
