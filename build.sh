#!/usr/bin/env bash
# Comando de build de Render: instala, junta estáticos y aplica migraciones en Neon.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
