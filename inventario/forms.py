from django import forms
from django.core.files.uploadedfile import UploadedFile

from .models import Categoria, Producto

MAX_IMAGEN = 5 * 1024 * 1024  # 5 MB


def _num(**extra):
    attrs = {"class": "input", "min": "0"}
    attrs.update(extra)
    return forms.NumberInput(attrs=attrs)


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
            "sku": "SKU",
            "categoria": "Categoría",
            "descripcion": "Descripción",
            "stock": "Stock inicial",
            "stock_minimo": "Stock mínimo",
        }
        help_texts = {
            "sku": "Código único del producto.",
            "stock_minimo": "Te avisaremos al llegar a esta cantidad.",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. Laptop Lenovo IdeaPad 3", "autofocus": True}),
            "sku": forms.TextInput(attrs={"class": "input", "placeholder": "Ej. LAP-0001", "autocapitalize": "characters"}),
            "categoria": forms.Select(attrs={"class": "input"}),
            "descripcion": forms.Textarea(attrs={"class": "input", "rows": 3, "placeholder": "Marca, modelo, características… (opcional)"}),
            "stock": _num(),
            "stock_minimo": _num(),
            "imagen": forms.FileInput(attrs={"accept": "image/*", "aria-label": "Imagen del producto"}),
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
        return self.cleaned_data["sku"].strip().upper()

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        # Solo valida archivos recién subidos (no toca el almacenamiento para los existentes)
        if isinstance(imagen, UploadedFile) and imagen.size > MAX_IMAGEN:
            raise forms.ValidationError("La imagen supera los 5 MB. Elige una más liviana.")
        return imagen

    def save(self, commit=True):
        producto = super().save(commit=False)
        sube_nueva = bool(self.files.get("imagen"))
        if self.cleaned_data.get("quitar_imagen") and not sube_nueva and producto.imagen:
            producto.imagen.delete(save=False)
            producto.imagen = None
        if commit:
            producto.save()
        return producto