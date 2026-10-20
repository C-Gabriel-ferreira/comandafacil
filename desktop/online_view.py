"""Aba Pedidos online: recebe os pedidos do cardápio digital, aceita/recusa e acompanha a entrega.

A busca na nuvem roda em segundo plano (thread) a cada 15 s, então a tela nunca trava se a internet cair.
"""
import threading
from urllib.parse import quote

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from comandafacil import db as dbm, online
from comandafacil.money import cents_to_input, format_brl

from . import dialogs as dlg

POLL_MS = 15000
STATUS_COLOR = {"novo": "#f59e0b", "aceito": "#38bdf8", "preparo": "#38bdf8", "pronto": "#22c55e",
                "saiu": "#a78bfa", "entregue": "#94a3b8", "recusado": "#ef4444", "cancelado": "#ef4444"}


class _Worker:
    """Executa um ciclo de sincronização fora da thread da tela, com conexão própria ao banco."""

    def __init__(self, transport):
        self.transport = transport
        self.running = False
        self.result = None   # ("ok", dict) | ("erro", texto)

    def start(self):
        if self.running:
            return
        self.running, self.result = True, None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        conn = None
        try:
            conn = dbm.connect()
            res = online.sync(conn, online.CloudClient.from_conn(conn, transport=self.transport))
            self.result = ("ok", res)
        except Exception as e:  # noqa: BLE001 — qualquer falha vira aviso discreto, nunca derruba o PDV
            self.result = ("erro", str(e) or e.__class__.__name__)
        finally:
            if conn is not None:
                conn.close()
            self.running = False


class OnlineConfigDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Pedidos online — configuração")
        self.setMinimumWidth(520)
        g = ctrl.setting
        lay = QVBoxLayout(self)
        hint = QLabel("Cole o código de ativação que a HelpTech enviou para a sua loja. "
                      "Ele liga o PDV ao seu cardápio digital." if not ctrl.online_configured() else
                      f"Loja ativada: {g('online_slug')}. Para trocar, cole um novo código.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        form = QFormLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("CF1.…")
        form.addRow("Código de ativação", self.code)
        self.delivery = QCheckBox("Aceitar entrega")
        self.delivery.setChecked(g("online_delivery", "1") == "1")
        self.pickup = QCheckBox("Aceitar retirada no balcão")
        self.pickup.setChecked(g("online_pickup", "1") == "1")
        self.fee = QLineEdit(cents_to_input(int(g("online_fee_cents", "0") or 0)))
        self.minimum = QLineEdit(cents_to_input(int(g("online_min_cents", "0") or 0)))
        self.hours = QLineEdit(g("online_hours"))
        self.hours.setPlaceholderText("Ex.: Ter a dom, 18h às 23h")
        self.notice = QLineEdit(g("online_notice"))
        self.notice.setPlaceholderText("Ex.: Não aceitamos fiado")
        self.site = QLineEdit(g("online_site"))
        self.site.setPlaceholderText("https://seu-site.vercel.app")
        form.addRow(self.delivery)
        form.addRow(self.pickup)
        form.addRow("Taxa de entrega (R$)", self.fee)
        form.addRow("Pedido mínimo (R$)", self.minimum)
        form.addRow("Horário (texto)", self.hours)
        form.addRow("Aviso no cardápio", self.notice)
        form.addRow("Endereço do site", self.site)
        lay.addLayout(form)
        row, ok = dlg._buttons(self, "Salvar")
        ok.clicked.connect(self._save)
        lay.addLayout(row)

    def _save(self):
        fee, minimum = dlg.money_or_none(self.fee.text() or "0"), dlg.money_or_none(self.minimum.text() or "0")
        if fee is None or minimum is None:
            return dlg.warn(self, "Digite valores válidos, por exemplo 5,00.")
        if self.code.text().strip():
            ok, _ = dlg.attempt(self, self.ctrl.online_activate, self.code.text())
            if not ok:
                return
        ok, _ = dlg.attempt(self, self.ctrl.online_save_settings, self.delivery.isChecked(),
                            self.pickup.isChecked(), fee, minimum, self.hours.text(), self.notice.text(),
                            self.site.text())
        if ok:
            self.accept()


class OnlineView(QWidget):
    def __init__(self, ctrl, on_new=None, on_change=None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.on_new = on_new or (lambda n: None)
        self.on_change = on_change or (lambda: None)
        self.rows = []
        self.worker = _Worker(ctrl.cloud_transport)
        self.last_error = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)

        head = QHBoxLayout()
        t = QLabel("Pedidos online")
        t.setObjectName("title")
        self.state = QLabel("")
        self.state.setObjectName("subtitle")
        head.addWidget(t)
        head.addWidget(self.state)
        head.addStretch(1)
        self.btn_toggle = QPushButton("Receber pedidos")
        self.btn_toggle.setObjectName("primary")
        self.btn_publish = QPushButton("Publicar cardápio")
        self.btn_link = QPushButton("Copiar link")
        self.btn_cfg = QPushButton("Configurar")
        self.btn_toggle.clicked.connect(self.toggle_accepting)
        self.btn_publish.clicked.connect(self.publish)
        self.btn_link.clicked.connect(self.copy_link)
        self.btn_cfg.clicked.connect(self.configure)
        for b in (self.btn_toggle, self.btn_publish, self.btn_link, self.btn_cfg):
            head.addWidget(b)
        lay.addLayout(head)

        self.banner = QLabel("")
        self.banner.setObjectName("subtitle")
        self.banner.setWordWrap(True)
        lay.addWidget(self.banner)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["#", "Hora", "Cliente", "Tipo", "Total", "Pagamento", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.show_detail)
        lay.addWidget(self.table, 2)

        box = QFrame()
        box.setObjectName("card")
        v = QVBoxLayout(box)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(210)
        v.addWidget(self.detail)
        actions = QHBoxLayout()
        self.btn_accept = QPushButton("✔ Aceitar e enviar à cozinha")
        self.btn_accept.setObjectName("primary")
        self.btn_reject = QPushButton("✖ Recusar")
        self.btn_reject.setObjectName("danger")
        self.btn_ready = QPushButton("Pronto")
        self.btn_out = QPushButton("🛵 Saiu para entrega")
        self.btn_print = QPushButton("🖨 Imprimir ficha")
        self.btn_wa = QPushButton("💬 Avisar no WhatsApp")
        for b, fn in ((self.btn_accept, self.accept_order), (self.btn_reject, self.reject_order),
                      (self.btn_ready, self.ready), (self.btn_out, self.dispatched),
                      (self.btn_print, self.print_ticket), (self.btn_wa, self.whatsapp)):
            b.clicked.connect(fn)
            actions.addWidget(b)
        v.addLayout(actions)
        lay.addWidget(box)

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.worker_start)
        self.poll.start(POLL_MS)
        self.collect = QTimer(self)
        self.collect.timeout.connect(self.worker_collect)
        self.collect.start(300)
        self.refresh()
        self.worker_start()

    # ----------------------------------------------------------------- sincronização
    def worker_start(self):
        if self.ctrl.user and self.ctrl.online_configured():
            self.worker.start()

    def worker_collect(self):
        res, self.worker.result = self.worker.result, None
        if not res:
            return
        kind, data = res
        if kind == "erro":
            self.last_error = data
        else:
            self.last_error = ""
            if data["new"]:
                beep = getattr(QApplication, "beep", None)
                if beep:
                    beep()
        self.refresh()

    # --------------------------------------------------------------------------- tela
    def refresh(self, *_):
        configured = self.ctrl.online_configured()
        accepting = self.ctrl.online_accepting()
        mgr = self.ctrl.is_manager
        self.btn_publish.setVisible(mgr and configured)
        self.btn_cfg.setVisible(mgr)
        self.btn_link.setVisible(configured)
        self.btn_toggle.setVisible(configured)
        self.btn_toggle.setText("⏸ Pausar pedidos" if accepting else "▶ Receber pedidos")
        if not configured:
            self.state.setText("● Não ativado")
            self.state.setStyleSheet("color:#94a3b8;font-weight:700;")
            self.banner.setText("Peça ao gerente para ativar: Configurar → colar o código de ativação.")
        else:
            self.state.setText("● Recebendo pedidos" if accepting else "● Pausado (clientes veem “loja fechada”)")
            self.state.setStyleSheet(f"color:{'#22c55e' if accepting else '#f59e0b'};font-weight:700;")
            self.banner.setText(("⚠ Sem conexão com a nuvem: " + self.last_error + " Tentando de novo…")
                                if self.last_error else "")
        sel = self._selected_code()
        self.rows = self.ctrl.online_orders()
        self.table.setRowCount(0)
        for o in self.rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [f"#{o['seq']}", o["received_at"][11:16], o["customer_name"],
                    "Entrega" if o["kind"] == "entrega" else "Retirada", format_brl(o["total_cents"]),
                    o["payment_method"], o["status_label"]]
            for c, val in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(val))
        if sel:
            for r, o in enumerate(self.rows):
                if o["code"] == sel:
                    self.table.selectRow(r)
        self.on_new(self.ctrl.online_new_count())
        self.show_detail()

    def _selected_code(self):
        idx = self.table.currentRow()
        return self.rows[idx]["code"] if 0 <= idx < len(self.rows) else None

    def _selected(self):
        code = self._selected_code()
        return next((o for o in self.rows if o["code"] == code), None)

    def show_detail(self):
        o = self._selected()
        for b in (self.btn_accept, self.btn_reject, self.btn_ready, self.btn_out, self.btn_print, self.btn_wa):
            b.setEnabled(o is not None)
        if not o:
            self.detail.setPlainText("Selecione um pedido na lista.")
            return
        st = o["status"]
        self.btn_accept.setEnabled(st == "novo")
        self.btn_reject.setEnabled(st == "novo")
        self.btn_ready.setEnabled(st in ("aceito", "preparo"))
        self.btn_out.setEnabled(st in ("aceito", "preparo", "pronto") and o["kind"] == "entrega")
        self.btn_wa.setEnabled(st != "novo")
        lines = [f"Pedido #{o['seq']} — {o['status_label']}   ({'ENTREGA' if o['kind'] == 'entrega' else 'RETIRADA'})",
                 f"Cliente: {o['customer_name']}   Tel.: {o['customer_phone']}"]
        if o["address"]:
            lines.append(f"Endereço: {o['address']}")
        lines.append("")
        for it in o["items"]:
            lines.append(f"{it['qty']}x {it['name']}   {format_brl(it['price_cents'] * it['qty'])}"
                         + (f"   ({it['note']})" if it.get("note") else ""))
        if o["note"]:
            lines += ["", f"Obs: {o['note']}"]
        lines += ["", f"Total {format_brl(o['total_cents'])}  (entrega {format_brl(o['delivery_fee_cents'])})",
                  f"Pagamento: {o['payment_method']}"
                  + (f" — troco para {format_brl(o['change_for_cents'])}" if o["change_for_cents"] else "")]
        self.detail.setPlainText("\n".join(lines))

    # --------------------------------------------------------------------------- ações
    def _sync_now(self):
        self.worker_start()

    def toggle_accepting(self):
        ok, _ = dlg.attempt(self, self.ctrl.online_set_accepting, not self.ctrl.online_accepting())
        self.refresh()
        if not ok:
            return

    def publish(self):
        ok, n = dlg.attempt(self, self.ctrl.online_publish)
        if ok:
            dlg.info(self, f"Cardápio publicado: {n} itens. Produtos sem estoque ou indisponíveis ficam ocultos.")

    def copy_link(self):
        link = online.public_link(self.ctrl.conn)
        if not link:
            return dlg.warn(self, "Informe o endereço do site em Configurar para gerar o link do cardápio.")
        QGuiApplication.clipboard().setText(link)
        dlg.info(self, f"Link copiado:\n{link}\n\nColoque na bio do Instagram e no status do WhatsApp.")

    def configure(self):
        if OnlineConfigDialog(self.ctrl, self).exec() == dlg.ACCEPTED:
            self.refresh()
            self.worker_start()

    def accept_order(self):
        o = self._selected()
        if not o:
            return
        ok, oid = dlg.attempt(self, self.ctrl.online_accept, o["code"])
        if ok:
            self.on_change()
            if dlg.confirm(self, "Pedido aceito e enviado à cozinha.\nImprimir a ficha agora?", "Aceito"):
                dlg.ReceiptDialog(self.ctrl.online_ticket(o["code"]), self, f"Pedido online #{o['seq']}").exec()
            self._sync_now()
        self.refresh()

    def reject_order(self):
        o = self._selected()
        if not o:
            return
        reason = dlg.ask_text(self, "Recusar pedido", "Motivo (o cliente verá):")
        if reason is None:
            return
        ok, _ = dlg.attempt(self, self.ctrl.online_reject, o["code"], reason)
        if ok:
            self._sync_now()
        self.refresh()

    def ready(self):
        self._simple(self.ctrl.online_ready)

    def dispatched(self):
        self._simple(self.ctrl.online_dispatched)

    def _simple(self, fn):
        o = self._selected()
        if o:
            ok, _ = dlg.attempt(self, fn, o["code"])
            if ok:
                self._sync_now()
            self.refresh()

    def print_ticket(self):
        o = self._selected()
        if o:
            dlg.ReceiptDialog(self.ctrl.online_ticket(o["code"]), self, f"Pedido online #{o['seq']}").exec()

    def whatsapp(self):
        o = self._selected()
        if o:
            phone, msg = self.ctrl.online_wa(o["code"])
            QDesktopServices.openUrl(QUrl(f"https://wa.me/{phone}?text={quote(msg)}"))
