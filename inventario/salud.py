"""Endpoints de salud para Render y para el "despertador".

/salud/     Liviano: NO toca la base de datos. Es el que debes llamar cada pocos
            minutos para que Render no duerma la app. Así Neon puede seguir
            durmiendo (su plan gratis tiene un tope de horas de cómputo).
/salud/db/  Completo: hace un SELECT 1 contra la base y dice dónde guarda las
            imágenes. Úsalo a mano o 1-2 veces al día. Si defines SALUD_TOKEN,
            hay que pasarlo: /salud/db/?token=TU_TOKEN

Ninguno requiere login. Ninguno devuelve claves ni detalles de errores.
"""
import hmac
import logging
import os
import time

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

log = logging.getLogger("tecnolab.salud")

_ARRANQUE = time.monotonic()  # momento en que se despertó/arrancó este proceso


def _base():
    return {
        "estado": "ok",
        "hora": timezone.now().isoformat(timespec="seconds"),
        # Si este número es pequeño, la app acaba de despertar o de reiniciarse.
        "activa_hace_s": int(time.monotonic() - _ARRANQUE),
        "version": os.environ.get("RENDER_GIT_COMMIT", "local")[:7],
    }


@never_cache
@require_http_methods(["GET", "HEAD"])
def salud(request):
    """Chequeo liviano: responde sin consultar la base de datos."""
    return JsonResponse(_base())


@never_cache
@require_http_methods(["GET", "HEAD"])
def salud_db(request):
    """Chequeo completo: base de datos y almacenamiento de imágenes."""
    token = getattr(settings, "SALUD_TOKEN", "")
    if token and not hmac.compare_digest(request.GET.get("token", ""), token):
        return JsonResponse({"estado": "prohibido"}, status=403)

    datos = _base()
    datos["almacenamiento"] = "cloudinary" if settings.USA_CLOUDINARY else "local"
    datos["motor"] = connection.vendor  # "postgresql" en Neon, "sqlite" en tu PC

    inicio = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        # El detalle va al log de Render, no a la respuesta pública.
        log.exception("Falló el chequeo de base de datos")
        datos["estado"] = "error"
        datos["db"] = "error"
        return JsonResponse(datos, status=503)

    datos["db"] = "ok"
    # Si es alto (varios cientos de ms) Neon estaba dormido y acaba de despertar.
    datos["db_ms"] = round((time.perf_counter() - inicio) * 1000)
    return JsonResponse(datos)
