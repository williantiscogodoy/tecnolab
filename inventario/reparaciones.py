import re
from collections import Counter

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from .forms import ReparacionEstadoForm, ReparacionForm
from .models import Movimiento, Producto, Reparacion, ReparacionPieza
from .roles import ADMIN, admin_o_tecnico, rol_de
from .utils import url_params


class PiezaInvalida(Exception):
    """Se lanza dentro de una transacción para deshacer todo si una pieza no se puede usar."""


# ============================== Utilidades ==============================
def _alcance(user):
    """El técnico ve solo sus reparaciones; el administrador ve todas."""
    qs = Reparacion.objects.all()
    return qs if rol_de(user) == ADMIN else qs.filter(tecnico=user)


def reparaciones_de(user):
    return _alcance(user).select_related("tecnico").prefetch_related("piezas__producto")


def _obtener(request, pk):
    reparacion = get_object_or_404(
        Reparacion.objects.select_related("tecnico").prefetch_related("piezas__producto"), pk=pk
    )
    if rol_de(request.user) != ADMIN and reparacion.tecnico_id != request.user.pk:
        raise Http404
    return reparacion


def piezas_disponibles(user):
    """Productos que se pueden elegir como pieza (con stock). El técnico solo ve los de categorías técnicas."""
    productos = Producto.objects.filter(activo=True, stock__gt=0).order_by("nombre")
    if rol_de(user) != ADMIN:
        productos = productos.filter(categoria__es_tecnica=True)

    etiquetas = [
        (p, f"{p.nombre}{' · ' + p.sku if p.sku else ''} (quedan {p.stock})") for p in productos
    ]
    repetidas = {e for e, n in Counter(e for _, e in etiquetas).items() if n > 1}
    return [
        {"id": p.pk, "stock": p.stock, "label": f"{e} #{p.pk}" if e in repetidas else e}
        for p, e in etiquetas
    ]


def _filas_post(post):
    """Filas de piezas tal como llegaron, para volver a mostrarlas si hubo un error."""
    return [
        {"id": pid, "label": texto, "cantidad": cant or "1"}
        for pid, texto, cant in zip(post.getlist("pieza"), post.getlist("pieza_texto"), post.getlist("cantidad"))
        if pid or texto.strip()
    ]


def _leer_piezas(post):
    """Devuelve {producto_id: cantidad} desde el formulario o lanza PiezaInvalida."""
    piezas = {}
    for pid, texto, cant in zip(post.getlist("pieza"), post.getlist("pieza_texto"), post.getlist("cantidad")):
        if not pid and not texto.strip():
            continue  # fila vacía
        if not pid:
            raise PiezaInvalida("Elige las piezas desde la lista de sugerencias.")
        try:
            pid, cant = int(pid), int(cant)
        except ValueError:
            raise PiezaInvalida("Revisa las piezas: hay un producto o una cantidad inválida.")
        if cant < 1:
            raise PiezaInvalida("La cantidad de cada pieza debe ser al menos 1.")
        piezas[pid] = piezas.get(pid, 0) + cant
    return piezas


def _consumir_piezas(reparacion, usuario, piezas):
    """Descuenta del inventario las piezas usadas. Debe llamarse dentro de transaction.atomic()."""
    es_admin = rol_de(usuario) == ADMIN
    nombres = []
    for pid in sorted(piezas):  # orden fijo para evitar bloqueos cruzados
        cantidad = piezas[pid]
        producto = Producto.objects.select_for_update().filter(pk=pid, activo=True).first()
        if producto is None:
            raise PiezaInvalida("Una de las piezas ya no está disponible.")
        if not es_admin and not producto.categoria.es_tecnica:
            raise PiezaInvalida(f"“{producto.nombre}” no es una pieza técnica.")
        if cantidad > producto.stock:
            raise PiezaInvalida(f"Solo hay {producto.stock} unidad(es) de “{producto.nombre}”.")

        movimiento = Movimiento.objects.create(
            producto=producto,
            tipo=Movimiento.SALIDA,
            cantidad=cantidad,
            usuario=usuario,
            nota=f"Reparación {reparacion.codigo}: {reparacion.cliente}"[:200],
        )
        ReparacionPieza.objects.create(
            reparacion=reparacion, producto=producto, cantidad=cantidad, movimiento=movimiento
        )
        nombres.append(producto.nombre)
    return nombres


