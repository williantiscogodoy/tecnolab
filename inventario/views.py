import json
from datetime import timedelta

from django.contrib.auth.decorators import user_passes_test
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.shortcuts import render
from django.utils import timezone

from .models import Categoria, Movimiento, Producto


def es_admin(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(es_admin, login_url="login")
def dashboard(request):
    productos = Producto.objects.filter(activo=True)

    valor_inventario = productos.aggregate(
        total=Sum(
            ExpressionWrapper(
                F("stock") * F("precio_compra"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
    )["total"] or 0

    bajo_stock = productos.filter(stock__lte=F("stock_minimo")).order_by("stock")

    # Gráfica 1: unidades en stock por categoría
    por_categoria = Categoria.objects.annotate(total=Sum("producto__stock")).order_by("-total")
    cat_labels = [c.nombre for c in por_categoria]
    cat_data = [c.total or 0 for c in por_categoria]

    # Gráfica 2: entradas y salidas de los últimos 7 días
    hoy = timezone.localdate()
    dias = [hoy - timedelta(days=i) for i in range(6, -1, -1)]
    movs = (
        Movimiento.objects.filter(fecha__date__gte=dias[0])
        .values("fecha__date", "tipo")
        .annotate(total=Sum("cantidad"))
    )
    tabla = {(m["fecha__date"], m["tipo"]): m["total"] for m in movs}

    contexto = {
        "total_productos": productos.count(),
        "total_unidades": productos.aggregate(t=Sum("stock"))["t"] or 0,
        "valor_inventario": valor_inventario,
        "bajo_stock": bajo_stock[:8],
        "total_bajo_stock": bajo_stock.count(),
        "cat_labels": cat_labels,
        "cat_data": cat_data,
        "dias_labels": [d.strftime("%d/%m") for d in dias],
        "entradas": [tabla.get((d, "ENTRADA"), 0) for d in dias],
        "salidas": [tabla.get((d, "SALIDA"), 0) for d in dias],
        "ultimos_movimientos": Movimiento.objects.select_related("producto", "usuario")[:6],
    }
    return render(request, "dashboard.html", contexto)