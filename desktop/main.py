"""Ponto de entrada do programa:  python comandafacil_desktop.py

Abre o PDV (Qt) e, em segundo plano, o servidor web para os celulares dos garçons — os dois
usam o mesmo banco de dados.
"""
import os
import socket
import sys
import threading
from pathlib import Path

from comandafacil import db as dbm

PORT = int(os.environ.get("COMANDAFACIL_PORT") or os.environ.get("PORT") or 5000)


def _prepare_environment():
    if getattr(sys, "frozen", False):
        for name in ("stdout", "stderr"):  # janela sem console: evita erro ao escrever em stdout/stderr
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, "w"))
        if not os.environ.get("COMANDAFACIL_DATA_DIR"):
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ComandaFacil" / "dados"
            base.mkdir(parents=True, exist_ok=True)
            os.environ["COMANDAFACIL_DATA_DIR"] = str(base)
            dbm.configure(base)


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


def start_phone_server():
    """Servidor web para celulares/tablets. Devolve (servidor, url) ou (None, None) se indisponível."""
    if port_in_use(PORT):
        return None, None
    try:
        from waitress import create_server

        from comandafacil import create_app
        server = create_server(create_app(), host="0.0.0.0", port=PORT, threads=8)
    except Exception as exc:  # o PDV continua funcionando sem o servidor
        print("Servidor para celulares indisponível:", exc, file=sys.stderr)
        return None, None
    threading.Thread(target=server.run, daemon=True, name="web").start()
    return server, f"http://{lan_ip()}:{PORT}"


def run() -> int:
    if "--selftest" in sys.argv:  # teste automático da interface (CI)
        from .selftest import run_selftest
        i = sys.argv.index("--selftest")
        return run_selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest")

    from PySide6.QtWidgets import QApplication

    from . import dialogs as dlg
    from .controller import Controller
    from .main_window import MainWindow
    from .theme import stylesheet

    _prepare_environment()
    app = QApplication(sys.argv)
    app.setApplicationName("ComandaFácil")
    app.setStyleSheet(stylesheet(True))

    try:
        dbm.init_db()
    except Exception as exc:  # banco corrompido/versão nova: avisa em vez de fechar em silêncio
        dlg.warn(None, f"Não foi possível abrir o banco de dados:\n{exc}", "Erro")
        return 1
    dbm.start_backup_thread()
    server, url = start_phone_server()
    ctrl = Controller(dbm.connect())

    try:
        while True:
            gate = dlg.LoginDialog(ctrl) if ctrl.has_users() else dlg.SetupDialog(ctrl)
            if gate.exec() != dlg.ACCEPTED:
                return 0
            win = MainWindow(ctrl, url)
            win.showMaximized()
            app.exec()
            if not win.logged_out:
                return 0
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    sys.exit(run())
