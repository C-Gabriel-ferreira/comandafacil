"""Aba Cozinha: pedidos enviados, com Iniciar / Pronto / Entregue (atualiza sozinha)."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from comandafacil.clock import minutes_since

from . import dialogs as dlg

COLS = 3
STATE_TEXT = {"enviado": "Na fila", "preparando": "Preparando", "pronto": "Pronto"}
STATE_COLOR = {"enviado": "#f59e0b", "preparando": "#38bdf8", "pronto": "#22c55e"}


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


class KitchenView(QWidget):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        head = QHBoxLayout()
        t = QLabel("Cozinha")
        t.setObjectName("title")
        self.count = QLabel("")
        self.count.setObjectName("subtitle")
        head.addWidget(t)
        head.addWidget(self.count)
        head.addStretch(1)
        lay.addLayout(head)
        self.inner = QWidget()
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(12)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(self.inner)
        lay.addWidget(area, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(10000)
        self.refresh()

    def refresh(self, *_):
        _clear(self.grid)
        tickets = self.ctrl.kitchen_tickets()
        self.count.setText(f"{len(tickets)} pedido(s) em andamento")
        for i, tk in enumerate(tickets):
            self.grid.addWidget(self._card(tk), i // COLS, i % COLS)
        if not tickets:
            self.grid.addWidget(QLabel("Nenhum pedido na cozinha. Use “Enviar à cozinha” na comanda."), 0, 0)

    def _card(self, tk):
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        title = tk["table_name"] if tk["kind"] == "mesa" else f"{tk['kind'].capitalize()} #{tk['order_id']}"
        if tk["customer_name"]:
            title += f" · {tk['customer_name']}"
        h = QLabel(f"<b>{title}</b>")
        h.setStyleSheet("font-size:15px;")
        mins = minutes_since(tk["since"]) if tk["since"] else 0
        st = QLabel(f"{STATE_TEXT[tk['state']]} · há {mins} min")
        st.setStyleSheet(f"color:{STATE_COLOR[tk['state']]};font-weight:700;")
        v.addWidget(h)
        v.addWidget(st)
        for it in tk["items"]:
            line = f"{it['quantity']}x {it['product_name']}"
            if it["note"]:
                line += f"\n    ↳ {it['note']}"
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            v.addWidget(lbl)
        row = QHBoxLayout()
        steps = {"enviado": [("Iniciar", "iniciar"), ("Pronto", "pronto")],
                 "preparando": [("Pronto", "pronto")], "pronto": [("Entregue", "entregue")]}[tk["state"]]
        for text, step in steps:
            b = QPushButton(text)
            if step in ("pronto", "entregue"):
                b.setObjectName("primary")
            b.clicked.connect(lambda _=False, oid=tk["order_id"], s=step: self.step(oid, s))
            row.addWidget(b)
        v.addLayout(row)
        return f

    def step(self, order_id, step):
        ok, _ = dlg.attempt(self, self.ctrl.kitchen_step, order_id, step)
        self.refresh()
