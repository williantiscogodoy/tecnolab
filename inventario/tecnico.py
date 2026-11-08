from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import floatformat
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.timezone import localtime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from decimal import Decimal, InvalidOperation

from .clientes import leer_monto
from .models import CajaMovimiento, Categoria, Cliente, Movimiento, Producto, Reparacion, Venta
from .roles import ADMIN, admin_o_tecnico, rol_de
from .utils import url_params


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
    productos = Producto.objects.filter(activo=True).select_related("categoria").prefetch_related("imagenes")
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
        "url": url_params(q=q, bajo=bajo_val),
        "activo": cat_id is None,
    }] + [{
        "nombre": c.nombre,
        "n": c.n,
        "url": url_params(q=q, categoria=c.pk, bajo=bajo_val),
        "activo": cat_id == c.pk,
    } for c in categorias]

    params = request.GET.copy()
    params.pop("page", None)
    params.pop("partial", None)

    ctx = {
        "page_obj": page_obj,
        "pag_anterior": max(page_obj.number - 1, 1),
        "pag_siguiente": min(page_obj.number + 1, page_obj.paginator.num_pages),
        "qs": params.urlencode(),
        "q": q,
        "chips": chips,
        "solo_bajo": solo_bajo,
        "ver_todo": ver_todo,
        "hay_categorias": bool(chips[1:]),
        "url_bajo": url_params(q=q, categoria=cat_id, bajo=None if solo_bajo else "1", todo=todo_val),
        "url_todo": url_params(q=q, categoria=cat_id, bajo=bajo_val, todo=None if ver_todo else "1"),
        "url_limpiar": reverse("tecnico_panel"),
    }

    # Búsqueda en vivo: el navegador pide solo los trozos que cambian
    if request.GET.get("partial"):
        return JsonResponse({
            "filtros": render_to_string("tecnico/_filtros.html", ctx, request),
            "resultados": render_to_string("tecnico/_resultados.html", ctx, request),
        })

    # Lo que solo se necesita en la carga completa de la página
    mias = Reparacion.objects.filter(tecnico=request.user)
    abiertas = mias.filter(estado__in=[Reparacion.EN_PROCESO, Reparacion.LISTO])
    ctx.update({
        "resumen": resumen,
        "n_abiertas": abiertas.count(),
        "reparaciones_abiertas": abiertas.select_related("tecnico").prefetch_related("piezas__producto")[:5],
        "retiros": (
            Movimiento.objects.filter(usuario=request.user, tipo=Movimiento.SALIDA)
            .select_related("producto")
            .order_by("-fecha", "-pk")[:6]
        ),
    })
    return render(request, "tecnico/panel.html", ctx)


def _item_producto(p):
    """Datos mínimos de un producto para listas dentro del cuadro de detalle."""
    return {
        "id": p.pk,
        "nombre": p.nombre,
        "sku": p.sku or "",
        "categoria": p.categoria.nombre,
        "stock": p.stock,
        "stock_minimo": p.stock_minimo,
        "imagen": p.portada_url or "",
        "detalle_url": reverse("tecnico_producto", args=[p.pk]),
    }


def _lista_compatibles(p):
    """Productos activos compatibles con p (la relación es mutua)."""
    qs = (
        p.compatibles.filter(activo=True)
        .select_related("categoria").prefetch_related("imagenes")
        .order_by("categoria__nombre", "nombre")
    )
    return [
        dict(_item_producto(c), quitar_url=reverse("tecnico_compat_quitar", args=[p.pk, c.pk]))
        for c in qs
    ]


