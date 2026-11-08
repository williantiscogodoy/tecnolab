# Tecnolab en Render + Neon + Cloudinary

## 1. Qué cambió en el proyecto

| Archivo | Para qué |
|---|---|
| `tecnolab/settings.py` | Lee `DATABASE_URL` (Neon) y `CLOUDINARY_URL`; WhiteNoise para estáticos; HTTPS y cookies seguras en producción; logs a consola. **Sin esas variables, todo sigue igual en tu PC** (SQLite + carpeta `media/`). |
| `inventario/salud.py` + `tecnolab/urls.py` | Archivo de salud: `/salud/` y `/salud/db/`. |
| `inventario/tests.py` | 5 pruebas del archivo de salud. |
| `inventario/management/commands/subir_media_a_cloudinary.py` | Sube tus imágenes actuales a Cloudinary sin tocar la base de datos. |
| `build.sh`, `render.yaml`, `.python-version`, `.gitattributes` | Despliegue en Render (Python 3.13, migraciones automáticas). |
| `.env.example`, `.gitignore` | Plantilla de variables; `media/` y el volcado de datos no se suben a GitHub. |

`requirements.txt` **no cambia**: ya tenía todo lo necesario.

## 2. Cuentas

**Neon:** crea un proyecto y copia la cadena de conexión (`postgresql://...`). Usa la que termina en `?sslmode=require`. Elige una región cercana a la que uses en Render.

**Cloudinary:** Dashboard → *API Keys* → copia la *API environment variable*, con forma `cloudinary://API_KEY:API_SECRET@CLOUD_NAME`.

## 3. Migrar tus datos (hazlo una sola vez, desde tu PC)

Con tu `.env` **todavía sin** `DATABASE_URL` ni `CLOUDINARY_URL`:

```bash
python -X utf8 manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude admin.logentry --exclude sessions --indent 2 -o datos_neon.json
```

Ahora agrega a tu `.env` las dos variables (`DATABASE_URL` de Neon y `CLOUDINARY_URL`) y corre:

```bash
python manage.py migrate
python -X utf8 manage.py loaddata datos_neon.json
python manage.py subir_media_a_cloudinary --dry-run
python manage.py subir_media_a_cloudinary
python manage.py runserver
```

Entra al sistema y revisa que los productos, el stock, la caja y las imágenes estén bien. Abre `http://127.0.0.1:8000/salud/db/`: debe decir `"almacenamiento": "cloudinary"` y `"db": "ok"`.

- Haz `loaddata` solo en una base de Neon **vacía** (recién migrada).
- `datos_neon.json` contiene los hashes de las contraseñas de tus usuarios: no lo subas a GitHub (ya está en `.gitignore`) ni lo compartas. Bórralo al terminar.
- Tus usuarios y contraseñas actuales siguen funcionando.

## 4. Subir a GitHub

```bash
git rm -r --cached media
git add .
git commit -m "Preparado para Render, Neon y Cloudinary"
git push
```

`git rm --cached` saca `media/` del repositorio pero **no borra tus archivos locales**.

## 5. Crear el servicio en Render

Opción rápida: *New → Blueprint* y elige el repositorio (usa `render.yaml`). Render te pedirá los valores de `DATABASE_URL`, `CLOUDINARY_URL` y `SALUD_TOKEN`.

Opción manual (*New → Web Service*):

- **Build Command:** `./build.sh`
- **Start Command:** `gunicorn tecnolab.wsgi:application --workers 2 --timeout 60`
- **Health Check Path:** `/salud/`
- **Variables de entorno:** `SECRET_KEY` (una nueva, larga y aleatoria; **no** uses la de tu `.env`), `DEBUG=False`, `PYTHON_VERSION=3.13.5`, `DATABASE_URL`, `CLOUDINARY_URL`, `SALUD_TOKEN` (opcional).

Render usa por defecto Python 3.14 en servicios nuevos; por eso se fija 3.13, que es la versión con la que desarrollaste.

## 6. El archivo de salud y la app que se duerme

| URL | Qué hace | Toca Neon |
|---|---|---|
| `/salud/` | Responde `ok`. Sin login. Es la que debes llamar cada pocos minutos. | No |
| `/salud/db/?token=TU_TOKEN` | Hace un `SELECT 1`, dice si las imágenes van a Cloudinary y cuánto tardó la base. | Sí |

La respuesta incluye `activa_hace_s`: si es un número chico, la app acaba de despertar o reiniciarse.

**Cómo mantenerla despierta:** Render duerme los servicios gratis tras 15 minutos sin tráfico y tarda alrededor de un minuto en despertar. Crea un monitor gratuito en [cron-job.org](https://cron-job.org) o UptimeRobot que llame a `https://TU-APP.onrender.com/salud/` **cada 10 minutos**.

**Por qué `/salud/` no toca la base:** el plan gratis de Neon incluye 100 horas de cómputo al mes y su cómputo mínimo es 0.25 CU. Mantenerlo despierto las 24 horas (~730 h × 0.25 = ~182 CU-h) superaría ese tope. Neon se suspende solo a los 5 minutos y despierta en unos cientos de milisegundos con la primera consulta, así que déjalo dormir.

**Horas de Render:** el plan gratis da 750 horas al mes por cuenta. Una sola app despierta las 24 horas usa ~720. Funciona con un único servicio, pero te deja casi sin margen. Si tu negocio solo usa el sistema de día, programa el monitor en horario de atención (por ejemplo 07:00 a 21:00, hora de La Paz): gastas la mitad de las horas.

Esto es un parche, no una garantía: si el monitor falla, la app vuelve a dormirse. La solución definitiva es un servicio de pago (el plan *Starter* desde 7 USD/mes no se duerme).

## 7. Verificación final

1. `https://TU-APP.onrender.com/salud/` responde `{"estado": "ok", ...}`.
2. `https://TU-APP.onrender.com/salud/db/?token=...` muestra `"almacenamiento": "cloudinary"`, `"db": "ok"`, `"motor": "postgresql"`.
3. Inicia sesión, abre un producto con imagen y sube una nueva: debe aparecer en tu panel de Cloudinary.
4. Si algo falla, mira la pestaña **Logs** de Render.

## 8. Pendiente (no incluido a propósito)

- **HSTS** no está activado: es difícil de revertir si cambias de dominio. Actívalo cuando tengas el dominio definitivo.
- **Correos** siguen saliendo a consola (dejaste Brevo para después).
- Los límites gratis de Cloudinary no los verifiqué; míralos en tu panel.
