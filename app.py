"""ComandaFácil — ponto de entrada web.

Variáveis de ambiente (todas opcionais):
  COMANDAFACIL_HOST          127.0.0.1 (padrão, só este PC) | 0.0.0.0 (rede local / servidor)
  COMANDAFACIL_PORT / PORT   porta (padrão 5000)
  COMANDAFACIL_DATA_DIR      pasta do banco, backups e secret.key (padrão: esta pasta)
  COMANDAFACIL_SECRET_KEY    chave de sessão (se ausente, é gerada e guardada em secret.key)
  COMANDAFACIL_HTTPS=1       site servido por HTTPS: cookies Secure + HSTS
  COMANDAFACIL_BEHIND_PROXY=1 atrás de proxy reverso (Caddy/Nginx): confia em X-Forwarded-*
"""
import os

from comandafacil import create_app
from comandafacil.db import start_backup_thread

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("COMANDAFACIL_HOST", "127.0.0.1")
    port = int(os.environ.get("COMANDAFACIL_PORT") or os.environ.get("PORT") or 5000)
    start_backup_thread()  # backup automático diário (mantém os 14 últimos)
    try:
        from waitress import serve  # servidor de produção (Windows/Linux)
        print(f"ComandaFácil em http://{host}:{port}  (waitress)")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        print("Aviso: 'waitress' não instalado; usando o servidor de desenvolvimento do Flask.")
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
