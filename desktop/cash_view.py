"""Aba Caixa: abertura, sangria/suprimento, resumo do turno e fechamento com conferência."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from comandafacil import cash, receipt
from comandafacil.money import format_brl

from . import dialogs as dlg


def _fmt(iso):
    return f"{iso[8:10]}/{iso[5:7]} {iso[11:16]}" if iso else "—"


class CashView(QWidget):
    def __init__(self, ctrl, on_change=None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.on_change = on_change or (lambda: None)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        head = QHBoxLayout()
        t = QLabel("Caixa")
        t.setObjectName("title")
        self.state = QLabel()
        self.state.setObjectName("subtitle")
        head.addWidget(t)
        head.addWidget(self.state)
        head.addStretch(1)
        self.btn_open = QPushButton("Abrir caixa")
        self.btn_open.setObjectName("primary")
        self.btn_sup, self.btn_san = QPushButton("+ Suprimento"), QPushButton("− Sangria")
        self.btn_partial = QPushButton("Parcial")
        self.btn_close = QPushButton("Fechar caixa")
        self.btn_close.setObjectName("danger")
        self.btn_open.clicked.connect(self.open_cash)
        self.btn_sup.clicked.connect(lambda: self.movement("suprimento"))
        self.btn_san.clicked.connect(lambda: self.movement("sangria"))
        self.btn_partial.clicked.connect(self.partial)
        self.btn_close.clicked.connect(self.close_cash)
        for b in (self.btn_open, self.btn_sup, self.btn_san, self.btn_partial, self.btn_close):
            head.addWidget(b)
        lay.addLayout(head)

        self.cards = QGridLayout()
        self.cards.setSpacing(10)
        lay.addLayout(self.cards)
        self.card_labels = {}
        for i, key in enumerate(("Fundo de troco", "Total vendido", "Comandas / ticket médio",
                                 "Dinheiro esperado na gaveta")):
            f = QFrame()
            f.setObjectName("card")
            v = QVBoxLayout(f)
            cap = QLabel(key)
            cap.setObjectName("muted")
            val = QLabel("—")
            val.setObjectName("bigvalue")
            v.addWidget(cap)
            v.addWidget(val)
            self.cards.addWidget(f, 0, i)
            self.card_labels[key] = val

        mid = QHBoxLayout()
        self.pay_table = self._table(["Forma de pagamento", "Valor"])
        self.mov_table = self._table(["Hora", "Tipo", "Motivo", "Valor"])
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(QLabel("Vendas por forma de pagamento"))
        left.addWidget(self.pay_table)
        right.addWidget(QLabel("Sangrias e suprimentos"))
        right.addWidget(self.mov_table)
        mid.addLayout(left, 1)
        mid.addLayout(right, 2)
        lay.addLayout(mid, 1)
        lay.addWidget(QLabel("Histórico de caixas"))
        self.hist_table = self._table(["#", "Abertura", "Fechamento", "Fundo", "Esperado", "Contado", "Diferença"])
        self.hist_table.setMaximumHeight(190)
        lay.addWidget(self.hist_table)
        self.refresh()

    def _table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return t

    @staticmethod
    def _fill(table, rows, right_cols=()):
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            for c, v in enumerate(row):
                it = QTableWidgetItem(str(v))
                if c in right_cols:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, c, it)

    def refresh(self):
        sess, sm = self.ctrl.cash_summary()
        is_open = sess is not None
        mgr = self.ctrl.is_manager
        self.btn_open.setVisible(not is_open)
        self.btn_open.setEnabled(mgr)
        for b in (self.btn_sup, self.btn_san, self.btn_partial, self.btn_close):
            b.setVisible(is_open)
            b.setEnabled(mgr or b is self.btn_partial)
        if is_open:
            self.state.setText(f"● Aberto desde {_fmt(sess['opened_at'])}")
            self.state.setStyleSheet("color:#22c55e;font-weight:700;")
            L = self.card_labels
            L["Fundo de troco"].setText(format_brl(sm["opening"]))
            L["Total vendido"].setText(format_brl(sm["sales_total"]))
            L["Comandas / ticket médio"].setText(f"{sm['orders']}  /  {format_brl(sm['avg'])}")
            L["Dinheiro esperado na gaveta"].setText(format_brl(sm["expected_cash"]))
            self._fill(self.pay_table, [(m, format_brl(v)) for m, v in sm["by_method"].items()], (1,))
            self._fill(self.mov_table, [(_fmt(m["created_at"]), m["kind"].capitalize(), m["reason"],
                                         ("+" if m["kind"] == "suprimento" else "−") + format_brl(m["amount_cents"]))
                                        for m in cash.movements(self.ctrl.conn, sess["id"])], (3,))
        else:
            self.state.setText("● Fechado" + ("" if mgr else " — peça ao gerente para abrir"))
            self.state.setStyleSheet("color:#ef4444;font-weight:700;")
            for v in self.card_labels.values():
                v.setText("—")
            self._fill(self.pay_table, [])
            self._fill(self.mov_table, [])
        hist = []
        for s in cash.history(self.ctrl.conn, 12):
            closed = s["closed_at"] is not None
            diff = (s["counted_cents"] - s["expected_cents"]) if closed else None
            hist.append((s["id"], _fmt(s["opened_at"]), _fmt(s["closed_at"]) if closed else "aberto",
                         format_brl(s["opening_cents"]),
                         format_brl(s["expected_cents"]) if closed else "—",
                         format_brl(s["counted_cents"]) if closed else "—",
                         format_brl(diff) if closed else "—"))
        self._fill(self.hist_table, hist, (3, 4, 5, 6))

    # ----------------------------------------------------------------- ações
    def open_cash(self):
        d = dlg.MoneyDialog("Abrir caixa", "Fundo de troco (R$)", self, default_cents=0,
                            hint="Informe o dinheiro que está na gaveta no início do turno.")
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.open_cash, d.cents)
        if ok:
            self.refresh()
            self.on_change()

    def movement(self, kind):
        label = "Retirada de dinheiro (sangria)" if kind == "sangria" else "Reforço de troco (suprimento)"
        d = dlg.MoneyDialog(label, "Valor (R$)", self, with_reason=True)
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.cash_movement, kind, d.cents, d.reason_text)
        if ok:
            self.refresh()

    def partial(self):
        s = self.ctrl.cash_session()
        if s:
            dlg.ReceiptDialog(receipt.build_cash_report(self.ctrl.conn, s["id"]), self, "Caixa — parcial").exec()

    def close_cash(self):
        s = self.ctrl.cash_session()
        if not s:
            return
        d = dlg.MoneyDialog("Fechar caixa", "Dinheiro contado na gaveta (R$)", self, with_reason=True,
                            reason_label="Observação", hint="Conte o dinheiro físico. O sistema compara com o "
                            "valor esperado e mostra a diferença.")
        if d.exec() != dlg.ACCEPTED:
            return
        ok, diff = dlg.attempt(self, self.ctrl.close_cash, d.cents, d.reason_text)
        if not ok:
            return
        dlg.info(self, cash.difference_label(diff), "Caixa fechado")
        dlg.ReceiptDialog(receipt.build_cash_report(self.ctrl.conn, s["id"]), self,
                          "Fechamento de caixa").exec()
        self.refresh()
        self.on_change()
