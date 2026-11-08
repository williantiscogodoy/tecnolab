from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class Categoria(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    es_tecnica = models.BooleanField(
        "Categoría técnica",
        default=False,
        help_text="Sus productos aparecen primero en el panel del técnico.",
    )
    usa_compatibilidad = models.BooleanField(
        "Usa compatibilidad",
        default=False,
        help_text="Permite vincular productos compatibles o similares (ej.: una pantalla que sirve para varios modelos).",
    )

    class Meta:
        verbose_name_plural = "categorías"

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    nombre = models.CharField(max_length=150)
    # El SKU es opcional. null=True permite varios productos sin código (NULL no cuenta como repetido).
    sku = models.CharField("SKU", max_length=50, unique=True, null=True, blank=True)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT)
    descripcion = models.TextField(blank=True)
    imagen = models.ImageField(upload_to="productos/", blank=True, null=True)
    precio_compra = models.DecimalField(max_digits=10, decimal_places=2)
    precio_venta = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=5)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    # Última vez que se cambió el precio de venta (se actualiza sola al guardar)
    precio_venta_actualizado = models.DateTimeField(null=True, blank=True, editable=False)
    # Compatibilidad mutua: si A es compatible con B, B también lo es con A.
    compatibles = models.ManyToManyField(
        "self", blank=True, symmetrical=True, verbose_name="productos compatibles",
    )

    def __str__(self):
        return f"{self.nombre} ({self.sku})" if self.sku else self.nombre

    @classmethod
    def from_db(cls, db, field_names, values):
        producto = super().from_db(db, field_names, values)
        # Guarda el precio con el que se cargó para saber si cambia al guardar
        producto._precio_venta_original = producto.__dict__.get("precio_venta")
        return producto

    def save(self, *args, **kwargs):
        original = getattr(self, "_precio_venta_original", None)
        if self._state.adding or (original is not None and original != self.precio_venta):
            self.precio_venta_actualizado = timezone.now()
            campos = kwargs.get("update_fields")
            if campos is not None:
                kwargs["update_fields"] = set(campos) | {"precio_venta_actualizado"}
        super().save(*args, **kwargs)
        self._precio_venta_original = self.precio_venta

    @property
    def galeria(self):
        """URLs de todas las imágenes: la principal primero y luego las de referencia."""
        urls = [self.imagen.url] if self.imagen else []
        urls += [r.imagen.url for r in self.imagenes.all()]
        return urls

    @property
    def portada_url(self):
        """Imagen para miniaturas: la principal o, si no hay, la primera de referencia."""
        if self.imagen:
            return self.imagen.url
        referencias = list(self.imagenes.all())  # usa prefetch_related("imagenes") si existe
        return referencias[0].imagen.url if referencias else None


class ProductoImagen(models.Model):
    """Imágenes de referencia adicionales de un producto (ángulos, detalles, empaque…)."""
    producto = models.ForeignKey(Producto, on_delete=models.CASCADE, related_name="imagenes")
    imagen = models.ImageField(upload_to="productos/referencias/")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "imagen de referencia"
        verbose_name_plural = "imágenes de referencia"

    def __str__(self):
        return f"Imagen de {self.producto.nombre}"


class Movimiento(models.Model):
    ENTRADA = "ENTRADA"
    SALIDA = "SALIDA"
    TIPOS = [(ENTRADA, "Entrada"), (SALIDA, "Salida")]

    producto = models.ForeignKey(Producto, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=10, choices=TIPOS)
    cantidad = models.PositiveIntegerField()
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    nota = models.CharField(max_length=200, blank=True)

    def save(self, *args, **kwargs):
        es_nuevo = self.pk is None
        super().save(*args, **kwargs)
        if es_nuevo:  # actualiza el stock solo al crear el movimiento
            if self.tipo == self.ENTRADA:
                self.producto.stock += self.cantidad
            else:
                self.producto.stock = max(0, self.producto.stock - self.cantidad)
            self.producto.save()


