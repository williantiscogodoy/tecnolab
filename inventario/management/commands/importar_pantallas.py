"""Importa las pantallas del sistema anterior (volcado .sql de phpMyAdmin) al inventario.

Cada nombre se guarda con el prefijo "Pantalla " (ej.: "mi 10 t lite" -> "Pantalla mi 10 t lite")
dentro de la categoría "Pantallas". Es seguro correrlo varias veces: no duplica productos.

Uso:
    python manage.py importar_pantallas ruta/al/archivo.sql --dry-run   # solo muestra lo que haría
    python manage.py importar_pantallas ruta/al/archivo.sql             # importa
    python manage.py importar_pantallas ruta/al/archivo.sql --precio-como compra
    python manage.py importar_pantallas ruta/al/archivo.sql --stock 10 --stock-minimo 3
    python manage.py importar_pantallas ruta/al/archivo.sql --actualizar   # pone stock a las ya importadas que quedaron en 0
"""
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

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


def fecha_precio(texto):
    """'15/07/26' -> fecha con hora (None si no es una fecha válida)."""
    try:
        d, m, a = (int(x) for x in texto.split("/"))
        return timezone.make_aware(datetime(a + 2000 if a < 100 else a, m, d, 12, 0))
    except ValueError:
        return None


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
        parser.add_argument("--stock", type=int, default=5, help="Stock inicial de cada pantalla (por defecto: 5)")
        parser.add_argument(
            "--stock-minimo", type=int, default=2,
            help="Stock mínimo para avisar 'stock bajo' (por defecto: 2, para que con 5 no salgan todas como bajas)",
        )
        parser.add_argument(
            "--actualizar", action="store_true",
            help="También pone --stock y --stock-minimo a las pantallas ya importadas que tengan stock 0",
        )

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
            nuevos.append((final, precio, desc, fecha_precio(f.group(1)) if f else None))

        self.stdout.write(f"Filas leídas: {len(filas)} · nuevas: {len(nuevos)} · ya existían/repetidas: {omitidos} · sin precio: {len(sin_precio)}")
        for n, p, _, _f in nuevos[:8]:
            self.stdout.write(f"   {n}  ->  Bs {p}")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run: no se guardó nada."))
            return

        con_venta = opts["precio_como"] == "venta"
        with transaction.atomic():
            cat, _ = Categoria.objects.get_or_create(nombre=opts["categoria"], defaults={"es_tecnica": True})
            Producto.objects.bulk_create([
                Producto(
                    nombre=n, categoria=cat, descripcion=d, precio_venta_actualizado=fecha or timezone.now(),
                    precio_venta=p if con_venta else Decimal("0"),
                    precio_compra=Decimal("0") if con_venta else p,
                    stock=opts["stock"], stock_minimo=opts["stock_minimo"],
                )
                for n, p, d, fecha in nuevos
            ])
            actualizadas = 0
            if opts["actualizar"]:
                actualizadas = Producto.objects.filter(
                    categoria=cat, nombre__startswith=f"{PREFIJO} ", stock=0,
                ).update(stock=opts["stock"], stock_minimo=opts["stock_minimo"])
        self.stdout.write(self.style.SUCCESS(f"Listo: {len(nuevos)} pantallas importadas en la categoría «{opts['categoria']}» con stock {opts['stock']}."))
        if opts["actualizar"]:
            self.stdout.write(f"Ya existentes con stock 0 actualizadas: {actualizadas}")
        if sin_precio:
            self.stdout.write(self.style.WARNING("Sin precio válido (no importadas): " + ", ".join(sin_precio)))
