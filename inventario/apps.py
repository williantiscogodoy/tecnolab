from django.apps import AppConfig
from django.db.models.signals import post_migrate


def crear_grupos(sender, **kwargs):
    """Crea el grupo 'Técnico' si todavía no existe (se ejecuta tras cada migrate)."""
    from django.contrib.auth.models import Group

    from .roles import GRUPO_TECNICO

    Group.objects.get_or_create(name=GRUPO_TECNICO)


class InventarioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventario"

    def ready(self):
        post_migrate.connect(crear_grupos, sender=self)