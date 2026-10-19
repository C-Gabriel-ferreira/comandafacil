"""Aba Relatórios (gerente): faturamento, ticket médio, formas de pagamento e produtos mais vendidos."""
import csv
from datetime import timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from comandafacil import clock, services
from comandafacil.money import cents_to_input, format_brl

from . import dialogs as dlg

PERIODS = ["Hoje", "Últimos 7 dias", "Últimos 30 dias", "Mês atual"]


def period_range(label):
    today = clock.today()
    if label == "Hoje":
        return today, today
    if label == "Últimos 7 dias":
        return today - timedelta(days=6), today
    if label == "Últimos 30 dias":
        return today - timedelta(days=29), today
    return today.replace(day=1), today


class ReportsView(QWidget):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        head = QHBoxLayout()
        t = QLabel("Relatórios")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch(1)
        self.period = QComboBox()
        self.period.addItems(PERIODS)
        self.period.currentIndexChanged.connect(self.refresh)
        csv_btn = QPushButton("Exportar CSV")
        csv_btn.clicked.connect(self.export_csv)
        head.addWidget(self.period)
        head.addWidget(csv_btn)
        lay.addLayout(head)
        self.cards = {}
        row = QHBoxLayout()
        for key in ("Faturamento", "Comandas", "Ticket médio", "Cancelamentos"):
            f = QFrame()
            f.setObjectName("card")
            v = QVBoxLayout(f)
            cap = QLabel(key)
            cap.setObjectName("muted")
            val = QLabel("—")
            val.setObjectName("bigvalue")
            v.addWidget(cap)
            v.addWidget(val)
            row.addWidget(f)
            self.cards[key] = val
        lay.addLayout(row)
        grid = QGridLayout()
        self.t_pay = self._table(["Forma de pagamento", "Comandas", "Valor"])
        self.t_kind = self._table(["Tipo", "Comandas", "Valor"])
        self.t_top = self._table(["Produto", "Qtd", "Valor"])
        self.t_hour = self._table(["Horário", "Comandas", "Valor"])
        for i, (title, tb) in enumerate((("Por forma de pagamento", self.t_pay), ("Por tipo", self.t_kind),
                                         ("Produtos mais vendidos", self.t_top), ("Por horário", self.t_hour))):
            box = QVBoxLayout()
            box.addWidget(QLabel(title))
            box.addWidget(tb)
            grid.addLayout(box, i // 2, i % 2)
        lay.addLayout(grid, 1)
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
    def _fill(table, rows):
        table.setRowCount(0)
        for vals in rows:
            r = table.rowCount()
            table.insertRow(r)
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, c, it)

    def refresh(self, *_):
        start, end = period_range(self.period.currentText())
        ok, rep = dlg.attempt(self, self.ctrl.report, start, end)
        if not ok:
            return
        self.cards["Faturamento"].setText(format_brl(rep["total"]))
        self.cards["Comandas"].setText(str(rep["orders"]))
        self.cards["Ticket médio"].setText(format_brl(rep["avg"]))
        self.cards["Cancelamentos"].setText(f"{rep['cancelled_n']}  ({format_brl(rep['cancelled_value'])})")
        self._fill(self.t_pay, [(r["label"], r["n"], format_brl(r["value"])) for r in rep["by_payment"]])
        self._fill(self.t_kind, [(services.KINDS.get(r["label"], r["label"]), r["n"], format_brl(r["value"]))
                                 for r in rep["by_kind"]])
        self._fill(self.t_top, [(r["label"], r["n"], format_brl(r["value"])) for r in rep["top_products"]])
        self._fill(self.t_hour, [(r["label"], r["n"], format_brl(r["value"])) for r in rep["by_hour"]])

    def export_csv(self):
        start, end = period_range(self.period.currentText())
        try:
            self.ctrl.require_manager()
        except services.BusinessError as exc:
            return dlg.warn(self, str(exc))
        path, _ = QFileDialog.getSaveFileName(self, "Exportar vendas", f"vendas_{start}_{end}.csv", "CSV (*.csv)")
        if not path:
            return
        rows = services.sales_rows(self.ctrl.conn, start, end)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Comanda", "Fechamento", "Tipo", "Mesa", "Cliente", "Subtotal", "Serviço", "Entrega",
                        "Total", "Pagamentos"])
            for o in rows:
                pays = "; ".join(f"{p.rsplit(' ', 1)[0]} {cents_to_input(int(p.rsplit(' ', 1)[1]))}"
                                 for p in (o["pays"] or "").split("; ") if p)
                w.writerow([o["id"], o["closed_at"], services.KINDS.get(o["kind"], o["kind"]),
                            o["table_name"] or "", o["customer_name"] or "",
                            cents_to_input(o["subtotal_cents"]), cents_to_input(o["service_cents"]),
                            cents_to_input(o["delivery_fee_cents"]), cents_to_input(o["total_cents"]), pays])
        dlg.info(self, f"{len(rows)} venda(s) exportada(s) para:\n{path}")
