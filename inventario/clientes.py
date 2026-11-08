"""Clientes (quienes compran piezas): búsqueda, alta rápida, lista, detalle y pagos de deudas."""
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import floatformat
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import CajaMovimiento, Cliente, Venta
from .roles import admin_o_tecnico, solo_admin

CERO = Decimal("0")


# ------------------------------------------------------------------ utilidades
def leer_monto(texto, maximo=Decimal("100000000")):
    """'12,5' o '12.50' -> Decimal con 2 decimales. Lanza InvalidOperation si no es un monto válido (>= 0)."""
    monto = Decimal((texto or "").strip().replace(",", "."))
    if not monto.is_finite() or monto < 0 or monto >= maximo:
        raise InvalidOperation
    return monto.quantize(Decimal("0.01"))


def deudas_por_cliente():
    """{cliente_id: deuda} solo de quienes deben algo."""
    fiado = {
        r["cliente"]: r["t"]
        for r in Venta.objects.filter(metodo=Venta.FIADO).values("cliente").annotate(t=Sum("total")).order_by()
    }
    pagado = {
        r["cliente"]: r["t"]
        for r in CajaMovimiento.objects.filter(origen=CajaMovimiento.ABONO, cliente__isnull=False)
        .values("cliente").annotate(t=Sum("monto")).order_by()
    }
    deudas = {cid: total - pagado.get(cid, CERO) for cid, total in fiado.items()}
    return {cid: d for cid, d in deudas.items() if d > 0}


def estado_de_ventas(cliente):
    """Cada venta con lo pagado y lo que resta. Los pagos se aplican a las deudas más antiguas primero."""
    restante = (
        cliente.movimientos_caja.filter(origen=CajaMovimiento.ABONO).aggregate(t=Sum("monto"))["t"] or CERO
    )
    filas = []
    for v in cliente.ventas.order_by("fecha", "pk"):
        if v.metodo != Venta.FIADO:
            filas.append({"venta": v, "pagado": v.total, "saldo": CERO, "estado": "pagado"})
            continue
        aplicado = min(v.total, restante)
        restante -= aplicado
        saldo = v.total - aplicado
        estado = "pagado" if saldo == 0 else ("parcial" if aplicado > 0 else "debe")
        filas.append({"venta": v, "pagado": aplicado, "saldo": saldo, "estado": estado})
    filas.reverse()  # lo más reciente primero
    return filas


def repartir_pago(cliente, monto):
    """Reparte un pago entre las cajas según lo que se está pagando.

    Los pagos cubren primero las deudas más antiguas; cada venta fiada manda su dinero a la caja
    que le correspondió al venderse (técnico o productos). Devuelve {caja: monto}.
    """
    ya_pagado = (
        cliente.movimientos_caja.filter(origen=CajaMovimiento.ABONO).aggregate(t=Sum("monto"))["t"] or CERO
    )
    por_asignar, reparto = monto, {}
    for v in cliente.ventas.filter(metodo=Venta.FIADO).order_by("fecha", "pk"):
        cubierto = min(v.total, ya_pagado)
        ya_pagado -= cubierto
        saldo = v.total - cubierto
        if saldo <= 0:
            continue
        parte = min(saldo, por_asignar)
        reparto[v.caja] = reparto.get(v.caja, CERO) + parte
        por_asignar -= parte
        if por_asignar <= 0:
            break
    return reparto


def _datos_cliente(c, deuda=None):
    return {
        "id": c.pk, "nombre": c.nombre, "telefono": c.telefono,
        "deuda": float(c.deuda if deuda is None else deuda),
    }


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["nombre", "telefono", "notas"]

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        if not nombre:
            raise forms.ValidationError("Escribe el nombre del cliente.")
        return nombre

    def clean_telefono(self):
        return " ".join(self.cleaned_data.get("telefono", "").split())

    def clean(self):
        datos = super().clean()
        nombre, tel = datos.get("nombre"), datos.get("telefono", "")
        if nombre:
            repetido = Cliente.objects.filter(nombre__iexact=nombre, telefono=tel)
            if self.instance.pk:
                repetido = repetido.exclude(pk=self.instance.pk)
            if repetido.exists():
                self.add_error("nombre", "Ya existe un cliente con ese nombre y teléfono.")
        return datos


