from django.contrib import admin

from .models import (
    Categoria, Movimiento, Producto, ProductoImagen, Reparacion, ReparacionEquipo, ReparacionPieza,
)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ["nombre", "es_tecnica", "usa_compatibilidad"]
    list_filter = ["es_tecnica", "usa_compatibilidad"]
    search_fields = ["nombre"]


class ProductoImagenInline(admin.TabularInline):
    model = ProductoImagen
    extra = 0


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "sku", "categoria", "stock", "stock_minimo", "precio_venta", "activo"]
    list_filter = ["categoria", "activo"]
    search_fields = ["nombre", "sku"]
    inlines = [ProductoImagenInline]
    filter_horizontal = ["compatibles"]


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ["producto", "tipo", "cantidad", "fecha", "usuario"]
    list_filter = ["tipo"]


class ReparacionEquipoInline(admin.TabularInline):
    model = ReparacionEquipo
    extra = 0


class ReparacionPiezaInline(admin.TabularInline):
    model = ReparacionPieza
    extra = 0
    readonly_fields = ["movimiento"]


@admin.register(Reparacion)
class ReparacionAdmin(admin.ModelAdmin):
    list_display = ["codigo", "cliente", "equipo", "estado", "tecnico", "cobrado", "creado"]
    list_filter = ["estado", "tecnico"]
    search_fields = ["cliente", "equipo", "telefono_cliente"]
    inlines = [ReparacionEquipoInline, ReparacionPiezaInline]