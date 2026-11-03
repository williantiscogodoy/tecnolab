from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from .forms import CategoriaForm, MovimientoForm, ProductoForm
from .models import Categoria, Movimiento, Producto


def es_admin(user):
    return user.is_authenticated and user.is_staff


staff_required = user_passes_test(es_admin, login_url="login")


# ============================== Dashboard ==============================
@staff_required
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

    # Stock por categoría (solo productos activos)
    por_categoria = (
        Categoria.objects.filter(producto__activo=True)
        .annotate(total=Sum("producto__stock"))
        .order_by("-total")
    )
    cat_labels = [c.nombre for c in por_categoria]
    cat_data = [c.total or 0 for c in por_categoria]

    # Entradas y salidas de los últimos 7 días
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


# ============================== Productos ==============================
@staff_required
def producto_lista(request):
    productos = Producto.objects.select_related("categoria")

    q = request.GET.get("q", "").strip()
    categoria = request.GET.get("categoria", "")
    estado = request.GET.get("estado", "")

    if q:
        productos = productos.filter(Q(nombre__icontains=q) | Q(sku__icontains=q))
    if categoria.isdigit():
        productos = productos.filter(categoria_id=int(categoria))
    if estado == "activos":
        productos = productos.filter(activo=True)
    elif estado == "inactivos":
        productos = productos.filter(activo=False)
    elif estado == "bajo":
        productos = productos.filter(activo=True, stock__lte=F("stock_minimo"))

    page_obj = Paginator(productos.order_by("nombre"), 10).get_page(request.GET.get("page"))

    # Conserva los filtros al cambiar de página
    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "productos/lista.html", {
        "page_obj": page_obj,
        "pag_anterior": max(page_obj.number - 1, 1),
        "pag_siguiente": min(page_obj.number + 1, page_obj.paginator.num_pages),
        "qs": params.urlencode(),
        "q": q,
        "categoria_id": int(categoria) if categoria.isdigit() else None,
        "estado": estado,
        "hay_filtros": bool(q or categoria or estado),
        "categorias": Categoria.objects.order_by("nombre"),
        "total_general": Producto.objects.count(),
    })


@staff_required
def producto_crear(request):
    form = ProductoForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        producto = form.save(commit=False)
        stock_inicial = form.cleaned_data.get("stock") or 0
        producto.stock = 0
        producto.save()

        # El stock inicial entra como un movimiento, así queda en el historial
        if stock_inicial:
            Movimiento.objects.create(
                producto=producto,
                tipo=Movimiento.ENTRADA,
                cantidad=stock_inicial,
                usuario=request.user,
                nota="Stock inicial",
            )

        messages.success(request, f"Producto “{producto.nombre}” creado correctamente.")
        return redirect("producto_lista")

    if request.method == "POST":
        messages.error(request, "Revisa los campos marcados en rojo.")
    return render(request, "productos/form.html", {"form": form, "producto": None})


@staff_required
def producto_editar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    form = ProductoForm(request.POST or None, request.FILES or None, instance=producto)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Los cambios en “{producto.nombre}” se guardaron.")
        return redirect("producto_lista")

    if request.method == "POST":
        messages.error(request, "Revisa los campos marcados en rojo.")
    return render(request, "productos/form.html", {"form": form, "producto": producto})


@staff_required
def producto_eliminar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    num_movimientos = producto.movimiento_set.count()
    tiene_movimientos = num_movimientos > 0

    if request.method == "POST":
        if tiene_movimientos:
            # Con historial no se borra: se desactiva para conservar los registros
            if request.POST.get("accion") == "desactivar":
                producto.activo = False
                producto.save(update_fields=["activo"])
                messages.success(request, f"“{producto.nombre}” se desactivó. Su historial se conserva.")
        else:
            nombre = producto.nombre
            producto.delete()
            messages.success(request, f"“{nombre}” se eliminó.")
        return redirect("producto_lista")

    return render(request, "productos/eliminar.html", {
        "producto": producto,
        "tiene_movimientos": tiene_movimientos,
        "num_movimientos": num_movimientos,
    })


# ============================== Categorías ==============================
@staff_required
def categoria_lista(request):
    categorias = Categoria.objects.annotate(
        num_productos=Count("producto"),
        total_stock=Sum("producto__stock"),
    ).order_by("nombre")
    return render(request, "categorias/lista.html", {"categorias": categorias})


