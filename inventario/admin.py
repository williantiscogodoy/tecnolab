from django.contrib import admin
from .models import Categoria, Producto, Movimiento


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    search_fields = ["nombre"]


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "sku", "categoria", "stock", "stock_minimo", "precio_venta", "activo"]
    list_filter = ["categoria", "activo"]
    search_fields = ["nombre", "sku"]


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ["producto", "tipo", "cantidad", "fecha", "usuario"]
    list_filter = ["tipo"]