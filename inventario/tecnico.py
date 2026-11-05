from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Categoria, Movimiento, Producto
from .roles import ADMIN, admin_o_tecnico, rol_de


def _url(**params):
    """Arma un '?a=1&b=2' ignorando los valores vacíos."""
    limpios = {k: v for k, v in params.items() if v not in ("", None, False)}
    return "?" + urlencode(limpios) if limpios else "?"


@admin_o_tecnico
def tecnico_panel(request):
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("categoria", "")
    cat_id = int(cat) if cat.isdigit() else None
    solo_bajo = request.GET.get("bajo") == "1"
    ver_todo = request.GET.get("todo") == "1"
    bajo_val = "1" if solo_bajo else None
    todo_val = "1" if ver_todo else None

    # Resumen de las piezas técnicas (las que el técnico más necesita vigilar)
    tecnicas = Producto.objects.filter(activo=True, categoria__es_tecnica=True)
    resumen = tecnicas.aggregate(
        total=Count("pk"),
        agotadas=Count("pk", filter=Q(stock=0)),
        bajas=Count("pk", filter=Q(stock__gt=0, stock__lte=F("stock_minimo"))),
    )

    # Búsqueda: cada palabra debe aparecer en el nombre, SKU, descripción o categoría
    productos = Producto.objects.filter(activo=True).select_related("categoria")
    for palabra in q.split():
        productos = productos.filter(
            Q(nombre__icontains=palabra)
            | Q(sku__icontains=palabra)
            | Q(descripcion__icontains=palabra)
            | Q(categoria__nombre__icontains=palabra)
        )

    if cat_id:
        productos = productos.filter(categoria_id=cat_id)
    elif not (q or ver_todo):
        # Sin búsqueda, el técnico ve solo lo suyo
        productos = productos.filter(categoria__es_tecnica=True)

    if solo_bajo:
        productos = productos.filter(stock__lte=F("stock_minimo"))

    # PRIORIDAD: primero las categorías técnicas, después el resto
    productos = productos.order_by("-categoria__es_tecnica", "categoria__nombre", "nombre")
    page_obj = Paginator(productos, 24).get_page(request.GET.get("page"))

    # Filtros rápidos por categoría técnica
    categorias = Categoria.objects.filter(es_tecnica=True).annotate(
        n=Count("producto", filter=Q(producto__activo=True))
    ).order_by("nombre")
    chips = [{
        "nombre": "Todo lo técnico",
        "url": _url(q=q, bajo=bajo_val),
        "activo": cat_id is None,
    }] + [{
        "nombre": c.nombre,
        "n": c.n,
        "url": _url(q=q, categoria=c.pk, bajo=bajo_val),
        "activo": cat_id == c.pk,
    } for c in categorias]

    params = request.GET.copy()
    params.pop("page", None)

    retiros = (
        Movimiento.objects.filter(usuario=request.user, tipo=Movimiento.SALIDA)
        .select_related("producto")
        .order_by("-fecha", "-pk")[:6]
    )

    return render(request, "tecnico/panel.html", {
        "page_obj": page_obj,
        "pag_anterior": max(page_obj.number - 1, 1),
        "pag_siguiente": min(page_obj.number + 1, page_obj.paginator.num_pages),
        "qs": params.urlencode(),
        "q": q,
        "chips": chips,
        "resumen": resumen,
        "solo_bajo": solo_bajo,
        "ver_todo": ver_todo,
        "hay_categorias": bool(chips[1:]),
        "url_bajo": _url(q=q, categoria=cat_id, bajo=None if solo_bajo else "1", todo=todo_val),
        "url_todo": _url(q=q, categoria=cat_id, bajo=bajo_val, todo=None if ver_todo else "1"),
        "url_limpiar": reverse("tecnico_panel"),
        "retiros": retiros,
    })


@admin_o_tecnico
@require_POST
def tecnico_retirar(request, pk):
    """El técnico retira piezas del inventario (se registra como una salida)."""
    destino = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        destino = reverse("tecnico_panel")

    try:
        cantidad = int(request.POST.get("cantidad", ""))
    except ValueError:
        cantidad = 0
    nota = " ".join(request.POST.get("nota", "").split())[:180]

    if cantidad < 1:
        messages.error(request, "Indica una cantidad de al menos 1.")
        return redirect(destino)

    with transaction.atomic():
        # Bloquea el producto para que dos retiros a la vez no dejen el stock en negativo
        producto = get_object_or_404(Producto.objects.select_for_update(), pk=pk, activo=True)

        if rol_de(request.user) != ADMIN and not producto.categoria.es_tecnica:
            messages.error(request, "Solo puedes retirar productos de categorías técnicas.")
        elif cantidad > producto.stock:
            messages.error(request, f"Solo hay {producto.stock} unidad(es) de “{producto.nombre}”.")
        else:
            Movimiento.objects.create(
                producto=producto,
                tipo=Movimiento.SALIDA,
                cantidad=cantidad,
                usuario=request.user,
                nota=f"Retiro técnico: {nota}" if nota else "Retiro técnico",
            )
            messages.success(
                request,
                f"Retiraste {cantidad} unidad(es) de “{producto.nombre}”. Quedan {producto.stock}.",
            )
    return redirect(destino)