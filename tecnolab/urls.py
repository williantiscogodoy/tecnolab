from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from inventario import salud

urlpatterns = [
    path("salud/", salud.salud, name="salud"),
    path("salud/db/", salud.salud_db, name="salud_db"),
    path("admin/", admin.site.urls),
    path("", include("inventario.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
