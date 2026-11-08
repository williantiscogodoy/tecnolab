import os
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)

# *.onrender.com ya queda cubierto; agrega tu dominio propio (si lo tienes) en ALLOWED_HOSTS del entorno.
ALLOWED_HOSTS = [".onrender.com", "localhost", "127.0.0.1"]
ALLOWED_HOSTS += config("ALLOWED_HOSTS", default="", cast=Csv())

CSRF_TRUSTED_ORIGINS = ["https://*.onrender.com"]
CSRF_TRUSTED_ORIGINS += config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "inventario",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sirve /static/ en Render
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "tecnolab.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "tecnolab.wsgi.application"

# ------------------------------- Base de datos -------------------------------
# Local: SQLite. En Render: DATABASE_URL de Neon (con ?sslmode=require).
DATABASE_URL = config("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=600,
        # Neon suspende el cómputo tras 5 min sin uso: revisa la conexión antes
        # de reutilizarla para no fallar con una conexión muerta.
        conn_health_checks=True,
        # La URL "-pooler" de Neon usa PgBouncer, que no admite cursores del servidor.
        disable_server_side_cursors="-pooler" in DATABASE_URL,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/La_Paz"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Login
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "inicio"

# Correos: en consola por ahora (luego Brevo)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ------------------------- Archivos estáticos y media -------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"  # solo se usa si no hay Cloudinary (desarrollo local)

# Con CLOUDINARY_URL definida, las imágenes (ImageField) se guardan en Cloudinary,
# porque el disco de Render es efímero y se borra en cada reinicio o despliegue.
# Sin ella (tu PC) siguen yendo a la carpeta media/.
CLOUDINARY_URL = config("CLOUDINARY_URL", default="")
USA_CLOUDINARY = bool(CLOUDINARY_URL)
if USA_CLOUDINARY:
    # decouple lee el .env pero no lo exporta; la librería de Cloudinary lo lee del entorno.
    os.environ["CLOUDINARY_URL"] = CLOUDINARY_URL

STORAGES = {
    "default": {
        "BACKEND": (
            "cloudinary_storage.storage.MediaCloudinaryStorage"
            if USA_CLOUDINARY
            else "django.core.files.storage.FileSystemStorage"
        ),
    },
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedStaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

# ------------------------------- Seguridad -------------------------------
# Salud: token opcional para proteger /salud/db/ (ver inventario/salud.py)
SALUD_TOKEN = config("SALUD_TOKEN", default="")

if not DEBUG:
    # Render termina el HTTPS y avisa con este encabezado.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # El chequeo interno de Render llega por HTTP: que no lo redirija.
    SECURE_REDIRECT_EXEMPT = [r"^salud/"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Errores a la consola: es lo que muestra la pestaña "Logs" de Render.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        # Bots que prueban hosts raros: no llenar los logs de tracebacks.
        "django.security.DisallowedHost": {"handlers": [], "propagate": False},
        "tecnolab.salud": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
