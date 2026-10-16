"""ComandaFácil para Windows — janela de controle do servidor local (HelpTech).

Inicia o sistema no computador do caixa e mostra os endereços de acesso.
Sem tkinter (ou com --console), roda só no terminal.
"""
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

APP_NAME = "ComandaFácil"
PORT = int(os.environ.get("COMANDAFACIL_PORT") or os.environ.get("PORT") or 5000)

if getattr(sys, "frozen", False):  # janela sem console: evita erro ao escrever em stdout/stderr
    for _n in ("stdout", "stderr"):
        if getattr(sys, _n) is None:
            setattr(sys, _n, open(os.devnull, "w"))

if getattr(sys, "frozen", False) and not os.environ.get("COMANDAFACIL_DATA_DIR"):
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ComandaFacil" / "dados"
    base.mkdir(parents=True, exist_ok=True)
    os.environ["COMANDAFACIL_DATA_DIR"] = str(base)
    os.environ.setdefault("COMANDAFACIL_TZ", "America/Sao_Paulo")

from comandafacil import create_app, db as dbm  # noqa: E402  (depois de definir o ambiente)

VENDOR = dbm.VENDOR


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # não envia nada; só descobre a interface de rede
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_server():
    from waitress import create_server
    app = create_app()
    server = create_server(app, host="0.0.0.0", port=PORT, threads=8)
    threading.Thread(target=server.run, daemon=True, name="web").start()
    dbm.start_backup_thread()
    return server


def run_console():
    if port_in_use(PORT):
        print(f"{APP_NAME} já está aberto. Acesse http://127.0.0.1:{PORT}")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    server = start_server()
    print(f"{APP_NAME} · {VENDOR['name']}\nNeste computador: http://127.0.0.1:{PORT}\n"
          f"Celulares no mesmo Wi-Fi: http://{lan_ip()}:{PORT}\nPressione Ctrl+C para encerrar.")
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.close()


def run_window():
    import tkinter as tk
    from tkinter import messagebox

    local, lan = f"http://127.0.0.1:{PORT}", f"http://{lan_ip()}:{PORT}"
    if port_in_use(PORT):  # já aberto: só leva ao navegador
        webbrowser.open(local)
        return
    server = start_server()

    root = tk.Tk()
    root.title(f"{APP_NAME} · {VENDOR['name']}")
    root.geometry("460x330")
    root.resizable(False, False)
    pad = {"padx": 18}
    tk.Label(root, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(16, 0), **pad)
    tk.Label(root, text=f"desenvolvido pela {VENDOR['name']}", fg="#666").pack(anchor="w", **pad)
    tk.Label(root, text="● Sistema em funcionamento", fg="#15803d", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 6), **pad)
    tk.Label(root, text="Neste computador:").pack(anchor="w", **pad)
    tk.Label(root, text=local, font=("Consolas", 11, "bold")).pack(anchor="w", **pad)
    tk.Label(root, text="Celulares e tablets (mesmo Wi-Fi):").pack(anchor="w", pady=(8, 0), **pad)
    tk.Label(root, text=lan, font=("Consolas", 11, "bold")).pack(anchor="w", **pad)

    def copy_lan():
        root.clipboard_clear(); root.clipboard_append(lan)
        info.config(text="Endereço copiado.")

    def open_data():
        try:
            os.startfile(os.environ["COMANDAFACIL_DATA_DIR"])  # type: ignore[attr-defined]
        except Exception:
            pass

    row = tk.Frame(root); row.pack(anchor="w", pady=14, **pad)
    tk.Button(row, text="Abrir o sistema", command=lambda: webbrowser.open(local), width=16).pack(side="left")
    tk.Button(row, text="Copiar endereço", command=copy_lan, width=16).pack(side="left", padx=6)
    tk.Button(row, text="Pasta de dados", command=open_data, width=14).pack(side="left")
    info = tk.Label(root, text="", fg="#666"); info.pack(anchor="w", **pad)
    tk.Label(root, text=f"Suporte: {VENDOR['phone']} · {VENDOR['email']}", fg="#666").pack(side="bottom", pady=10)

    def on_close():
        if messagebox.askyesno(APP_NAME, "Encerrar o sistema? Os garçons ficarão sem acesso."):
            server.close()
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    webbrowser.open(local)
    root.mainloop()


if __name__ == "__main__":
    if "--console" in sys.argv:
        run_console()
    else:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            run_console()
        else:
            run_window()
