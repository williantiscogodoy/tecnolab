import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import floatformat
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import ReparacionEstadoForm, ReparacionForm
from .models import CajaMovimiento, Movimiento, Producto, Reparacion, ReparacionEquipo, ReparacionPieza
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
    return _alcance(user).select_related("tecnico").prefetch_related("piezas__producto", "equipos")


def _obtener(request, pk):
    reparacion = get_object_or_404(
        Reparacion.objects.select_related("tecnico").prefetch_related("piezas__producto", "equipos"), pk=pk
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
        {"id": p.pk, "stock": p.stock, "costo": float(p.precio_compra), "nombre": p.nombre,
         "label": f"{e} #{p.pk}" if e in repetidas else e}
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


def _filas_equipos_post(post):
    """Filas de teléfonos tal como llegaron, para volver a mostrarlas si hubo un error."""
    return [
        {"modelo": modelo, "falla": falla}
        for modelo, falla in zip(post.getlist("equipo_modelo"), post.getlist("equipo_falla"))
        if modelo.strip() or falla.strip()
    ]


def _leer_equipos(post):
    """Devuelve [{'modelo', 'falla'}] con los teléfonos que dejó el cliente o lanza PiezaInvalida."""
    equipos = []
    for modelo, falla in zip(post.getlist("equipo_modelo"), post.getlist("equipo_falla")):
        modelo, falla = " ".join(modelo.split()), falla.strip()
        if not modelo and not falla:
            continue  # fila vacía
        if not modelo:
            raise PiezaInvalida("Escribe el modelo de cada teléfono que dejó el cliente.")
        if len(modelo) > 120:
            raise PiezaInvalida("El modelo de un teléfono es demasiado largo (máximo 120 letras).")
        equipos.append({"modelo": modelo, "falla": falla})
    if not equipos:
        raise PiezaInvalida("Anota al menos un teléfono que dejó el cliente.")
    return equipos


def _resumen_equipos(equipos):
    """Texto corto de los modelos y de las fallas, para la lista y la búsqueda."""
    modelos = " + ".join(e["modelo"] for e in equipos)
    if len(modelos) > 120:
        modelos = modelos[:119] + "…"
    if len(equipos) == 1:
        trabajo = equipos[0]["falla"]
    else:
        trabajo = "\n".join(f"{e['modelo']}: {e['falla']}" for e in equipos if e["falla"])
    return modelos, trabajo


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
            reparacion=reparacion, producto=producto, cantidad=cantidad,
            costo_unitario=producto.precio_compra, movimiento=movimiento,
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
            | Q(equipos__modelo__icontains=palabra) | Q(equipos__falla__icontains=palabra)
        )
        codigo = re.fullmatch(r"[Rr]?-?0*(\d+)", palabra)  # permite buscar "R-0012" o "12"
        if codigo:
            filtro |= Q(pk=int(codigo.group(1)))
        reparaciones = reparaciones.filter(filtro)
    reparaciones = reparaciones.distinct()  # un mismo trabajo puede coincidir por varios equipos
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
    filas, filas_equipos = [], []

    if request.method == "POST":
        filas = _filas_post(request.POST)
        filas_equipos = _filas_equipos_post(request.POST)
        error = None
        piezas, equipos = {}, []
        try:
            equipos = _leer_equipos(request.POST)
            piezas = _leer_piezas(request.POST)
        except PiezaInvalida as e:
            error = str(e)

        if form.is_valid() and not error:
            if not any(e["falla"] for e in equipos) and not piezas:
                error = "Anota la falla de cada teléfono o elige al menos una pieza."
            else:
                try:
                    with transaction.atomic():
                        reparacion = form.save(commit=False)
                        reparacion.tecnico = request.user
                        reparacion.equipo, reparacion.trabajo = _resumen_equipos(equipos)
                        reparacion.save()
                        ReparacionEquipo.objects.bulk_create(
                            [ReparacionEquipo(reparacion=reparacion, **e) for e in equipos]
                        )
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
        "filas_equipos": filas_equipos,
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
        "puede_cobrar": puede_cobrar(reparacion),
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
        messages.error(request, "No se pudo actualizar. Revisa el estado y el cobro.")
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

