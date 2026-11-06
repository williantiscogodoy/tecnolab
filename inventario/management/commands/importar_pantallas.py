"""Importa las pantallas del sistema anterior (volcado .sql de phpMyAdmin) al inventario.

Cada nombre se guarda con el prefijo "Pantalla " (ej.: "mi 10 t lite" -> "Pantalla mi 10 t lite")
dentro de la categoría "Pantallas". Es seguro correrlo varias veces: no duplica productos.

Uso:
    python manage.py importar_pantallas ruta/al/archivo.sql --dry-run   # solo muestra lo que haría
    python manage.py importar_pantallas ruta/al/archivo.sql             # importa
    python manage.py importar_pantallas ruta/al/archivo.sql --precio-como compra
"""
import re
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventario.models import Categoria, Producto

FILA = re.compile(r"\((\d+),\s*'((?:[^'\\]|\\.|'')*)',\s*'((?:[^'\\]|\\.|'')*)'\)")
FECHA = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")
PREFIJO = "Pantalla"


def leer_texto(ruta):
    datos = Path(ruta).read_bytes()
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return datos.decode("latin-1")


def limpiar_nombre(nombre):
    nombre = nombre.replace("\\'", "'").replace("''", "'")
    return re.sub(r"\s+", " ", nombre).strip()


class Command(BaseCommand):
    help = "Importa las pantallas del .sql anterior con el prefijo 'Pantalla' en el nombre."

    def add_arguments(self, parser):
        parser.add_argument("sql", help="Ruta del archivo .sql exportado de phpMyAdmin")
        parser.add_argument("--dry-run", action="store_true", help="No guarda nada, solo muestra el resultado")
        parser.add_argument(
            "--precio-como", choices=["venta", "compra"], default="venta",
            help="Dónde guardar el precio del sistema anterior (por defecto: venta)",
        )
        parser.add_argument("--categoria", default="Pantallas", help="Categoría donde se guardan")

    def handle(self, *args, **opts):
        if not Path(opts["sql"]).is_file():
            raise CommandError(f"No encuentro el archivo: {opts['sql']}")

        filas = FILA.findall(leer_texto(opts["sql"]))
        if not filas:
            raise CommandError("No se encontraron filas INSERT de la tabla productos en el archivo.")

        existentes = {n.lower() for n in Producto.objects.values_list("nombre", flat=True)}
        nuevos, omitidos, sin_precio = [], 0, []
        vistos = set()

        for _id, nombre, precio_raw in filas:
            base = limpiar_nombre(nombre)
            if not base:
                continue
            if base.lower().startswith(PREFIJO.lower()):
                base = base[len(PREFIJO):].strip()
            final = f"{PREFIJO} {base}"
            clave = final.lower()
            if clave in existentes or clave in vistos:
                omitidos += 1
                continue
            vistos.add(clave)

            m = re.match(r"\s*(\d+(?:[.,]\d+)?)", precio_raw)
            if not m:
                sin_precio.append(final)
                continue
            precio = Decimal(m.group(1).replace(",", "."))
            f = FECHA.search(precio_raw)
            desc = f"Precio del sistema anterior actualizado el {f.group(1)}." if f else ""
            nuevos.append((final, precio, desc))

        self.stdout.write(f"Filas leídas: {len(filas)} · nuevas: {len(nuevos)} · ya existían/repetidas: {omitidos} · sin precio: {len(sin_precio)}")
        for n, p, _ in nuevos[:8]:
            self.stdout.write(f"   {n}  ->  Bs {p}")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run: no se guardó nada."))
            return

        con_venta = opts["precio_como"] == "venta"
        with transaction.atomic():
            cat, _ = Categoria.objects.get_or_create(nombre=opts["categoria"], defaults={"es_tecnica": True})
            Producto.objects.bulk_create([
                Producto(
                    nombre=n, categoria=cat, descripcion=d,
                    precio_venta=p if con_venta else Decimal("0"),
                    precio_compra=Decimal("0") if con_venta else p,
                    stock=0,
                )
                for n, p, d in nuevos
            ])
        self.stdout.write(self.style.SUCCESS(f"Listo: {len(nuevos)} pantallas importadas en la categoría «{opts['categoria']}»."))
        if sin_precio:
            self.stdout.write(self.style.WARNING("Sin precio válido (no importadas): " + ", ".join(sin_precio)))