class Reparacion(models.Model):
    """Trabajo de un técnico sobre el equipo de un cliente."""
    EN_PROCESO = "EN_PROCESO"
    LISTO = "LISTO"
    ENTREGADO = "ENTREGADO"
    NO_REPARABLE = "NO_REPARABLE"
    ESTADOS = [
        (EN_PROCESO, "En proceso"),
        (LISTO, "Listo para entregar"),
        (ENTREGADO, "Entregado"),
        (NO_REPARABLE, "No reparable"),
    ]

    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reparaciones",
    )
    cliente = models.CharField(max_length=120)
    telefono_cliente = models.CharField("Teléfono del cliente", max_length=30, blank=True)
    equipo = models.CharField(max_length=120)
    trabajo = models.TextField("Arreglo", blank=True)
    notas = models.TextField(blank=True)
    # Lo que el técnico va a cobrar al cliente por todos los arreglos
    cobro = models.DecimalField("Cobro al cliente", max_digits=10, decimal_places=2, null=True, blank=True)
    # Dinero que realmente se recibió al entregar el equipo (es lo que suma la caja)
    cobrado = models.DecimalField("Dinero cobrado", max_digits=10, decimal_places=2, null=True, blank=True)
    cobrado_en = models.DateTimeField(null=True, blank=True)
    cobrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="cobros_reparaciones",
    )
    estado = models.CharField(max_length=20, choices=ESTADOS, default=EN_PROCESO)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado", "-pk"]
        verbose_name = "reparación"
        verbose_name_plural = "reparaciones"

    def __str__(self):
        return f"{self.codigo} · {self.cliente}"

    @property
    def codigo(self):
        return f"R-{self.pk:04d}" if self.pk else ""

    @property
    def abierta(self):
        return self.estado in (self.EN_PROCESO, self.LISTO)

    @property
    def costo_piezas(self):
        """Inversión aproximada: lo que costaron las piezas usadas (precio de compra de cuando se usaron)."""
        return sum((pz.subtotal for pz in self.piezas.all()), Decimal("0"))

    @property
    def cobrada(self):
        """True cuando ya se registró el dinero recibido (ya está en caja)."""
        return self.cobrado_en is not None

    @property
    def ingreso(self):
        """Lo que entró: el dinero cobrado si ya se cobró; si no, el cobro anotado."""
        return self.cobrado if self.cobrado is not None else self.cobro

    @property
    def ganancia(self):
        """Ganancia del arreglo = ingreso - inversión en piezas (None si aún no hay monto)."""
        base = self.ingreso
        return None if base is None else base - self.costo_piezas

    @property
    def margen(self):
        """Ganancia como porcentaje del ingreso, igual que la ganancia por unidad de un producto."""
        base = self.ingreso
        if not base:
            return None
        return (base - self.costo_piezas) / base * 100


class ReparacionEquipo(models.Model):
    """Cada teléfono que el cliente deja en una misma reparación, con su propia falla."""
    reparacion = models.ForeignKey(Reparacion, on_delete=models.CASCADE, related_name="equipos")
    modelo = models.CharField(max_length=120)
    falla = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "equipo de la reparación"
        verbose_name_plural = "equipos de la reparación"

    def __str__(self):
        return self.modelo


class ReparacionPieza(models.Model):
    """Pieza del inventario usada en una reparación (genera una salida automática)."""
    reparacion = models.ForeignKey(Reparacion, on_delete=models.CASCADE, related_name="piezas")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    # Precio de compra de la pieza cuando se usó (así la ganancia no cambia si el precio sube después)
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    movimiento = models.OneToOneField(
        Movimiento, null=True, blank=True, on_delete=models.SET_NULL, related_name="pieza_reparacion"
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.cantidad} × {self.producto.nombre}"

    @property
    def subtotal(self):
        return self.cantidad * self.costo_unitario