@admin_o_tecnico
def tecnico_producto(request, pk):
    """Detalle de un producto para el cuadro emergente (JSON)."""
    es_admin = rol_de(request.user) == ADMIN
    base = Producto.objects.select_related("categoria").prefetch_related("imagenes")
    # El administrador también puede abrir productos inactivos
    p = get_object_or_404(base if es_admin else base.filter(activo=True), pk=pk)
    usa_compat = p.categoria.usa_compatibilidad
    datos = {
        "id": p.pk,
        "nombre": p.nombre,
        "sku": p.sku or "",
        "categoria": p.categoria.nombre,
        "es_tecnica": p.categoria.es_tecnica,
        "descripcion": p.descripcion,
        "stock": p.stock,
        "stock_minimo": p.stock_minimo,
        "imagenes": p.galeria,
        "puede_retirar": (es_admin or p.categoria.es_tecnica) and p.stock > 0 and p.activo,
        "retirar_url": reverse("tecnico_retirar", args=[p.pk]),
        # Técnico y administrador ven los dos precios y cuándo se cambió el de venta por última vez
        "precio_compra": f"{p.precio_compra:.2f}",
        "precio_venta": f"{p.precio_venta:.2f}",
        "precio_venta_actualizado": (
            localtime(p.precio_venta_actualizado).strftime("%d/%m/%Y") if p.precio_venta_actualizado else ""
        ),
        "usa_compatibilidad": usa_compat,
        "puede_compat": usa_compat and p.activo,
        "compatibles": _lista_compatibles(p),
        "compat_buscar_url": reverse("tecnico_compat_buscar", args=[p.pk]),
        "compat_agregar_url": reverse("tecnico_compat_agregar", args=[p.pk]),
    }
    if es_admin:
        datos["editar_url"] = reverse("producto_editar", args=[p.pk])
    return JsonResponse(datos)


def _error_json(mensaje, estado=400):
    return JsonResponse({"error": mensaje}, status=estado)


@admin_o_tecnico
def tecnico_compat_buscar(request, pk):
    """Productos que se pueden vincular como compatibles (primero los de la misma categoría)."""
    p = get_object_or_404(Producto.objects.select_related("categoria"), pk=pk, activo=True)
    if not p.categoria.usa_compatibilidad:
        return _error_json("Esta categoría no usa compatibilidad.")

    q = request.GET.get("q", "").strip()
    candidatos = (
        Producto.objects.filter(activo=True)
        .exclude(pk=p.pk)
        .exclude(pk__in=p.compatibles.values("pk"))
        .select_related("categoria").prefetch_related("imagenes")
    )
    for palabra in q.split():
        candidatos = candidatos.filter(
            Q(nombre__icontains=palabra)
            | Q(sku__icontains=palabra)
            | Q(categoria__nombre__icontains=palabra)
        )
    candidatos = candidatos.annotate(
        _orden=Case(
            When(categoria_id=p.categoria_id, then=Value(0)),
            default=Value(1), output_field=IntegerField(),
        )
    ).order_by("_orden", "nombre")[:12]

    return JsonResponse({
        "resultados": [
            dict(_item_producto(c), misma_categoria=c.categoria_id == p.categoria_id)
            for c in candidatos
        ]
    })


@admin_o_tecnico
@require_POST
def tecnico_compat_agregar(request, pk):
    """Vincula otro producto como compatible (queda en los dos sentidos)."""
    p = get_object_or_404(Producto.objects.select_related("categoria"), pk=pk, activo=True)
    if not p.categoria.usa_compatibilidad:
        return _error_json("Esta categoría no usa compatibilidad.")

    otro_id = request.POST.get("otro", "")
    otro = (
        Producto.objects.filter(pk=int(otro_id), activo=True).first() if otro_id.isdigit() else None
    )
    if otro is None:
        return _error_json("No encontramos ese producto.", 404)
    if otro.pk == p.pk:
        return _error_json("Un producto no puede ser compatible consigo mismo.")

    p.compatibles.add(otro)  # si ya estaba vinculado no se duplica
    return JsonResponse({
        "compatibles": _lista_compatibles(p),
        "mensaje": f"“{otro.nombre}” ahora es compatible.",
    })


@admin_o_tecnico
@require_POST
def tecnico_compat_quitar(request, pk, otro):
    """Quita el vínculo de compatibilidad entre dos productos."""
    p = get_object_or_404(Producto, pk=pk)
    elegido = get_object_or_404(Producto, pk=otro)
    p.compatibles.remove(elegido)
    return JsonResponse({
        "compatibles": _lista_compatibles(p),
        "mensaje": f"Se quitó “{elegido.nombre}” de los compatibles.",
    })