@staff_required
def categoria_guardar(request, pk=None):
    """Crea o edita una categoría (se usa desde el cuadro de diálogo de la lista)."""
    if request.method != "POST":
        return redirect("categoria_lista")

    categoria = get_object_or_404(Categoria, pk=pk) if pk else None
    form = CategoriaForm(request.POST, instance=categoria)

    if form.is_valid():
        guardada = form.save()
        accion = "actualizada" if pk else "creada"
        messages.success(request, f"Categoría “{guardada.nombre}” {accion}.")
    else:
        error = next(iter(form.errors.values()))[0]
        messages.error(request, error)
    return redirect("categoria_lista")


@staff_required
def categoria_eliminar(request, pk):
    if request.method != "POST":
        return redirect("categoria_lista")

    categoria = get_object_or_404(Categoria, pk=pk)
    if categoria.producto_set.exists():
        messages.error(request, f"No se puede eliminar “{categoria.nombre}” porque tiene productos asignados.")
    else:
        nombre = categoria.nombre
        categoria.delete()
        messages.success(request, f"Categoría “{nombre}” eliminada.")
    return redirect("categoria_lista")


# ============================== Movimientos ==============================
@staff_required
def movimiento_lista(request):
    movimientos = Movimiento.objects.select_related("producto", "usuario")

    q = request.GET.get("q", "").strip()
    tipo = request.GET.get("tipo", "")
    desde = parse_date(request.GET.get("desde", "") or "")
    hasta = parse_date(request.GET.get("hasta", "") or "")

    if q:
        movimientos = movimientos.filter(Q(producto__nombre__icontains=q) | Q(producto__sku__icontains=q))
    if tipo in (Movimiento.ENTRADA, Movimiento.SALIDA):
        movimientos = movimientos.filter(tipo=tipo)
    else:
        tipo = ""
    if desde:
        movimientos = movimientos.filter(fecha__date__gte=desde)
    if hasta:
        movimientos = movimientos.filter(fecha__date__lte=hasta)

    totales = {r["tipo"]: r["t"] for r in movimientos.values("tipo").annotate(t=Sum("cantidad"))}
    page_obj = Paginator(movimientos.order_by("-fecha", "-pk"), 15).get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)

    return render(request, "movimientos/lista.html", {
        "page_obj": page_obj,
        "pag_anterior": max(page_obj.number - 1, 1),
        "pag_siguiente": min(page_obj.number + 1, page_obj.paginator.num_pages),
        "qs": params.urlencode(),
        "q": q,
        "tipo": tipo,
        "desde": desde.isoformat() if desde else "",
        "hasta": hasta.isoformat() if hasta else "",
        "hay_filtros": bool(q or tipo or desde or hasta),
        "total_entradas": totales.get(Movimiento.ENTRADA, 0),
        "total_salidas": totales.get(Movimiento.SALIDA, 0),
        "total_general": Movimiento.objects.count(),
    })


@staff_required
def movimiento_crear(request):
    inicial = {"tipo": Movimiento.ENTRADA}
    pre = request.GET.get("producto", "")
    if pre.isdigit():
        inicial["producto"] = int(pre)

    form = MovimientoForm(request.POST or None, initial=inicial)

    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        with transaction.atomic():
            # Bloquea la fila del producto para que dos salidas simultáneas no dejen el stock en negativo
            producto = Producto.objects.select_for_update().get(pk=datos["producto"].pk)
            if datos["tipo"] == Movimiento.SALIDA and datos["cantidad"] > producto.stock:
                form.add_error("cantidad", f"Solo hay {producto.stock} unidad(es) en stock.")
            else:
                Movimiento.objects.create(
                    producto=producto,
                    tipo=datos["tipo"],
                    cantidad=datos["cantidad"],
                    nota=datos["nota"],
                    usuario=request.user,
                )
                tipo_txt = "Entrada" if datos["tipo"] == Movimiento.ENTRADA else "Salida"
                messages.success(
                    request,
                    f"{tipo_txt} de {datos['cantidad']} unidad(es) registrada. "
                    f"Stock de “{producto.nombre}”: {producto.stock}.",
                )
                return redirect("movimiento_lista")

    if request.method == "POST":
        messages.error(request, "Revisa los datos del movimiento.")

    return render(request, "movimientos/form.html", {
        "form": form,
        "productos": Producto.objects.filter(activo=True).order_by("nombre"),
    })