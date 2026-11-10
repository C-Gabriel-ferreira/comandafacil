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


SERVER_PROBLEM = ""  # motivo de o acesso pelo navegador não ter iniciado (mostrado ao usuário)


def _log_server_problem(text):
    """Guarda o motivo em servidor.log (na pasta de dados), porque a janela do PDV não tem console."""
    global SERVER_PROBLEM
    SERVER_PROBLEM = text
    try:
        with open(dbm.DATA_DIR / "servidor.log", "a", encoding="utf-8") as f:
            f.write(f"{dbm.now()}  {text}\n")
    except OSError:
        pass


def start_phone_server():
    """Servidor web (navegador e celulares). Devolve (servidor, url) ou (None, None) se indisponível.

    Se a porta padrão estiver ocupada (ex.: outra cópia do programa aberta), usa a próxima livre."""
    from comandafacil import create_app

    try:
        from waitress import create_server

        app = create_app()
    except Exception as exc:  # o PDV continua funcionando sem o servidor
        _log_server_problem(f"Não foi possível preparar o servidor: {type(exc).__name__}: {exc}")
        return None, None
    for port in range(PORT, PORT + 10):
        if port_in_use(port):
            continue
        try:
            server = create_server(app, host="0.0.0.0", port=port, threads=8)
        except Exception as exc:  # porta bloqueada pelo firewall/antivírus, por exemplo
            _log_server_problem(f"Porta {port} indisponível: {type(exc).__name__}: {exc}")
            continue
        threading.Thread(target=server.run, daemon=True, name="web").start()
        return server, f"http://{lan_ip()}:{port}"
    if not SERVER_PROBLEM:
        _log_server_problem(f"As portas {PORT} a {PORT + 9} já estão em uso (talvez o programa já esteja aberto).")
    return None, None


def _saved_dark() -> bool:
    from PySide6.QtCore import QSettings
    return str(QSettings("ComandaFacil", "Desktop").value("dark2", "false")).lower() == "true"


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
    app.setStyleSheet(stylesheet(_saved_dark()))

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
            if server is None and SERVER_PROBLEM:
                dlg.warn(win, "O acesso pelo navegador e pelos celulares não iniciou, mas o PDV funciona normalmente.\n\n"
                              f"Motivo: {SERVER_PROBLEM}\n\nDetalhes em: {dbm.DATA_DIR / 'servidor.log'}", "Servidor web")
            app.exec()
            if not win.logged_out:
                return 0
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    sys.exit(run())