@admin_o_tecnico
@require_POST
def tecnico_retirar(request, pk):
    """Retira una pieza para un cliente: sale del inventario y queda como venta (efectivo, QR o fiado)."""
    destino = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
        destino = reverse("tecnico_panel")

    try:
        cantidad = int(request.POST.get("cantidad", ""))
    except ValueError:
        cantidad = 0
    nota = " ".join(request.POST.get("nota", "").split())[:150]
    metodo = request.POST.get("metodo", "")
    cliente_id = request.POST.get("cliente", "")

    if cantidad < 1:
        messages.error(request, "Indica una cantidad de al menos 1.")
        return redirect(destino)
    if metodo not in (Venta.EFECTIVO, Venta.QR, Venta.FIADO):
        messages.error(request, "Elige la forma de pago: efectivo, QR o fiado.")
        return redirect(destino)
    cliente = Cliente.objects.filter(pk=int(cliente_id), activo=True).first() if cliente_id.isdigit() else None
    if cliente is None:
        messages.error(request, "Elige el cliente que se lleva la pieza.")
        return redirect(destino)

    precio_texto = request.POST.get("precio", "").strip()
    with transaction.atomic():
        # Bloquea el producto para que dos retiros a la vez no dejen el stock en negativo
        producto = get_object_or_404(Producto.objects.select_for_update(), pk=pk, activo=True)
        try:
            precio = leer_monto(precio_texto) if precio_texto else producto.precio_venta
        except InvalidOperation:
            messages.error(request, "El precio no es válido.")
            return redirect(destino)

        if rol_de(request.user) != ADMIN and not producto.categoria.es_tecnica:
            messages.error(request, "Solo puedes retirar productos de categorías técnicas.")
        elif cantidad > producto.stock:
            messages.error(request, f"Solo hay {producto.stock} unidad(es) de “{producto.nombre}”.")
        else:
            etiqueta = dict(Venta.METODOS)[metodo]
            # El dinero va a la caja según la categoría: técnica -> caja técnico; las demás -> caja productos
            caja = CajaMovimiento.TECNICO if producto.categoria.es_tecnica else CajaMovimiento.PRODUCTOS
            nombre_caja = dict(CajaMovimiento.CAJAS)[caja].lower()
            movimiento = Movimiento.objects.create(
                producto=producto, tipo=Movimiento.SALIDA, cantidad=cantidad, usuario=request.user,
                nota=f"Venta a {cliente.nombre} ({etiqueta})" + (f": {nota}" if nota else ""),
            )
            total = (precio * cantidad).quantize(Decimal("0.01"))
            venta = Venta.objects.create(
                cliente=cliente, producto=producto, producto_nombre=producto.nombre, cantidad=cantidad,
                precio_unitario=precio, costo_unitario=producto.precio_compra, total=total, metodo=metodo,
                caja=caja, movimiento=movimiento, usuario=request.user, nota=nota,
            )
            if metodo != Venta.FIADO:  # fiado no entra a caja hasta que el cliente pague
                CajaMovimiento.objects.create(
                    caja=caja, metodo=metodo, monto=total, origen=CajaMovimiento.VENTA,
                    concepto=f"Venta: {cantidad} × {producto.nombre} a {cliente.nombre}"[:200],
                    usuario=request.user, venta=venta, cliente=cliente,
                )
                aviso = f"Se registraron Bs {floatformat(total, '2g')} en la {nombre_caja} ({etiqueta.lower()})."
            else:
                aviso = f"Quedó fiado: {cliente.nombre} debe ahora Bs {floatformat(cliente.deuda, '2g')} (cuando pague irá a la {nombre_caja})."
            messages.success(
                request,
                f"Retiraste {cantidad} × “{producto.nombre}” para {cliente.nombre}. {aviso} Quedan {producto.stock}.",
            )
    return redirect(destino)