# ============================== Lista ==============================
@admin_o_tecnico
def reparacion_lista(request):
    base = reparaciones_de(request.user)
    q = request.GET.get("q", "").strip()
    estado = request.GET.get("estado", "")
    estados_validos = dict(Reparacion.ESTADOS)

    cuentas = {r["estado"]: r["n"] for r in _alcance(request.user).order_by().values("estado").annotate(n=Count("pk"))}

    reparaciones = base
    for palabra in q.split():
        filtro = (
            Q(cliente__icontains=palabra) | Q(equipo__icontains=palabra)
            | Q(telefono_cliente__icontains=palabra) | Q(trabajo__icontains=palabra)
            | Q(notas__icontains=palabra)
        )
        codigo = re.fullmatch(r"[Rr]?-?0*(\d+)", palabra)  # permite buscar "R-0012" o "12"
        if codigo:
            filtro |= Q(pk=int(codigo.group(1)))
        reparaciones = reparaciones.filter(filtro)
    if estado in estados_validos:
        reparaciones = reparaciones.filter(estado=estado)
    else:
        estado = ""

    page_obj = Paginator(reparaciones, 15).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    params.pop("partial", None)

    chips = [{"nombre": "Todas", "n": sum(cuentas.values()), "url": url_params(q=q), "activo": not estado}]
    chips += [
        {"nombre": nombre, "n": cuentas.get(clave, 0), "url": url_params(q=q, estado=clave), "activo": estado == clave}
        for clave, nombre in Reparacion.ESTADOS
    ]

    ctx = {
        "page_obj": page_obj,
        "pag_anterior": max(page_obj.number - 1, 1),
        "pag_siguiente": min(page_obj.number + 1, page_obj.paginator.num_pages),
        "qs": params.urlencode(),
        "q": q,
        "estado": estado,
        "chips": chips,
        "total": sum(cuentas.values()),
        "es_admin": rol_de(request.user) == ADMIN,
    }
    if request.GET.get("partial"):
        return JsonResponse({
            "filtros": render_to_string("reparaciones/_filtros.html", ctx, request),
            "tabla": render_to_string("reparaciones/_tabla.html", ctx, request),
        })
    return render(request, "reparaciones/lista.html", ctx)


# ============================== Crear ==============================
@admin_o_tecnico
def reparacion_crear(request):
    form = ReparacionForm(request.POST or None)
    filas = []

    if request.method == "POST":
        filas = _filas_post(request.POST)
        error = None
        piezas = {}
        try:
            piezas = _leer_piezas(request.POST)
        except PiezaInvalida as e:
            error = str(e)

        if form.is_valid() and not error:
            if not form.cleaned_data["trabajo"].strip() and not piezas:
                error = "Anota cuál es el arreglo o elige al menos una pieza."
            else:
                try:
                    with transaction.atomic():
                        reparacion = form.save(commit=False)
                        reparacion.tecnico = request.user
                        reparacion.save()
                        nombres = _consumir_piezas(reparacion, request.user, piezas)
                        if not reparacion.trabajo.strip():
                            reparacion.trabajo = "Cambio de " + ", ".join(nombres)
                            reparacion.save(update_fields=["trabajo", "actualizado"])
                    messages.success(request, f"Reparación {reparacion.codigo} registrada para {reparacion.cliente}.")
                    return redirect("reparacion_detalle", pk=reparacion.pk)
                except PiezaInvalida as e:
                    error = str(e)

        if error:
            form.add_error(None, error)
        messages.error(request, "No se pudo registrar la reparación. Revisa los datos.")

    return render(request, "reparaciones/form.html", {
        "form": form,
        "piezas": piezas_disponibles(request.user),
        "filas": filas,
    })


# ============================== Detalle y acciones ==============================
@admin_o_tecnico
def reparacion_detalle(request, pk):
    reparacion = _obtener(request, pk)
    return render(request, "reparaciones/detalle.html", {
        "rep": reparacion,
        "estado_form": ReparacionEstadoForm(instance=reparacion),
        "piezas": piezas_disponibles(request.user) if reparacion.abierta else [],
        "filas": [],
        "es_admin": rol_de(request.user) == ADMIN,
    })


@admin_o_tecnico
@require_POST
def reparacion_actualizar(request, pk):
    reparacion = _obtener(request, pk)
    form = ReparacionEstadoForm(request.POST, instance=reparacion)
    if form.is_valid():
        form.save()
        messages.success(request, f"Reparación {reparacion.codigo} actualizada.")
    else:
        messages.error(request, "No se pudo actualizar. Revisa el estado elegido.")
    return redirect("reparacion_detalle", pk=pk)


@admin_o_tecnico
@require_POST
def reparacion_pieza(request, pk):
    reparacion = _obtener(request, pk)
    if not reparacion.abierta:
        messages.error(request, "Esta reparación está cerrada. Cámbiale el estado a “En proceso” para agregar piezas.")
        return redirect("reparacion_detalle", pk=pk)

    try:
        piezas = _leer_piezas(request.POST)
        if not piezas:
            raise PiezaInvalida("Elige al menos una pieza.")
        with transaction.atomic():
            nombres = _consumir_piezas(reparacion, request.user, piezas)
        messages.success(request, f"Se descontó del inventario: {', '.join(nombres)}.")
    except PiezaInvalida as e:
        messages.error(request, str(e))
    return redirect("reparacion_detalle", pk=pk)