class Cliente(models.Model):
    """Quien compra piezas (técnicos externos o clientes de mostrador). No inicia sesión."""
    nombre = models.CharField(max_length=120)
    telefono = models.CharField("teléfono", max_length=30, blank=True)
    notas = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    @property
    def deuda(self):
        """Lo que debe: ventas fiadas menos los pagos que fue haciendo."""
        fiado = self.ventas.filter(metodo=Venta.FIADO).aggregate(t=models.Sum("total"))["t"] or Decimal("0")
        pagado = self.movimientos_caja.filter(origen=CajaMovimiento.ABONO).aggregate(t=models.Sum("monto"))["t"] or Decimal("0")
        return fiado - pagado


class Venta(models.Model):
    """Una pieza que salió del inventario hacia un cliente, con su precio y forma de pago."""
    EFECTIVO = "EFECTIVO"
    QR = "QR"
    FIADO = "FIADO"
    METODOS = [(EFECTIVO, "Efectivo"), (QR, "QR"), (FIADO, "Fiado")]

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="ventas")
    # Si algún día se borra el producto, la venta conserva su nombre
    producto = models.ForeignKey(Producto, null=True, blank=True, on_delete=models.SET_NULL, related_name="ventas")
    producto_nombre = models.CharField(max_length=200)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    costo_unitario = models.DecimalField("costo unitario (precio de compra)", max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    metodo = models.CharField(max_length=10, choices=METODOS)
    # A qué caja va el dinero de esta venta (según la categoría del producto cuando se vendió):
    # categoría técnica -> caja técnico; las demás -> caja productos. Los pagos de una deuda siguen esta caja.
    caja = models.CharField(
        max_length=10, default="PRODUCTOS",
        choices=[("TECNICO", "Caja técnico"), ("PRODUCTOS", "Caja productos")],
    )
    movimiento = models.OneToOneField(
        Movimiento, null=True, blank=True, on_delete=models.SET_NULL, related_name="venta"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="ventas_realizadas"
    )
    nota = models.CharField(max_length=180, blank=True)
    fecha = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-fecha", "-pk"]

    def __str__(self):
        return f"{self.cantidad} × {self.producto_nombre} → {self.cliente}"

    @property
    def ganancia(self):
        return self.total - self.cantidad * self.costo_unitario


class CajaMovimiento(models.Model):
    """Cada vez que entra plata. Hay dos cajas separadas: la del técnico (reparaciones) y la de productos."""
    TECNICO = "TECNICO"
    PRODUCTOS = "PRODUCTOS"
    CAJAS = [(TECNICO, "Caja técnico"), (PRODUCTOS, "Caja productos")]

    EFECTIVO = "EFECTIVO"
    QR = "QR"
    METODOS = [(EFECTIVO, "Efectivo"), (QR, "QR")]

    REPARACION = "REPARACION"
    VENTA = "VENTA"
    ABONO = "ABONO"   # pago (total o parcial) de una deuda por fiado
    ORIGENES = [(REPARACION, "Reparación"), (VENTA, "Venta"), (ABONO, "Pago de deuda")]

    caja = models.CharField(max_length=10, choices=CAJAS)
    metodo = models.CharField(max_length=10, choices=METODOS, default=EFECTIVO)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    concepto = models.CharField(max_length=200)
    origen = models.CharField(max_length=12, choices=ORIGENES)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimientos_caja"
    )
    fecha = models.DateTimeField(default=timezone.now)
    reparacion = models.ForeignKey(
        Reparacion, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimientos_caja"
    )
    venta = models.ForeignKey(Venta, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimientos_caja")
    cliente = models.ForeignKey(
        Cliente, null=True, blank=True, on_delete=models.SET_NULL, related_name="movimientos_caja"
    )

    class Meta:
        ordering = ["-fecha", "-pk"]
        indexes = [models.Index(fields=["caja", "fecha"])]

    def __str__(self):
        return f"{self.get_caja_display()}: Bs {self.monto} ({self.concepto})"
