"""Sección Usuarios > Personal: cuentas con las que se inicia sesión (técnicos y administradores)."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .roles import ADMIN, GRUPO_TECNICO, TECNICO, rol_de, solo_admin

User = get_user_model()


def _puede_gestionar(quien, objetivo):
    """Solo un superadministrador puede tocar la cuenta de otro superadministrador."""
    return quien.is_superuser or not objetivo.is_superuser


def _etiqueta_rol(u):
    if u.is_superuser:
        return "Superadministrador"
    return {"admin": "Administrador", "tecnico": "Técnico"}.get(rol_de(u), "Sin rol")


@solo_admin
def personal_lista(request):
    usuarios = list(User.objects.order_by("-is_active", "username"))
    for u in usuarios:
        u.rol_texto = _etiqueta_rol(u)
        u.es_yo = u.pk == request.user.pk
        u.editable = _puede_gestionar(request.user, u)
    return render(request, "usuarios/personal.html", {"tab": "personal", "usuarios": usuarios})


class UsuarioNuevoForm(forms.Form):
    username = forms.CharField(max_length=150)
    nombre = forms.CharField(max_length=60, required=False)
    password = forms.CharField(strip=False)
    rol = forms.ChoiceField(choices=[(TECNICO, "Técnico"), (ADMIN, "Administrador")])

    def clean_username(self):
        nombre = self.cleaned_data["username"].strip()
        if not nombre or " " in nombre:
            raise forms.ValidationError("El usuario no puede tener espacios.")
        if User.objects.filter(username__iexact=nombre).exists():
            raise forms.ValidationError("Ese usuario ya existe.")
        return nombre

    def clean(self):
        datos = super().clean()
        if datos.get("password") and datos.get("username"):
            try:
                validate_password(datos["password"], User(username=datos["username"]))
            except ValidationError as e:
                self.add_error("password", e)
        return datos


@solo_admin
@require_POST
def personal_crear(request):
    form = UsuarioNuevoForm(request.POST)
    if not form.is_valid():
        campo, errores = next(iter(form.errors.items()))
        messages.error(request, errores[0])
        return redirect("personal_lista")
    d = form.cleaned_data
    with transaction.atomic():
        u = User(username=d["username"], first_name=d["nombre"].strip(), is_active=True, is_staff=d["rol"] == ADMIN)
        u.set_password(d["password"])
        u.save()
        if d["rol"] == TECNICO:
            grupo, _ = Group.objects.get_or_create(name=GRUPO_TECNICO)
            u.groups.add(grupo)
    messages.success(request, f"Usuario “{u.username}” creado como {'administrador' if u.is_staff else 'técnico'}.")
    return redirect("personal_lista")


@solo_admin
@require_POST
def personal_estado(request, pk):
    """Activa o desactiva una cuenta (no se borra, así se conserva su historial)."""
    u = get_object_or_404(User, pk=pk)
    if u.pk == request.user.pk:
        messages.error(request, "No puedes desactivar tu propia cuenta.")
    elif not _puede_gestionar(request.user, u):
        messages.error(request, "No puedes modificar esa cuenta.")
    else:
        u.is_active = not u.is_active
        u.save(update_fields=["is_active"])
        messages.success(request, f"Usuario “{u.username}” {'activado' if u.is_active else 'desactivado'}.")
    return redirect("personal_lista")


@solo_admin
@require_POST
def personal_clave(request, pk):
    u = get_object_or_404(User, pk=pk)
    if not _puede_gestionar(request.user, u):
        messages.error(request, "No puedes modificar esa cuenta.")
        return redirect("personal_lista")
    clave = request.POST.get("password", "")
    try:
        validate_password(clave, u)
    except ValidationError as e:
        messages.error(request, e.messages[0])
        return redirect("personal_lista")
    u.set_password(clave)
    u.save(update_fields=["password"])
    if u.pk == request.user.pk:
        update_session_auth_hash(request, u)  # mantiene la sesión abierta
    messages.success(request, f"Se cambió la contraseña de “{u.username}”.")
    return redirect("personal_lista")