# ============================== Entrega y cobro ("Bien") ==============================
def puede_cobrar(reparacion):
    """Se puede registrar el cobro mientras no se haya cobrado y el equipo tenga arreglo."""
    return reparacion.cobrado_en is None and reparacion.estado != Reparacion.NO_REPARABLE


@admin_o_tecnico
def reparacion_resumen(request, pk):
    """Resumen para el cuadro "Bien": qué se reparó, qué piezas salieron y cuánto se cobra (JSON)."""
    r = _obtener(request, pk)
    if not puede_cobrar(r):
        return JsonResponse({"error": "Esta reparación ya no se puede cobrar."}, status=400)

    equipos = [{"modelo": e.modelo, "falla": e.falla} for e in r.equipos.all()]
    if not equipos:  # reparaciones muy antiguas, sin teléfonos separados
        equipos = [{"modelo": r.equipo, "falla": r.trabajo}]
    return JsonResponse({
        "codigo": r.codigo,
        "cliente": r.cliente,
        "equipos": equipos,
        "piezas": [
            {"nombre": pz.producto.nombre, "cantidad": pz.cantidad, "costo": float(pz.subtotal)}
            for pz in r.piezas.all()
        ],
        "costo_piezas": float(r.costo_piezas),
        "cobro": None if r.cobro is None else float(r.cobro),
        "ya_entregada": r.estado == Reparacion.ENTREGADO,
        "cobrar_url": reverse("reparacion_cobrar", args=[r.pk]),
    })


@admin_o_tecnico
@require_POST
def reparacion_cobrar(request, pk):
    """Marca la reparación como entregada y registra el dinero recibido en la caja."""
    _obtener(request, pk)  # comprueba que el técnico solo toque lo suyo
    destino = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(destino, {request.get_host()}, request.is_secure()):
        destino = reverse("reparacion_detalle", args=[pk])

    metodo = request.POST.get("metodo", CajaMovimiento.EFECTIVO)
    if metodo not in (CajaMovimiento.EFECTIVO, CajaMovimiento.QR):
        messages.error(request, "Elige cómo pagó el cliente: efectivo o QR.")
        return redirect(destino)

    try:
        monto = Decimal(request.POST.get("monto", "").strip().replace(",", "."))
        if not monto.is_finite() or monto < 0 or monto >= Decimal("100000000"):
            raise InvalidOperation
        monto = monto.quantize(Decimal("0.01"))
    except InvalidOperation:
        messages.error(request, "Escribe cuánto se cobró (puede ser 0 si no se cobró nada).")
        return redirect(destino)

    with transaction.atomic():
        r = Reparacion.objects.select_for_update().get(pk=pk)  # evita registrar el cobro dos veces
        if not puede_cobrar(r):
            messages.error(request, f"La reparación {r.codigo} ya estaba cobrada o no se puede cobrar.")
            return redirect(destino)
        r.estado = Reparacion.ENTREGADO
        r.cobrado, r.cobrado_en, r.cobrado_por = monto, timezone.now(), request.user
        if r.cobro is None:
            r.cobro = monto
        r.save()
        # El dinero de las reparaciones va a la caja del técnico (aparte de la de productos)
        CajaMovimiento.objects.create(
            caja=CajaMovimiento.TECNICO, metodo=metodo, monto=monto, origen=CajaMovimiento.REPARACION,
            concepto=f"Reparación {r.codigo}: {r.cliente}"[:200],
            usuario=request.user, fecha=r.cobrado_en, reparacion=r,
        )

    messages.success(request, f"Reparación {r.codigo} entregada. Se registraron Bs {floatformat(monto, '2g')} en la caja técnico ({dict(CajaMovimiento.METODOS)[metodo].lower()}).")
    return redirect(destino)
