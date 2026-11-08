"""Sube a Cloudinary las imágenes que hoy están en la carpeta media/.

No toca la base de datos: conserva los nombres que ya tienen guardados
(p. ej. "productos/foto.png") y sube cada archivo con el identificador
"media/productos/foto", que es el que Django/Cloudinary buscan al armar la URL.
Es seguro correrlo varias veces (sobrescribe, no duplica).

Uso (con CLOUDINARY_URL definida en tu .env):
    python manage.py subir_media_a_cloudinary --dry-run   # solo lista lo que subiría
    python manage.py subir_media_a_cloudinary             # sube
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

EXTENSIONES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}


class Command(BaseCommand):
    help = "Sube las imágenes de media/ a Cloudinary conservando sus nombres."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Solo muestra qué subiría.")

    def handle(self, *args, **opciones):
        if not settings.USA_CLOUDINARY:
            raise CommandError("Falta CLOUDINARY_URL en el .env: no hay a dónde subir.")

        raiz = Path(settings.MEDIA_ROOT)
        archivos = sorted(
            p for p in raiz.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONES
        )
        if not archivos:
            self.stdout.write(f"No hay imágenes en {raiz}.")
            return

        import cloudinary.uploader  # se importa aquí: ya existe CLOUDINARY_URL en el entorno

        prefijo = settings.MEDIA_URL.strip("/")  # "media"
        subidas = fallidas = 0
        for ruta in archivos:
            nombre = ruta.relative_to(raiz).as_posix()  # productos/foto.png
            public_id = f"{prefijo}/{Path(nombre).with_suffix('').as_posix()}"
            if opciones["dry_run"]:
                self.stdout.write(f"[simulación] {nombre}  ->  {public_id}")
                continue
            try:
                resp = cloudinary.uploader.upload(
                    str(ruta),
                    public_id=public_id,
                    overwrite=True,
                    invalidate=True,
                    resource_type="image",
                    tags=["media"],
                )
            except Exception as e:  # sigue con las demás y avisa al final
                fallidas += 1
                self.stderr.write(self.style.ERROR(f"✗ {nombre}: {e}"))
                continue
            subidas += 1
            self.stdout.write(self.style.SUCCESS(f"✓ {nombre}") + f"  {resp.get('secure_url', '')}")

        if not opciones["dry_run"]:
            self.stdout.write(f"\nListo: {subidas} subidas, {fallidas} con error.")
            if fallidas:
                raise CommandError("Algunas imágenes fallaron; revisa los mensajes y vuelve a correrlo.")
