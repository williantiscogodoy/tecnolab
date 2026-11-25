"""Listas de reposición: pedir productos, ver qué se pidió y, al llegar, sumarlos al stock.

Quién puede qué:
- Administrador y técnico: crear listas, ver todas, pedir productos y cambiar o quitar los suyos.
  El administrador puede cambiar o quitar cualquiera.
- El técnico solo pide productos de categorías técnicas y no ve el precio de compra.
- Solo el administrador revisa la llegada y confirma (eso suma stock).
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Movimiento, Producto, Reposicion, ReposicionItem
from .roles import ADMIN, admin_o_tecnico, rol_de, solo_admin

MAX_CANTIDAD = 100000   # tope razonable por producto: evita errores de tecleo (un cero de más)
MAX_RESULTADOS = 30     # productos que muestra el buscador de la lista


# ------------------------------- utilidades -------------------------------
def _entero(valor, minimo=0):
    """Convierte a entero entre `minimo` y MAX_CANTIDAD; devuelve None si no sirve."""
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return n if minimo <= n <= MAX_CANTIDAD else None


def _es_admin(user):
    return rol_de(user) == ADMIN


def _productos_visibles(user):
    """Productos que ese usuario puede pedir: el técnico solo los de categorías técnicas."""
    productos = Producto.objects.filter(activo=True).select_related("categoria")
    if not _es_admin(user):
        productos = productos.filter(categoria__es_tecnica=True)
    return productos


def _sugerido(producto):
    """Cantidad sugerida: lo necesario para quedar con el doble del stock mínimo (al menos 1)."""
    return max(1, producto.stock_minimo * 2 - producto.stock)


def _puede_tocar(user, item):
    """El administrador toca cualquier producto de la lista; el técnico, solo los que él pidió."""
    return _es_admin(user) or item.pedido_por_id == user.id


def _volver(lista, **query):
    url = reverse("reposicion_detalle", args=[lista.pk])
    consulta = "&".join(f"{k}={v}" for k, v in query.items() if v)
    return redirect(f"{url}?{consulta}" if consulta else url)


def _lista_abierta_o_aviso(request, lista):
    """True si se puede seguir editando; si no, avisa y devuelve False."""
    if lista.abierta:
        return True
    messages.warning(request, f"La lista #{lista.pk} ya está {lista.get_estado_display().lower()}: no se puede modificar.")
    return False


# ------------------------------- listado -------------------------------
@admin_o_tecnico
def reposicion_lista(request):
    estado = request.GET.get("estado", "abiertas")
    filtros = {"abiertas": Reposicion.ABIERTA, "recibidas": Reposicion.RECIBIDA, "canceladas": Reposicion.CANCELADA}
    if estado not in filtros:
        estado = "abiertas"

    conteos = {e: 0 for e in filtros.values()}
    conteos.update({fila["estado"]: fila["n"] for fila in Reposicion.objects.values("estado").annotate(n=Count("pk"))})

    listas = (
        Reposicion.objects.filter(estado=filtros[estado])
        .select_related("creado_por")
        .annotate(n_productos=Count("items"), u_pedidas=Sum("items__cantidad_pedida"), u_recibidas=Sum("items__cantidad_recibida"))
        .order_by("-creado_en", "-pk")   # al agrupar se pierde el orden de Meta: sin esto, paginar puede repetir filas
    )
    pagina = Paginator(listas, 15).get_page(request.GET.get("page"))
    return render(request, "reposicion/lista.html", {
        "page_obj": pagina,
        "estado": estado,
        "n_abiertas": conteos[Reposicion.ABIERTA],
        "n_recibidas": conteos[Reposicion.RECIBIDA],
        "n_canceladas": conteos[Reposicion.CANCELADA],
        "es_admin": _es_admin(request.user),
    })


# ------------------------------- crear -------------------------------
@admin_o_tecnico
@require_POST
def reposicion_crear(request):
    """Crea una lista vacía o, con bajos=1, ya con los productos que están en su stock mínimo o por debajo."""
    with transaction.atomic():
        lista = Reposicion.objects.create(creado_por=request.user)
        agregados = 0
        if request.POST.get("bajos") == "1":
            bajos = _productos_visibles(request.user).filter(stock__lte=F("stock_minimo")).order_by("nombre")
            nuevos = [
                ReposicionItem(reposicion=lista, producto=p, cantidad_pedida=_sugerido(p), pedido_por=request.user)
                for p in bajos
            ]
            ReposicionItem.objects.bulk_create(nuevos)
            agregados = len(nuevos)

    if request.POST.get("bajos") == "1":
        if agregados:
            messages.success(request, f"Lista #{lista.pk} creada con {agregados} producto(s) con stock bajo. Ajusta las cantidades o quita los que no necesites.")
        else:
            messages.info(request, f"Lista #{lista.pk} creada. Ahora mismo no hay productos con stock bajo: busca los que quieras pedir.")
    else:
        messages.success(request, f"Lista #{lista.pk} creada. Busca y agrega los productos que quieras pedir.")
    return _volver(lista)


# ------------------------------- detalle -------------------------------
@admin_o_tecnico
def reposicion_detalle(request, pk):
    lista = get_object_or_404(Reposicion.objects.select_related("creado_por", "cerrado_por"), pk=pk)
    es_admin = _es_admin(request.user)

    items = list(lista.items.select_related("producto", "producto__categoria", "pedido_por"))
    for it in items:
        it.editable = lista.abierta and _puede_tocar(request.user, it)

    resultados, total_candidatos, q, ver = [], 0, "", "bajo"
    if lista.abierta:
        q = request.GET.get("q", "").strip()
        ver = "todos" if request.GET.get("ver") == "todos" else "bajo"
        candidatos = _productos_visibles(request.user).exclude(reposicion_items__reposicion=lista)
        if q:
            candidatos = candidatos.filter(Q(nombre__icontains=q) | Q(sku__icontains=q))
        elif ver == "bajo":
            candidatos = candidatos.filter(stock__lte=F("stock_minimo"))
        total_candidatos = candidatos.count()
        resultados = list(candidatos.order_by(F("stock") - F("stock_minimo"), "nombre")[:MAX_RESULTADOS])
        for p in resultados:
            p.sugerido = _sugerido(p)

    unidades = sum(it.cantidad_pedida for it in items)
    contexto = {
        "lista": lista,
        "items": items,
        "n_productos": len(items),
        "unidades": unidades,
        "recibidas": sum(it.cantidad_recibida or 0 for it in items),
        "costo_estimado": sum(it.cantidad_pedida * it.producto.precio_compra for it in items) if es_admin else None,
        "resultados": resultados,
        "total_candidatos": total_candidatos,
        "q": q,
        "ver": ver,
        "es_admin": es_admin,
        "puede_cancelar": lista.abierta and (es_admin or lista.creado_por_id == request.user.id),
    }
    return render(request, "reposicion/detalle.html", contexto)


# ------------------------------- agregar, cambiar y quitar productos -------------------------------
@admin_o_tecnico
@require_POST
def reposicion_agregar(request, pk):
    lista = get_object_or_404(Reposicion, pk=pk)
    q, ver = request.POST.get("q", ""), request.POST.get("ver", "")
    if not _lista_abierta_o_aviso(request, lista):
        return _volver(lista)

    producto = get_object_or_404(_productos_visibles(request.user), pk=_entero(request.POST.get("producto"), 1) or 0)
    cantidad = _entero(request.POST.get("cantidad"), 1)
    if cantidad is None:
        messages.error(request, f"Escribe una cantidad entre 1 y {MAX_CANTIDAD}.")
        return _volver(lista, q=q, ver=ver)

    existente = lista.items.filter(producto=producto).first()
    if existente is None:
        ReposicionItem.objects.create(reposicion=lista, producto=producto, cantidad_pedida=cantidad, pedido_por=request.user)
        messages.success(request, f"“{producto.nombre}” agregado: {cantidad} para traer.")
    elif _puede_tocar(request.user, existente):
        existente.cantidad_pedida = cantidad
        existente.save(update_fields=["cantidad_pedida"])
        messages.info(request, f"“{producto.nombre}” ya estaba en la lista: ahora son {cantidad}.")
    else:
        quien = existente.pedido_por.get_username() if existente.pedido_por else "otra persona"
        messages.info(request, f"“{producto.nombre}” ya está en la lista (lo pidió {quien}).")
    return _volver(lista, q=q, ver=ver)


@admin_o_tecnico
@require_POST
def reposicion_item_editar(request, pk, item_id):
    lista = get_object_or_404(Reposicion, pk=pk)
    item = get_object_or_404(ReposicionItem.objects.select_related("producto"), pk=item_id, reposicion=lista)
    if not _lista_abierta_o_aviso(request, lista):
        return _volver(lista)
    if not _puede_tocar(request.user, item):
        messages.error(request, "Solo puedes cambiar los productos que tú pediste.")
        return _volver(lista)
    cantidad = _entero(request.POST.get("cantidad"), 1)
    if cantidad is None:
        messages.error(request, f"Escribe una cantidad entre 1 y {MAX_CANTIDAD}.")
    else:
        item.cantidad_pedida = cantidad
        item.save(update_fields=["cantidad_pedida"])
        messages.success(request, f"“{item.producto.nombre}”: ahora {cantidad} para traer.")
    return _volver(lista)


@admin_o_tecnico
@require_POST
def reposicion_item_quitar(request, pk, item_id):
    lista = get_object_or_404(Reposicion, pk=pk)
    item = get_object_or_404(ReposicionItem.objects.select_related("producto"), pk=item_id, reposicion=lista)
    if not _lista_abierta_o_aviso(request, lista):
        return _volver(lista)
    if not _puede_tocar(request.user, item):
        messages.error(request, "Solo puedes quitar los productos que tú pediste.")
        return _volver(lista)
    nombre = item.producto.nombre
    item.delete()
    messages.success(request, f"“{nombre}” quitado de la lista.")
    return _volver(lista)


@admin_o_tecnico
@require_POST
def reposicion_cancelar(request, pk):
    with transaction.atomic():
        lista = get_object_or_404(Reposicion.objects.select_for_update(), pk=pk)
        if not _lista_abierta_o_aviso(request, lista):
            return _volver(lista)
        if not (_es_admin(request.user) or lista.creado_por_id == request.user.id):
            messages.error(request, "Solo puede cancelar la lista quien la creó o un administrador.")
            return _volver(lista)
        lista.estado = Reposicion.CANCELADA
        lista.cerrado_por = request.user
        lista.cerrado_en = timezone.now()
        lista.save(update_fields=["estado", "cerrado_por", "cerrado_en"])
    messages.success(request, f"Lista #{lista.pk} cancelada.")
    return redirect("reposicion_lista")


# ------------------------------- recibir y sumar al stock -------------------------------
def _filas_recepcion(items, post=None, errores=None):
    """Una fila por producto con lo que se muestra en el formulario de llegada."""
    filas = []
    for it in items:
        if post is None:
            llego, cantidad = True, str(it.cantidad_pedida)   # por defecto: llegó completo
        else:
            llego = post.get(f"llego_{it.pk}") == "on"
            cantidad = post.get(f"cant_{it.pk}", "")
        filas.append({"item": it, "llego": llego, "cantidad": cantidad, "error": (errores or {}).get(it.pk)})
    return filas


@solo_admin
def reposicion_recibir(request, pk):
    lista = get_object_or_404(Reposicion.objects.select_related("creado_por"), pk=pk)
    if not lista.abierta:
        messages.warning(request, f"La lista #{lista.pk} ya está {lista.get_estado_display().lower()}.")
        return _volver(lista)

    if request.method == "POST":
        with transaction.atomic():
            lista = Reposicion.objects.select_for_update().get(pk=pk)
            if not lista.abierta:   # alguien la cerró mientras tanto: así nunca se suma dos veces
                messages.warning(request, f"La lista #{lista.pk} ya fue {lista.get_estado_display().lower()}.")
                return _volver(lista)

            items = list(lista.items.select_related("producto"))
            enviados = {i for i in (_entero(x, 1) for x in request.POST.getlist("item")) if i}
            if enviados != {it.pk for it in items}:
                messages.error(request, "La lista cambió mientras la revisabas. Revísala de nuevo antes de confirmar.")
                return redirect("reposicion_recibir", pk=lista.pk)

            recibidas, errores = {}, {}
            for it in items:
                if request.POST.get(f"llego_{it.pk}") != "on":
                    recibidas[it.pk] = 0
                    continue
                n = _entero(request.POST.get(f"cant_{it.pk}"), 0)
                if n is None:
                    errores[it.pk] = f"Escribe un número entre 0 y {MAX_CANTIDAD}."
                else:
                    recibidas[it.pk] = n

            sin_llegadas = not errores and not any(recibidas.values())
            if errores or sin_llegadas:
                if errores:
                    messages.error(request, "Revisa las cantidades marcadas en rojo.")
                else:
                    messages.error(request, "No marcaste ningún producto como recibido. Si no llegó nada, cancela la lista.")
                return render(request, "reposicion/recibir.html", {
                    "lista": lista, "filas": _filas_recepcion(items, request.POST, errores),
                })

            # Productos bloqueados y releídos: el stock se suma sobre el valor real de ese momento
            productos = Producto.objects.select_for_update().in_bulk([it.producto_id for it in items])
            ahora = timezone.now()
            sumadas, faltaron = 0, []
            for it in items:
                cantidad = recibidas[it.pk]
                it.cantidad_recibida = cantidad
                it.save(update_fields=["cantidad_recibida"])
                if cantidad:
                    Movimiento.objects.create(
                        producto=productos[it.producto_id], tipo=Movimiento.ENTRADA, cantidad=cantidad,
                        usuario=request.user, nota=f"Reposición #{lista.pk}",
                    )
                    sumadas += cantidad
                if cantidad < it.cantidad_pedida:
                    faltaron.append(f"{it.producto.nombre} ({cantidad} de {it.cantidad_pedida})")

            lista.estado = Reposicion.RECIBIDA
            lista.cerrado_por = request.user
            lista.cerrado_en = ahora
            lista.save(update_fields=["estado", "cerrado_por", "cerrado_en"])

        con_stock = sum(1 for v in recibidas.values() if v)
        messages.success(request, f"Lista #{lista.pk} recibida: se sumaron {sumadas} unidad(es) de {con_stock} producto(s) al stock.")
        if faltaron:
            messages.warning(request, "Llegó menos de lo pedido: " + "; ".join(faltaron) + ".")
        return _volver(lista)

    items = list(lista.items.select_related("producto"))
    if not items:
        messages.warning(request, "La lista está vacía: agrega productos antes de recibirla.")
        return _volver(lista)
    return render(request, "reposicion/recibir.html", {"lista": lista, "filas": _filas_recepcion(items)})
