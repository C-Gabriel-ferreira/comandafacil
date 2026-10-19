"""Aba Produtos (gerente): cardápio, preços e controle de estoque."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from comandafacil.money import cents_to_input, format_brl

from . import dialogs as dlg


class ProductDialog(QDialog):
    def __init__(self, categories, product=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar produto" if product else "Novo produto")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(product["name"] if product else "")
        self.cat = QComboBox()
        self.cat.setEditable(True)
        self.cat.addItems(categories)
        self.cat.setCurrentText(product["category"] if product else (categories[0] if categories else "Outros"))
        self.price = QLineEdit(cents_to_input(product["price_cents"]) if product else "")
        self.price.setPlaceholderText("0,00")
        self.avail = QCheckBox("Disponível para venda")
        self.avail.setChecked(bool(product["available"]) if product else True)
        self.track = QCheckBox("Controlar estoque")
        self.track.setChecked(bool(product["track_stock"]) if product else False)
        self.stock = QSpinBox()
        self.stock.setRange(0, 999999)
        self.stock.setValue(product["stock"] if product else 0)
        self.stock_min = QSpinBox()
        self.stock_min.setRange(0, 999999)
        self.stock_min.setValue(product["stock_min"] if product else 0)
        form.addRow("Nome", self.name)
        form.addRow("Categoria", self.cat)
        form.addRow("Preço (R$)", self.price)
        form.addRow("", self.avail)
        form.addRow("", self.track)
        form.addRow("Estoque atual", self.stock)
        form.addRow("Estoque mínimo", self.stock_min)
        lay.addLayout(form)
        self.track.toggled.connect(self._sync)
        self._sync()
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = QPushButton("Cancelar"), QPushButton("Salvar")
        ok.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._ok)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _sync(self, *_):
        self.stock.setEnabled(self.track.isChecked())
        self.stock_min.setEnabled(self.track.isChecked())

    def _ok(self):
        price = dlg.money_or_none(self.price.text())
        if price is None:
            return dlg.warn(self, "Preço inválido. Exemplo: 12,50")
        if not self.name.text().strip():
            return dlg.warn(self, "Informe o nome do produto.")
        self.price_cents = price
        self.accept()

    def values(self):
        return (self.name.text(), self.cat.currentText(), self.price_cents, self.avail.isChecked(),
                self.track.isChecked(), self.stock.value(), self.stock_min.value())


class ProductsView(QWidget):
    def __init__(self, ctrl, on_change=None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.on_change = on_change or (lambda: None)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        head = QHBoxLayout()
        t = QLabel("Produtos")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar…")
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self.refresh)
        head.addWidget(self.search)
        new, edit, delete = QPushButton("+ Novo"), QPushButton("Editar"), QPushButton("Excluir")
        new.setObjectName("primary")
        delete.setObjectName("danger")
        new.clicked.connect(self.new)
        edit.clicked.connect(self.edit)
        delete.clicked.connect(self.delete)
        for b in (new, edit, delete):
            head.addWidget(b)
        lay.addLayout(head)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Produto", "Categoria", "Preço", "Estoque", "Mínimo", "Situação"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        lay.addWidget(self.table, 1)
        self.refresh()

    def refresh(self, *_):
        text = self.search.text().strip().lower()
        self.rows = [p for p in self.ctrl.all_products() if text in p["name"].lower() or text in p["category"].lower()]
        self.table.setRowCount(0)
        for p in self.rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            low = p["track_stock"] and p["stock"] <= p["stock_min"]
            status = ("Indisponível" if not p["available"] else ("Estoque baixo" if low else "Ativo"))
            vals = [p["name"], p["category"], format_brl(p["price_cents"]),
                    str(p["stock"]) if p["track_stock"] else "—",
                    str(p["stock_min"]) if p["track_stock"] else "—", status]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, it)

    def _current(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def _categories(self):
        return sorted({p["category"] for p in self.ctrl.all_products()}) or ["Outros"]

    def new(self):
        d = ProductDialog(self._categories(), None, self)
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.save_product, None, *d.values())
        if ok:
            self.refresh()
            self.on_change()

    def edit(self):
        p = self._current()
        if not p:
            return
        d = ProductDialog(self._categories(), p, self)
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.save_product, p["id"], *d.values())
        if ok:
            self.refresh()
            self.on_change()

    def delete(self):
        p = self._current()
        if not p:
            return
        if not dlg.confirm(self, f"Excluir “{p['name']}”?\nO histórico de vendas é preservado. "
                                 "Para apenas tirar do cardápio, edite e desmarque “Disponível”."):
            return
        ok, _ = dlg.attempt(self, self.ctrl.delete_product, p["id"])
        if ok:
            self.refresh()
            self.on_change()
