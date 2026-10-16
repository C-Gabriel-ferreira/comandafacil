#!/usr/bin/env sh
# Linux/macOS: inicia o ComandaFácil (rede local). Uso: ./iniciar_servidor.sh
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
COMANDAFACIL_HOST="${COMANDAFACIL_HOST:-0.0.0.0}" COMANDAFACIL_PORT="${COMANDAFACIL_PORT:-5000}" exec python3 app.py
