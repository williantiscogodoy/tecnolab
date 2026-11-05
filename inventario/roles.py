from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

GRUPO_TECNICO = "Técnico"
ADMIN = "admin"
TECNICO = "tecnico"


def rol_de(user):
    """Administrador = usuario 'staff'. Técnico = usuario del grupo 'Técnico'."""
    if not user.is_authenticated:
        return None
    if user.is_staff:
        return ADMIN
    if user.groups.filter(name=GRUPO_TECNICO).exists():
        return TECNICO
    return None


def rol_requerido(*roles):
    """Permite la vista solo a los roles indicados.

    - Sin sesión: manda al login.
    - Técnico donde no corresponde: lo devuelve a su panel (evita bucles de redirección).
    - Usuario sin rol: 403.
    """
    def decorador(vista):
        @wraps(vista)
        def envuelta(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), "login")
            rol = rol_de(request.user)
            if rol in roles:
                return vista(request, *args, **kwargs)
            if rol == TECNICO:
                messages.warning(request, "Esa sección es solo para administradores.")
                return redirect("tecnico_panel")
            raise PermissionDenied
        return envuelta
    return decorador


solo_admin = rol_requerido(ADMIN)
admin_o_tecnico = rol_requerido(ADMIN, TECNICO)