from django import forms
from django.core.files.uploadedfile import UploadedFile

from .models import Categoria, Movimiento, Producto, ProductoImagen, Reparacion

MAX_IMAGEN = 5 * 1024 * 1024  # 5 MB por imagen
MAX_REFERENCIAS = 8           # imágenes de referencia por producto


def _num(**extra):
    attrs = {"class": "input", "min": "0"}
    attrs.update(extra)
    return forms.NumberInput(attrs=attrs)


# ============================== Productos ==============================
class ProductoForm(forms.ModelForm):
    precio_compra = forms.DecimalField(
        label="Precio de compra", min_value=0, max_digits=10, decimal_places=2,
        widget=_num(step="0.01", inputmode="decimal", placeholder="0.00"),
    )
    precio_venta = forms.DecimalField(
        label="Precio de venta", min_value=0, max_digits=10, decimal_places=2,
        widget=_num(step="0.01", inputmode="decimal", placeholder="0.00"),
    )
    quitar_imagen = forms.BooleanField(required=False, label="Quitar imagen actual")

    class Meta:
        model = Producto
        fields = [
            "nombre", "sku", "categoria", "descripcion",
            "precio_compra", "precio_venta", "stock", "stock_minimo",
            "activo", "imagen",
        ]
        labels = {
            "nombre": "Nombre",
            "sku": "SKU (opcional)",
            "categoria": "Categoría",
            "descripcion": "Descripción",
            "stock": "Stock inicial",
            "stock_minimo": "Stock mínimo",
        }
        help_texts = {
            "sku": "Déjalo vacío si el producto no tiene código.",
            "stock_minimo": "Te avisaremos al llegar a esta cantidad.",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. Pantalla Samsung A12", "autofocus": True}),
            "sku": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. PAN-0001", "autocapitalize": "characters"}),
            "categoria": forms.Select(attrs={"class": "input"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "Marca, modelo, compatibilidad… (opcional)"}),
            "stock": _num(),
            "stock_minimo": _num(),
            "imagen": forms.FileInput(attrs={"accept": "image/*", "aria-label": "Imagen principal del producto"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].queryset = Categoria.objects.order_by("nombre")
        self.fields["categoria"].empty_label = "Selecciona una categoría"

        if self.instance.pk:
            # Al editar, el stock solo cambia con movimientos para conservar el historial
            self.fields["stock"].label = "Stock actual"
            self.fields["stock"].disabled = True
            self.fields["stock"].help_text = ""
        else:
            self.fields["stock"].help_text = "Se registra como una entrada de inventario."

    def clean_sku(self):
        # Sin SKU se guarda como NULL para que varios productos puedan no tenerlo
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        return sku or None

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        # Solo valida archivos recién subidos (no toca el almacenamiento para los existentes)
        if isinstance(imagen, UploadedFile) and imagen.size > MAX_IMAGEN:
            raise forms.ValidationError("La imagen supera los 5 MB. Elige una más liviana.")
        return imagen

    def clean(self):
        datos = super().clean()
        nuevas = self.files.getlist("referencias")
        if nuevas:
            quitar = {int(i) for i in self.data.getlist("quitar_ref") if i.isdigit()}
            actuales = self.instance.imagenes.exclude(pk__in=quitar).count() if self.instance.pk else 0
            if actuales + len(nuevas) > MAX_REFERENCIAS:
                raise forms.ValidationError(
                    f"Máximo {MAX_REFERENCIAS} imágenes de referencia por producto."
                )
            validador = forms.ImageField()
            for archivo in nuevas:
                if archivo.size > MAX_IMAGEN:
                    raise forms.ValidationError(f"“{archivo.name}” supera los 5 MB.")
                try:
                    validador.clean(archivo)
                except forms.ValidationError:
                    raise forms.ValidationError(f"“{archivo.name}” no es una imagen válida.")
        return datos

    def save(self, commit=True):
        producto = super().save(commit=False)
        sube_nueva = bool(self.files.get("imagen"))
        if self.cleaned_data.get("quitar_imagen") and not sube_nueva and producto.imagen:
            producto.imagen.delete(save=False)
            producto.imagen = None
        if commit:
            producto.save()
        return producto

    def guardar_referencias(self, producto):
        """Borra las imágenes de referencia marcadas y guarda las nuevas (llamar tras guardar el producto)."""
        ids = [int(i) for i in self.data.getlist("quitar_ref") if i.isdigit()]
        if ids:
            for ref in producto.imagenes.filter(pk__in=ids):
                ref.imagen.delete(save=False)
                ref.delete()
        for archivo in self.files.getlist("referencias"):
            ProductoImagen.objects.create(producto=producto, imagen=archivo)


# ============================== Categorías ==============================
class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "es_tecnica"]

    def clean_nombre(self):
        nombre = " ".join(self.cleaned_data["nombre"].split())
        existe = (
            Categoria.objects.filter(nombre__iexact=nombre)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if existe:
            raise forms.ValidationError("Ya existe una categoría con ese nombre.")
        return nombre


# ============================== Movimientos ==============================
class MovimientoForm(forms.ModelForm):
    cantidad = forms.IntegerField(
        label="Cantidad", min_value=1,
        error_messages={"min_value": "La cantidad debe ser al menos 1."},
        widget=forms.NumberInput(attrs={"class": "input", "min": "1", "placeholder": "0", "inputmode": "numeric"}),
    )

    class Meta:
        model = Movimiento
        fields = ["producto", "tipo", "cantidad", "nota"]
        labels = {"producto": "Producto", "tipo": "Tipo de movimiento", "nota": "Nota"}
        widgets = {
            "nota": forms.TextInput(attrs={
                "class": "input", "maxlength": 200,
                "placeholder": "Ej. Compra a proveedor, venta en mostrador… (opcional)",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["producto"].queryset = Producto.objects.filter(activo=True).order_by("nombre")
        self.fields["producto"].empty_label = "Selecciona un producto"

    def clean(self):
        datos = super().clean()
        producto, tipo, cantidad = datos.get("producto"), datos.get("tipo"), datos.get("cantidad")
        if producto and tipo == Movimiento.SALIDA and cantidad and cantidad > producto.stock:
            self.add_error("cantidad", f"Solo hay {producto.stock} unidad(es) en stock.")
        return datos


# ============================== Reparaciones ==============================
class ReparacionForm(forms.ModelForm):
    class Meta:
        model = Reparacion
        fields = ["cliente", "telefono_cliente", "equipo", "trabajo", "notas"]
        labels = {
            "cliente": "Nombre del cliente",
            "telefono_cliente": "Teléfono de contacto (opcional)",
            "equipo": "Equipo que dejó",
            "trabajo": "Arreglo a realizar",
            "notas": "Notas (opcional)",
        }
        help_texts = {
            "trabajo": "Describe el arreglo. Si solo vas a cambiar una pieza, puedes dejarlo vacío y elegirla abajo.",
        }
        widgets = {
            "cliente": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. Juan Pérez", "autocomplete": "off", "autofocus": True}),
            "telefono_cliente": forms.TextInput(attrs={"class": "input", "type": "tel", "placeholder": "Ej. 70012345", "inputmode": "tel"}),
            "equipo": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. Samsung Galaxy A12, iPhone 11…"}),
            "trabajo": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "Ej. No carga, revisar puerto y limpiar"}),
            "notas": forms.Textarea(attrs={"class": "input", "rows": 2, "placeholder": "Estado del equipo al recibirlo, accesorios que dejó…"}),
        }

    def clean_cliente(self):
        return " ".join(self.cleaned_data["cliente"].split())


class ReparacionEstadoForm(forms.ModelForm):
    class Meta:
        model = Reparacion
        fields = ["estado", "notas"]
        widgets = {
            "estado": forms.Select(attrs={"class": "input"}),
            "notas": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "Avances, observaciones, a quién se entregó…"}),
        }