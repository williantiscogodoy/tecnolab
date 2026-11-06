from django.conf import settings
from django.db import models


class Categoria(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    es_tecnica = models.BooleanField(
        "Categoría técnica",
        default=False,
        help_text="Sus productos aparecen primero en el panel del técnico.",
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

    def __str__(self):
        return f"{self.nombre} ({self.sku})" if self.sku else self.nombre

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


class ReparacionPieza(models.Model):
    """Pieza del inventario usada en una reparación (genera una salida automática)."""
    reparacion = models.ForeignKey(Reparacion, on_delete=models.CASCADE, related_name="piezas")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    movimiento = models.OneToOneField(
        Movimiento, null=True, blank=True, on_delete=models.SET_NULL, related_name="pieza_reparacion"
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.cantidad} × {self.producto.nombre}"