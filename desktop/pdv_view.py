"""Tela principal do PDV: salão + balcão + delivery numa só tela (comanda à direita)."""
from urllib.parse import quote

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from comandafacil import services
from comandafacil.money import format_brl

from . import dialogs as dlg

BOARD_COLS, PRODUCT_COLS = 3, 2


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()


def _scroll(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


class PdvView(QWidget):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.table_id = None      # mesa selecionada (comanda pode ainda não existir)
        self.order_id = None      # comanda aberta selecionada
        self.kind_filter = "todos"
        self.category = None
        self._build()
        QShortcut(QKeySequence("F2"), self, activated=self.search.setFocus)
        QShortcut(QKeySequence("F10"), self, activated=self.pay)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_board)
        self.timer.start(20000)
        self.refresh_all()

    # ------------------------------------------------------------- layout
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_board(), 4)
        root.addWidget(self._build_menu(), 4)
        root.addWidget(self._build_order(), 4)

    def _panel(self):
        f = QFrame()
        f.setObjectName("panel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(12, 12, 12, 12)
        return f, lay

    def _build_board(self):
        panel, lay = self._panel()
        title = QLabel("Atendimentos")
        title.setObjectName("title")
        lay.addWidget(title)
        chips = QHBoxLayout()
        self.chip_buttons = {}
        for key, text in (("todos", "Todos"), ("mesa", "Mesas"), ("balcao", "Balcão"), ("delivery", "Delivery")):
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(key == "todos")
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            chips.addWidget(b)
            self.chip_buttons[key] = b
        lay.addLayout(chips)
        self.board_inner = QWidget()
        self.board_grid = QGridLayout(self.board_inner)
        self.board_grid.setSpacing(8)
        self.board_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(_scroll(self.board_inner), 1)
        row = QHBoxLayout()
        b1, b2 = QPushButton("+ Balcão"), QPushButton("+ Delivery")
        b1.clicked.connect(lambda: self.new_counter("balcao"))
        b2.clicked.connect(lambda: self.new_counter("delivery"))
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        return panel

    def _build_menu(self):
        panel, lay = self._panel()
        self.banner = QLabel()
        self.banner.setObjectName("alert_bad")
        self.banner.setWordWrap(True)
        lay.addWidget(self.banner)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar produto (F2)…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_products)
        self.search.returnPressed.connect(self._search_enter)
        lay.addWidget(self.search)
        self.chip_row = QHBoxLayout()
        chip_host = QWidget()
        chip_host.setLayout(self.chip_row)
        chips_scroll = _scroll(chip_host)
        chips_scroll.setFixedHeight(48)
        chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(chips_scroll)
        self.prod_inner = QWidget()
        self.prod_grid = QGridLayout(self.prod_inner)
        self.prod_grid.setSpacing(8)
        self.prod_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(_scroll(self.prod_inner), 1)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Qtd"))
        self.qty = QSpinBox()
        self.qty.setRange(1, 99)
        opts.addWidget(self.qty)
        self.note = QLineEdit()
        self.note.setPlaceholderText("Observação do próximo item (ex.: sem cebola)")
        opts.addWidget(self.note, 1)
        lay.addLayout(opts)
        return panel

    def _build_order(self):
        panel, lay = self._panel()
        self.order_title = QLabel("Selecione uma mesa")
        self.order_title.setObjectName("title")
        self.order_sub = QLabel("")
        self.order_sub.setObjectName("subtitle")
        self.order_sub.setWordWrap(True)
        lay.addWidget(self.order_title)
        lay.addWidget(self.order_sub)
        self.items = QTableWidget(0, 4)
        self.items.setHorizontalHeaderLabels(["Qtd", "Item", "Cozinha", "Valor"])
        self.items.verticalHeader().setVisible(False)
        self.items.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.items.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items.setAlternatingRowColors(True)
        hh = self.items.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        lay.addWidget(self.items, 1)
        ib = QHBoxLayout()
        self.btn_minus, self.btn_plus, self.btn_remove = QPushButton("−"), QPushButton("+"), QPushButton("Remover")
        self.btn_remove.setObjectName("danger")
        self.btn_minus.clicked.connect(lambda: self.change_selected(-1))
        self.btn_plus.clicked.connect(lambda: self.change_selected(+1))
        self.btn_remove.clicked.connect(self.remove_selected)
        for b in (self.btn_minus, self.btn_plus, self.btn_remove):
            ib.addWidget(b)
        lay.addLayout(ib)

        self.service_chk = QCheckBox()
        self.service_chk.clicked.connect(self.toggle_service)
        self.lbl_sub, self.lbl_svc, self.lbl_dlv = QLabel(), QLabel(), QLabel()
        grid = QGridLayout()
        grid.addWidget(QLabel("Subtotal"), 0, 0)
        grid.addWidget(self.lbl_sub, 0, 1, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.service_chk, 1, 0)
        grid.addWidget(self.lbl_svc, 1, 1, Qt.AlignmentFlag.AlignRight)
        self.dlv_caption = QLabel("Entrega")
        grid.addWidget(self.dlv_caption, 2, 0)
        grid.addWidget(self.lbl_dlv, 2, 1, Qt.AlignmentFlag.AlignRight)
        lay.addLayout(grid)
        tot = QHBoxLayout()
        cap = QLabel("TOTAL")
        cap.setObjectName("muted")
        self.lbl_total = QLabel(format_brl(0))
        self.lbl_total.setObjectName("total")
        tot.addWidget(cap)
        tot.addStretch(1)
        tot.addWidget(self.lbl_total)
        lay.addLayout(tot)

        self.btn_kitchen = QPushButton("Enviar à cozinha")
        self.btn_kitchen.clicked.connect(self.send_kitchen)
        lay.addWidget(self.btn_kitchen)
        self.btn_pay = QPushButton("Fechar conta  (F10)")
        self.btn_pay.setObjectName("primary")
        self.btn_pay.setMinimumHeight(46)
        self.btn_pay.clicked.connect(self.pay)
        lay.addWidget(self.btn_pay)
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        self.btn_bill = QPushButton("Pedir conta")
        self.btn_bill.clicked.connect(self.toggle_bill)
        self.btn_print = QPushButton("🖨 Conferência")
        self.btn_split = QPushButton("Dividir")
        self.btn_customer = QPushButton("Cliente")
        self.btn_wa = QPushButton("WhatsApp")
        self.btn_transfer = QPushButton("Transferir")
        self.btn_merge = QPushButton("Juntar")
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setObjectName("danger")
        self.btn_print.clicked.connect(self.print_check)
        self.btn_split.clicked.connect(self.split)
        self.btn_customer.clicked.connect(self.edit_customer)
        self.btn_wa.clicked.connect(self.whatsapp)
        self.btn_transfer.clicked.connect(self.transfer)
        self.btn_merge.clicked.connect(self.merge)
        self.btn_cancel.clicked.connect(self.cancel)
        for b in (self.btn_bill, self.btn_print, self.btn_split, self.btn_customer, self.btn_wa):
            r1.addWidget(b)
        for b in (self.btn_transfer, self.btn_merge, self.btn_cancel):
            r2.addWidget(b)
        lay.addLayout(r1)
        lay.addLayout(r2)
        return panel

    # ----------------------------------------------------------- atualização
    def refresh_all(self):
        self.refresh_banner()
        self.refresh_board()
        self.refresh_categories()
        self.refresh_products()
        self.refresh_order()

    def refresh_banner(self):
        if self.ctrl.cash_session():
            self.banner.hide()
        else:
            self.banner.setText("⚠ Caixa fechado — o gerente precisa abrir o caixa (aba Caixa) para vender.")
            self.banner.show()

    def set_filter(self, key):
        self.kind_filter = key
        for k, b in self.chip_buttons.items():
            b.setChecked(k == key)
        self.refresh_board()

    def refresh_board(self):
        _clear(self.board_grid)
        cards = [c for c in self.ctrl.board() if self.kind_filter in ("todos", c["kind"])]
        for i, c in enumerate(cards):
            if c["busy"]:
                text = f"{c['name']}\n{format_brl(c['total'])}\n{c['minutes']} min"
                if c["who"]:
                    text += f"\n{c['who'][:18]}"
                if c["ready"]:
                    text += "\n🔔 Pedido pronto"
                elif c["bill"]:
                    text += "\n💲 Pediu a conta"
            else:
                text = f"{c['name']}\nLivre"
            b = QPushButton(text)
            b.setObjectName("tableReady" if c["ready"] else "tableBill" if c["bill"]
                            else "tableBusy" if c["busy"] else "tableFree")
            b.setCheckable(True)
            b.setChecked(self._is_selected(c))
            b.clicked.connect(lambda _=False, card=c: self.select_card(card))
            self.board_grid.addWidget(b, i // BOARD_COLS, i % BOARD_COLS)
        if not cards:
            self.board_grid.addWidget(QLabel("Nada por aqui ainda."), 0, 0)

    def _is_selected(self, card):
        if card["order_id"] is not None:
            return card["order_id"] == self.order_id
        return card["table_id"] is not None and card["table_id"] == self.table_id and self.order_id is None

    def refresh_categories(self):
        _clear(self.chip_row)
        cats = [None] + self.ctrl.categories()
        for cat in cats:
            b = QPushButton(cat or "Todos")
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(cat == self.category)
            b.clicked.connect(lambda _=False, c=cat: self.set_category(c))
            self.chip_row.addWidget(b)
        self.chip_row.addStretch(1)

    def set_category(self, cat):
        self.category = cat
        self.refresh_categories()
        self.refresh_products()

    def refresh_products(self, *_):
        _clear(self.prod_grid)
        prods = self.ctrl.products(self.search.text(), self.category)
        cat_index = {c: i for i, c in enumerate(self.ctrl.categories())}
        for i, p in enumerate(prods):
            text = f"{p['name']}\n{format_brl(p['price_cents'])}"
            out = bool(p["track_stock"]) and p["stock"] <= 0
            if p["track_stock"]:
                text += f"  ·  {'esgotado' if out else str(p['stock']) + ' un.'}"
            b = QPushButton(text)
            b.setObjectName("product")
            b.setProperty("cat", cat_index.get(p["category"], 7) % 8)
            b.setEnabled(not out)
            b.clicked.connect(lambda _=False, pid=p["id"]: self.add_product(pid))
            self.prod_grid.addWidget(b, i // PRODUCT_COLS, i % PRODUCT_COLS)
        if not prods:
            self.prod_grid.addWidget(QLabel("Nenhum produto encontrado."), 0, 0)
        self._last_products = prods

    def _search_enter(self):
        if len(self._last_products) == 1:
            self.add_product(self._last_products[0]["id"])
            self.search.clear()

    def refresh_order(self):
        has_order = self.order_id is not None
        order = items = totals = None
        if has_order:
            order, items, totals = self.ctrl.order_detail(self.order_id)
            if order["status"] != "aberta":      # fechada/cancelada por outro caminho
                self.order_id, has_order = None, False
        self.items.setRowCount(0)
        if has_order:
            kind = services.KINDS[order["kind"]]
            self.order_title.setText(f"{order['table_name']}" if order["kind"] == "mesa" else f"{kind} #{order['id']}")
            bits = [f"Comanda #{order['id']}"]
            if order["customer_name"]:
                bits.append(order["customer_name"])
            if order["customer_phone"]:
                bits.append(order["customer_phone"])
            if order["address"]:
                bits.append(order["address"])
            self.order_sub.setText(" · ".join(bits))
            for it in items:
                r = self.items.rowCount()
                self.items.insertRow(r)
                label = it["product_name"] + (f"\n   ↳ {it['note']}" if it["note"] else "")
                cells = [QTableWidgetItem(str(it["quantity"])), QTableWidgetItem(label),
                         QTableWidgetItem(services.KITCHEN_LABELS.get(it["kitchen_status"], "")),
                         QTableWidgetItem(format_brl(it["unit_price_cents"] * it["quantity"]))]
                cells[0].setData(Qt.ItemDataRole.UserRole, it["id"])
                cells[3].setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                for c, cell in enumerate(cells):
                    self.items.setItem(r, c, cell)
            self.items.resizeRowsToContents()
            if items:
                self.items.selectRow(self.items.rowCount() - 1)
            self.service_chk.setText(f"Taxa de serviço ({order['service_pct']}%)")
            self.service_chk.setChecked(bool(order["service_enabled"]))
            self.lbl_sub.setText(format_brl(totals["subtotal"]))
            self.lbl_svc.setText(format_brl(totals["service"]))
            self.lbl_dlv.setText(format_brl(totals["delivery"]))
            self.lbl_total.setText(format_brl(totals["total"]))
            is_delivery = order["kind"] == "delivery"
            self.dlv_caption.setVisible(is_delivery)
            self.lbl_dlv.setVisible(is_delivery)
        elif self.table_id is not None:
            name = next((c["name"] for c in self.ctrl.board() if c["table_id"] == self.table_id), "Mesa")
            self.order_title.setText(name)
            self.order_sub.setText("Mesa livre — toque em um produto para abrir a comanda.")
            self._zero_totals()
        else:
            self.order_title.setText("Selecione uma mesa")
            self.order_sub.setText("Ou abra um pedido de balcão/delivery.")
            self._zero_totals()
        kind = order["kind"] if has_order else None
        self.btn_pay.setEnabled(has_order and totals["count"] > 0)
        for b in (self.btn_minus, self.btn_plus, self.btn_remove, self.btn_print, self.btn_split,
                  self.btn_cancel, self.btn_bill):
            b.setEnabled(has_order)
        new_items = sum(1 for it in items if it["kitchen_status"] == "novo") if has_order else 0
        self.btn_kitchen.setEnabled(new_items > 0)
        self.btn_kitchen.setText(f"Enviar à cozinha ({new_items})" if new_items else "Enviar à cozinha")
        self.btn_bill.setText("Conta pedida ✔" if has_order and order["bill_requested_at"] else "Pedir conta")
        self.service_chk.setEnabled(has_order)
        self.btn_customer.setEnabled(has_order and kind in ("balcao", "delivery"))
        self.btn_wa.setEnabled(has_order and kind in ("balcao", "delivery"))
        self.btn_transfer.setEnabled(has_order and kind == "mesa")
        self.btn_merge.setEnabled(has_order and kind == "mesa")

    def _zero_totals(self):
        self.items.setRowCount(0)
        for lbl in (self.lbl_sub, self.lbl_svc, self.lbl_dlv, self.lbl_total):
            lbl.setText(format_brl(0))
        self.service_chk.setText("Taxa de serviço")
        self.service_chk.setChecked(False)
        self.dlv_caption.hide()
        self.lbl_dlv.hide()

    # ----------------------------------------------------------------- ações
    def select_card(self, card):
        self.table_id = card["table_id"]
        self.order_id = card["order_id"]
        self.refresh_board()
        self.refresh_order()

    def new_counter(self, kind):
        d = dlg.CustomerDialog(kind, self)
        if d.exec() != dlg.ACCEPTED:
            return
        ok, oid = dlg.attempt(self, self.ctrl.new_counter_order, kind, *d.values())
        if ok:
            self.table_id, self.order_id = None, oid
            self.refresh_all()

    def add_product(self, product_id):
        if self.order_id is None and self.table_id is None:
            return dlg.warn(self, "Selecione uma mesa (ou abra um pedido de balcão/delivery) primeiro.")
        ok, oid = dlg.attempt(self, self.ctrl.add_item, product_id, self.qty.value(), self.note.text(),
                              self.order_id, self.table_id)
        if ok:
            self.order_id = oid
            self.qty.setValue(1)
            self.note.clear()
            self.refresh_board()
            self.refresh_products()
            self.refresh_order()

    def _selected_item(self):
        row = self.items.currentRow()
        if row < 0:
            return None
        return self.items.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def change_selected(self, delta):
        item_id = self._selected_item()
        if item_id is None:
            return
        ok, _ = dlg.attempt(self, self.ctrl.change_qty, item_id, delta)
        if ok:
            self.refresh_all()

    def remove_selected(self):
        item_id = self._selected_item()
        if item_id is None:
            return
        ok, _ = dlg.attempt(self, self.ctrl.remove_item, item_id)
        if ok:
            self.refresh_all()

    def toggle_service(self, checked):
        if self.order_id is None:
            return
        ok, _ = dlg.attempt(self, self.ctrl.set_service, self.order_id, checked)
        self.refresh_order()
        self.refresh_board()

    def send_kitchen(self):
        if self.order_id is None:
            return
        ok, n = dlg.attempt(self, self.ctrl.send_to_kitchen, self.order_id)
        if ok:
            self.refresh_all()

    def toggle_bill(self):
        if self.order_id is None:
            return
        o, _, _ = self.ctrl.order_detail(self.order_id)
        ok, _ = dlg.attempt(self, self.ctrl.request_bill, self.order_id, not o["bill_requested_at"])
        if ok:
            self.refresh_all()

    def pay(self):
        if self.order_id is None or not self.btn_pay.isEnabled():
            return
        d = dlg.PaymentDialog(self.ctrl, self.order_id, self)
        if d.exec() != dlg.ACCEPTED:
            return
        paid_id = self.order_id
        msg = "Conta fechada com sucesso."
        if d.change:
            msg += f"\n\nTroco: {format_brl(d.change)}"
        dlg.info(self, msg, "Pagamento concluído")
        if dlg.confirm(self, "Imprimir o cupom?", "Cupom"):
            dlg.ReceiptDialog(self.ctrl.receipt_text(paid_id), self, f"Cupom — comanda #{paid_id}").exec()
        self.order_id = self.table_id = None
        self.refresh_all()

    def print_check(self):
        if self.order_id is None:
            return
        dlg.ReceiptDialog(self.ctrl.receipt_text(self.order_id), self, "Conferência de conta").exec()

    def split(self):
        if self.order_id is not None:
            dlg.SplitDialog(self.ctrl, self.order_id, self).exec()

    def edit_customer(self):
        if self.order_id is None:
            return
        o, _, _ = self.ctrl.order_detail(self.order_id)
        d = dlg.CustomerDialog(o["kind"], self, o["customer_name"] or "", o["customer_phone"] or "",
                               o["address"] or "", o["delivery_fee_cents"] or 0)
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.update_customer, self.order_id, *d.values())
        if ok:
            self.refresh_all()

    def whatsapp(self):
        """Abre o WhatsApp Web/aplicativo com a confirmação do pedido (envio é manual)."""
        if self.order_id is None:
            return
        o, items, totals = self.ctrl.order_detail(self.order_id)
        if not o["customer_phone"]:
            return dlg.warn(self, "Informe o telefone do cliente (botão Cliente).")
        text = services.whatsapp_message(self.ctrl.conn, o, items, totals)
        QGuiApplication.clipboard().setText(text)
        url = f"https://wa.me/{services.phone_for_whatsapp(o['customer_phone'])}?text={quote(text)}"
        QDesktopServices.openUrl(QUrl(url))
        dlg.info(self, "A mensagem foi copiada e a conversa foi aberta no WhatsApp.\n"
                       "Confira e envie manualmente.")

    def transfer(self):
        if self.order_id is None:
            return
        free = self.ctrl.free_tables()
        d = dlg.ChoiceDialog("Transferir comanda", "Mover para a mesa livre:",
                             [(c["name"], c["table_id"]) for c in free], self)
        if not free:
            return dlg.warn(self, "Não há mesas livres.")
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.transfer, self.order_id, d.value())
        if ok:
            self.refresh_all()

    def merge(self):
        if self.order_id is None:
            return
        busy = self.ctrl.busy_tables(self.order_id)
        if not busy:
            return dlg.warn(self, "Não há outra mesa ocupada para juntar.")
        d = dlg.ChoiceDialog("Juntar mesas", "Trazer os itens da mesa:",
                             [(c["name"], c["order_id"]) for c in busy], self)
        if d.exec() != dlg.ACCEPTED:
            return
        ok, _ = dlg.attempt(self, self.ctrl.merge, self.order_id, d.value())
        if ok:
            self.refresh_all()

    def cancel(self):
        if self.order_id is None:
            return
        reason = dlg.ask_text(self, "Cancelar comanda", "Motivo do cancelamento:")
        if reason is None:
            return
        ok, _ = dlg.attempt(self, self.ctrl.cancel, self.order_id, reason or "")
        if ok:
            self.order_id = self.table_id = None
            self.refresh_all()
