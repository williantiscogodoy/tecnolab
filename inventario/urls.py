from django.contrib.auth import views as auth_views
from django.urls import path

from . import reparaciones, tecnico, views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("inicio/", views.inicio, name="inicio"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="login.html", redirect_authenticated_user=True
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Productos
    path("productos/", views.producto_lista, name="producto_lista"),
    path("productos/nuevo/", views.producto_crear, name="producto_crear"),
    path("productos/<int:pk>/editar/", views.producto_editar, name="producto_editar"),
    path("productos/<int:pk>/eliminar/", views.producto_eliminar, name="producto_eliminar"),

    # Categorías
    path("categorias/", views.categoria_lista, name="categoria_lista"),
    path("categorias/guardar/", views.categoria_guardar, name="categoria_crear"),
    path("categorias/<int:pk>/guardar/", views.categoria_guardar, name="categoria_editar"),
    path("categorias/<int:pk>/eliminar/", views.categoria_eliminar, name="categoria_eliminar"),

    # Panel técnico
    path("tecnico/", tecnico.tecnico_panel, name="tecnico_panel"),
    path("tecnico/retirar/<int:pk>/", tecnico.tecnico_retirar, name="tecnico_retirar"),
    path("tecnico/producto/<int:pk>/", tecnico.tecnico_producto, name="tecnico_producto"),

    # Reparaciones
    path("reparaciones/", reparaciones.reparacion_lista, name="reparacion_lista"),
    path("reparaciones/nueva/", reparaciones.reparacion_crear, name="reparacion_crear"),
    path("reparaciones/<int:pk>/", reparaciones.reparacion_detalle, name="reparacion_detalle"),
    path("reparaciones/<int:pk>/actualizar/", reparaciones.reparacion_actualizar, name="reparacion_actualizar"),
    path("reparaciones/<int:pk>/pieza/", reparaciones.reparacion_pieza, name="reparacion_pieza"),

    # Movimientos
    path("movimientos/", views.movimiento_lista, name="movimiento_lista"),
    path("movimientos/nuevo/", views.movimiento_crear, name="movimiento_crear"),
]