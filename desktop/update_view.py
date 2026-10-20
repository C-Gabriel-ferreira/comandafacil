"""Novidades e atualização do programa: botão na barra lateral + diálogo com o histórico e "Atualizar agora"."""
import os
import sys
import threading

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from comandafacil import updater

from . import dialogs as dlg

CHECK_MS = 6 * 60 * 60 * 1000


class UpdateChecker:
    """Procura versão nova em segundo plano (thread); a tela lê o resultado por um QTimer."""

    def __init__(self, ctrl, on_result):
        self.ctrl, self.on_result = ctrl, on_result
        self.running, self.result = False, None
        self.info, self.error = None, ""
        self.collect = QTimer()
        self.collect.timeout.connect(self._collect)
        self.collect.start(500)
        self.every = QTimer()
        self.every.timeout.connect(self.start)
        self.every.start(CHECK_MS)

    def start(self):
        if self.running or os.environ.get("COMANDAFACIL_NO_UPDATE_CHECK"):
            return
        self.running, self.result = True, None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.result = ("ok", self.ctrl.update_fetch())
        except Exception as e:  # noqa: BLE001
            self.result = ("erro", str(e))
        finally:
            self.running = False

    def _collect(self):
        res, self.result = self.result, None
        if res:
            if res[0] == "ok":
                self.info = res[1]
                self.ctrl.update_checked()
            self.error = res[1] if res[0] == "erro" else ""
            self.on_result(self.info)


class UpdateDialog(QDialog):
    def __init__(self, ctrl, info, parent=None):
        super().__init__(parent)
        self.ctrl, self.info = ctrl, info
        self.state = {"done": 0, "total": 0, "result": None, "running": False}
        self.setWindowTitle("Novidades e atualização")
        self.setMinimumSize(560, 520)
        lay = QVBoxLayout(self)
        self.head = QLabel(f"Versão instalada: {ctrl.app_version()}")
        self.head.setObjectName("title")
        lay.addWidget(self.head)
        self.sub = QLabel("")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        lay.addWidget(self.text, 1)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_close = QPushButton("Fechar")
        self.btn_close.clicked.connect(self.accept)
        self.btn_page = QPushButton("Ver na internet")
        self.btn_page.clicked.connect(self.open_page)
        self.btn_go = QPushButton("⬆ Atualizar agora")
        self.btn_go.setObjectName("primary")
        self.btn_go.clicked.connect(self.start_update)
        for b in (self.btn_close, self.btn_page, self.btn_go):
            row.addWidget(b)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.fill()

    def fill(self):
        info = self.info
        history = self.ctrl.update_history()
        if info:
            self.sub.setText(f"Nova versão disponível: {info['version']}. Seus dados e backups são mantidos; "
                             "o programa fecha, atualiza e abre de novo sozinho.")
            self.text.setPlainText(f"NOVIDADES DA {info['version']}\n{info['notes'] or '—'}\n\n"
                                   f"{'-' * 40}\nHISTÓRICO\n\n{history}")
        else:
            self.sub.setText("Você está com a versão mais recente.")
            self.text.setPlainText(history)
        self.btn_go.setVisible(bool(info))
        self.btn_go.setEnabled(self.ctrl.is_manager)
        self.btn_go.setToolTip("" if self.ctrl.is_manager else "Peça ao gerente para atualizar.")
        self.btn_page.setVisible(bool(info and info.get("page")))

    def open_page(self):
        QDesktopServices.openUrl(QUrl(self.info["page"]))

    def start_update(self):
        if not getattr(sys, "frozen", False):
            return dlg.info(self, "A atualização automática funciona no programa instalado (Windows). "
                                  "Aqui, use ‘Ver na internet’ para baixar o instalador.")
        if not dlg.confirm(self, f"Atualizar para a versão {self.info['version']} agora?\n\n"
                                 "O ComandaFácil será fechado e reaberto sozinho. Termine antes qualquer "
                                 "pagamento em andamento."):
            return
        ok, _ = dlg.attempt(self, self.ctrl.update_authorize, self.info)
        if not ok:
            return
        self.btn_go.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.sub.setText("Baixando a atualização…")
        self.state.update(done=0, total=0, result=None, running=True)

        def work():
            try:
                def prog(d, t):
                    self.state["done"], self.state["total"] = d, t
                self.state["result"] = ("ok", self.ctrl.update_fetch_installer(self.info, prog))
            except Exception as e:  # noqa: BLE001
                self.state["result"] = ("erro", str(e))
            finally:
                self.state["running"] = False
        threading.Thread(target=work, daemon=True).start()
        self.timer.start(200)

    def poll(self):
        st = self.state
        if st["total"]:
            self.bar.setRange(0, st["total"])
            self.bar.setValue(st["done"])
        if st["running"] or not st["result"]:
            return
        self.timer.stop()
        kind, data = st["result"]
        if kind == "erro":
            self.bar.setVisible(False)
            self.btn_close.setEnabled(True)
            self.btn_go.setEnabled(True)
            self.sub.setText("Não foi possível atualizar.")
            return dlg.warn(self, data)
        self.sub.setText("Instalando… o programa vai fechar e abrir de novo.")
        try:
            self.ctrl.update_launch(data)
        except Exception as e:  # noqa: BLE001
            self.btn_close.setEnabled(True)
            return dlg.warn(self, str(e))
        app = QApplication.instance()
        if app:
            app.quit()