# ------------------------------------------------------------------ al retirar una pieza (técnico y admin)
@admin_o_tecnico
def clientes_buscar(request):
    """Clientes activos para elegir al retirar una pieza; primero los que compraron hace poco."""
    q = request.GET.get("q", "").strip()
    clientes = Cliente.objects.filter(activo=True)
    for palabra in q.split():
        clientes = clientes.filter(Q(nombre__icontains=palabra) | Q(telefono__icontains=palabra))
    clientes = list(clientes.annotate(ultima=Max("ventas__fecha")).order_by("-ultima", "nombre")[:8])
    deudas = deudas_por_cliente()
    return JsonResponse({"resultados": [_datos_cliente(c, deudas.get(c.pk, CERO)) for c in clientes]})


@admin_o_tecnico
@require_POST
def clientes_rapido(request):
    """Crea un cliente al momento (nombre y teléfono) sin salir del cuadro de retiro."""
    form = ClienteForm({"nombre": request.POST.get("nombre", ""), "telefono": request.POST.get("telefono", "")})
    if not form.is_valid():
        error = next(iter(form.errors.values()))[0]
        return JsonResponse({"error": error}, status=400)
    cliente = form.save()
    return JsonResponse(_datos_cliente(cliente, CERO), status=201)


# ------------------------------------------------------------------ sección Usuarios > Clientes (administrador)
@solo_admin
def clientes_lista(request):
    q = request.GET.get("q", "").strip()
    ver = request.GET.get("ver", "")
    deudas = deudas_por_cliente()

    base = Cliente.objects.all()
    conteos = {
        "activos": base.filter(activo=True).count(),
        "deuda": base.filter(pk__in=deudas.keys(), activo=True).count() if deudas else 0,
        "inactivos": base.filter(activo=False).count(),
    }

    clientes = base
    if ver == "deuda":
        clientes = clientes.filter(pk__in=deudas.keys())
    elif ver == "inactivos":
        clientes = clientes.filter(activo=False)
    else:
        ver = ""
        clientes = clientes.filter(activo=True)
    for palabra in q.split():
        clientes = clientes.filter(Q(nombre__icontains=palabra) | Q(telefono__icontains=palabra))

    page_obj = Paginator(clientes.order_by("nombre"), 20).get_page(request.GET.get("page"))
    compras = {
        r["cliente"]: r
        for r in Venta.objects.filter(cliente__in=[c.pk for c in page_obj])
        .values("cliente").annotate(n=Count("pk"), t=Sum("total")).order_by()
    }
    for c in page_obj:
        c.n_compras = compras.get(c.pk, {}).get("n", 0)
        c.total_comprado = compras.get(c.pk, {}).get("t") or CERO
        c.debe = deudas.get(c.pk, CERO)

    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "usuarios/clientes_lista.html", {
        "tab": "clientes",
        "page_obj": page_obj,
        "pag_anterior": max(page_obj.number - 1, 1),
        "pag_siguiente": min(page_obj.number + 1, page_obj.paginator.num_pages),
        "qs": params.urlencode(),
        "q": q, "ver": ver, "conteos": conteos,
        "deuda_total": sum(deudas.values(), CERO),
        "n_deudores": len(deudas),
        "hay_filtros": bool(q or ver),
    })


@solo_admin
def cliente_detalle(request, pk):
    cliente = get_object_or_404(Cliente, pk=pk)
    filas = estado_de_ventas(cliente)
    total_comprado = sum((f["venta"].total for f in filas), CERO)
    deuda = cliente.deuda
    pagos = cliente.movimientos_caja.filter(origen=CajaMovimiento.ABONO).select_related("usuario")
    return render(request, "usuarios/cliente_detalle.html", {
        "tab": "clientes",
        "cliente": cliente,
        "filas": filas,
        "pagos": pagos,
        "total_comprado": total_comprado,
        "deuda": deuda,
        "pagado_total": total_comprado - deuda,
        "n_compras": len(filas),
    })


@solo_admin
@require_POST
def cliente_guardar(request, pk=None):
    """Crea o edita un cliente."""
    cliente = get_object_or_404(Cliente, pk=pk) if pk else None
    form = ClienteForm(request.POST, instance=cliente)
    destino = reverse("cliente_detalle", args=[pk]) if pk else reverse("clientes_lista")
    if not form.is_valid():
        messages.error(request, next(iter(form.errors.values()))[0])
        return redirect(destino)
    nuevo = form.save(commit=False)
    if cliente:
        quiere_activo = request.POST.get("activo") == "on"
        if not quiere_activo and cliente.deuda > 0:
            messages.error(request, f"{cliente.nombre} todavía debe Bs {floatformat(cliente.deuda, '2g')}. Cobra la deuda antes de desactivarlo.")
            return redirect(destino)
        nuevo.activo = quiere_activo
    nuevo.save()
    messages.success(request, f"Cliente “{nuevo.nombre}” {'actualizado' if cliente else 'creado'}.")
    return redirect(reverse("cliente_detalle", args=[nuevo.pk]) if not cliente else destino)


@solo_admin
@require_POST
def cliente_pagar(request, pk):
    """Registra un pago (total o parcial) de la deuda de un cliente. El dinero va a la caja de lo que se vendió."""
    destino = reverse("cliente_detalle", args=[pk])
    metodo = request.POST.get("metodo", "")
    if metodo not in (CajaMovimiento.EFECTIVO, CajaMovimiento.QR):
        messages.error(request, "Elige cómo pagó: efectivo o QR.")
        return redirect(destino)
    nota = " ".join(request.POST.get("nota", "").split())[:120]

    with transaction.atomic():
        cliente = get_object_or_404(Cliente.objects.select_for_update(), pk=pk)
        deuda = cliente.deuda
        if deuda <= 0:
            messages.info(request, f"{cliente.nombre} no tiene deudas pendientes.")
            return redirect(destino)
        if request.POST.get("todo") == "1":
            monto = deuda
        else:
            try:
                monto = leer_monto(request.POST.get("monto", ""))
            except InvalidOperation:
                messages.error(request, "Escribe cuánto pagó.")
                return redirect(destino)
            if monto <= 0:
                messages.error(request, "El pago debe ser mayor a 0.")
                return redirect(destino)
            if monto > deuda:
                messages.error(request, f"El pago no puede ser mayor a lo que debe (Bs {floatformat(deuda, '2g')}).")
                return redirect(destino)
        reparto = repartir_pago(cliente, monto)
        for caja, parte in reparto.items():  # una entrada por caja (el pago puede cubrir ventas de las dos)
            CajaMovimiento.objects.create(
                caja=caja, metodo=metodo, monto=parte, origen=CajaMovimiento.ABONO,
                concepto=f"Pago de deuda: {cliente.nombre}" + (f" ({nota})" if nota else ""),
                usuario=request.user, cliente=cliente,
            )
        restante = deuda - monto
        donde = " y ".join(
            f"{dict(CajaMovimiento.CAJAS)[c].lower()} Bs {floatformat(p, '2g')}" for c, p in reparto.items()
        )
    if restante == 0:
        messages.success(request, f"{cliente.nombre} pagó todo (Bs {floatformat(monto, '2g')}). Ya no debe nada. Entró a la {donde}.")
    else:
        messages.success(request, f"Se registró el pago de Bs {floatformat(monto, '2g')} (entró a la {donde}). {cliente.nombre} aún debe Bs {floatformat(restante, '2g')}.")
    return redirect(destino)
