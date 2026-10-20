"""Diálogos do PDV: login, primeiro acesso, pagamento, cliente, caixa, impressão etc."""
from PySide6.QtCore import QSizeF, Qt
from PySide6.QtGui import QFont, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from comandafacil.money import cents_to_input, format_brl, parse_money
from comandafacil.services import PAYMENT_METHODS, BusinessError

from .ui import set_state

ACCEPTED = QDialog.DialogCode.Accepted
PAY_ICONS = {"Dinheiro": "💵", "Pix": "⚡", "Cartão de débito": "💳", "Cartão de crédito": "💳", "Vale-refeição": "🎟"}


# ----------------------------------------------------------------- utilidades
def warn(parent, message, title="Atenção"):
    QMessageBox.warning(parent, title, message)


def info(parent, message, title="ComandaFácil"):
    QMessageBox.information(parent, title, message)


def confirm(parent, message, title="Confirmar") -> bool:
    return QMessageBox.question(parent, title, message) == QMessageBox.StandardButton.Yes


def attempt(parent, fn, *args, **kwargs):
    """Executa uma ação; mostra o aviso e devolve (False, None) se violar uma regra de negócio."""
    try:
        return True, fn(*args, **kwargs)
    except BusinessError as exc:
        warn(parent, str(exc))
    except ValueError as exc:
        warn(parent, str(exc))
    return False, None


def money_or_none(text):
    try:
        return parse_money(text)
    except ValueError:
        return None


def _buttons(dialog, ok_text="Confirmar", primary=True):
    box = QHBoxLayout()
    box.addStretch(1)
    cancel = QPushButton("Cancelar")
    cancel.clicked.connect(dialog.reject)
    ok = QPushButton(ok_text)
    if primary:
        ok.setObjectName("primary")
    ok.setDefault(True)
    box.addWidget(cancel)
    box.addWidget(ok)
    return box, ok


# ------------------------------------------------------------ acesso ao sistema
class SetupDialog(QDialog):
    """Primeiro acesso: cria o gerente."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Primeiro acesso — criar gerente")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        t = QLabel("Bem-vindo ao ComandaFácil")
        t.setObjectName("title")
        s = QLabel("Crie a conta do gerente. Ela administra caixa, produtos e usuários.")
        s.setObjectName("subtitle")
        s.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(s)
        form = QFormLayout()
        self.name = QLineEdit()
        self.user = QLineEdit()
        self.pw = QLineEdit()
        self.pw2 = QLineEdit()
        for w in (self.pw, self.pw2):
            w.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Seu nome", self.name)
        form.addRow("Usuário", self.user)
        form.addRow("Senha (mín. 8)", self.pw)
        form.addRow("Repita a senha", self.pw2)
        lay.addLayout(form)
        row, ok = _buttons(self, "Criar conta")
        ok.clicked.connect(self._save)
        lay.addLayout(row)

    def _save(self):
        if self.pw.text() != self.pw2.text():
            return warn(self, "As senhas não conferem.")
        done, _ = attempt(self, self.ctrl.create_manager, self.user.text(), self.name.text(), self.pw.text())
        if done:
            self.ctrl.login(self.user.text(), self.pw.text())
            self.accept()


class LoginDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Entrar")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        t = QLabel(ctrl.setting("business_name", "ComandaFácil"))
        t.setObjectName("title")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        form = QFormLayout()
        self.user = QLineEdit()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Usuário", self.user)
        form.addRow("Senha", self.pw)
        lay.addLayout(form)
        row, ok = _buttons(self, "Entrar")
        ok.clicked.connect(self._enter)
        lay.addLayout(row)
        self.pw.returnPressed.connect(self._enter)
        self.user.returnPressed.connect(self.pw.setFocus)

    def _enter(self):
        done, _ = attempt(self, self.ctrl.login, self.user.text(), self.pw.text())
        if done:
            self.accept()
        else:
            self.pw.clear()
            self.pw.setFocus()


# ------------------------------------------------------------------ valores
class MoneyDialog(QDialog):
    """Pede um valor em reais (e, opcionalmente, um motivo)."""

    def __init__(self, title, label, parent=None, with_reason=False, reason_label="Motivo",
                 default_cents=None, hint=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        if hint:
            h = QLabel(hint)
            h.setObjectName("subtitle")
            h.setWordWrap(True)
            lay.addWidget(h)
        form = QFormLayout()
        self.value = QLineEdit(cents_to_input(default_cents) if default_cents is not None else "")
        self.value.setPlaceholderText("0,00")
        form.addRow(label, self.value)
        self.reason = QLineEdit() if with_reason else None
        if self.reason:
            form.addRow(reason_label, self.reason)
        lay.addLayout(form)
        row, ok = _buttons(self)
        ok.clicked.connect(self._ok)
        lay.addLayout(row)
        self.cents = 0

    def _ok(self):
        v = money_or_none(self.value.text())
        if v is None:
            return warn(self, "Digite um valor válido, por exemplo 25,50.")
        self.cents = v
        self.accept()

    @property
    def reason_text(self):
        return self.reason.text().strip() if self.reason else ""


def ask_text(parent, title, label):
    text, ok = QInputDialog.getText(parent, title, label)
    return text.strip() if ok else None


# ------------------------------------------------------------ balcão/delivery
class CustomerDialog(QDialog):
    def __init__(self, kind, parent=None, name="", phone="", address="", fee_cents=0):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle("Novo pedido de delivery" if kind == "delivery" else "Novo pedido de balcão")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(name)
        self.phone = QLineEdit(phone)
        self.address = QLineEdit(address)
        self.fee = QLineEdit(cents_to_input(fee_cents))
        form.addRow("Cliente" + (" *" if kind == "delivery" else ""), self.name)
        form.addRow("Telefone", self.phone)
        if kind == "delivery":
            form.addRow("Endereço *", self.address)
            form.addRow("Taxa de entrega", self.fee)
        lay.addLayout(form)
        row, ok = _buttons(self, "Abrir pedido")
        ok.clicked.connect(self._ok)
        lay.addLayout(row)

    def _ok(self):
        fee = money_or_none(self.fee.text() or "0")
        if fee is None:
            return warn(self, "Taxa de entrega inválida.")
        if self.kind == "delivery" and (not self.name.text().strip() or not self.address.text().strip()):
            return warn(self, "Delivery precisa do nome do cliente e do endereço.")
        self.fee_cents = fee if self.kind == "delivery" else 0
        self.accept()

    def values(self):
        return (self.name.text().strip(), self.phone.text().strip(),
                self.address.text().strip(), self.fee_cents)


# ----------------------------------------------------------------- pagamento
class PaymentDialog(QDialog):
    """Fechar conta com um ou mais pagamentos; mostra falta/troco em tempo real."""

    def __init__(self, ctrl, order_id, parent=None):
        super().__init__(parent)
        self.ctrl, self.order_id = ctrl, order_id
        self.change = 0
        order, _, totals = ctrl.order_detail(order_id)
        self.total = totals["total"]
        self.setWindowTitle(f"Fechar conta — comanda #{order_id}")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        left = QVBoxLayout()
        cap = QLabel("TOTAL A PAGAR")
        cap.setObjectName("muted")
        self.total_lbl = QLabel(format_brl(self.total))
        self.total_lbl.setObjectName("total")
        left.addWidget(cap)
        left.addWidget(self.total_lbl)
        top.addLayout(left)
        top.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("pill_muted")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.status)
        lay.addLayout(top)

        hint = QLabel("Toque na forma de pagamento para adicionar. Pode combinar mais de uma.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        quick = QHBoxLayout()
        quick.setSpacing(8)
        for m in PAYMENT_METHODS:
            b = QPushButton(f"{PAY_ICONS.get(m, '•')}  {m.replace('Cartão de ', 'Cartão ').replace('Vale-refeição', 'Vale-ref.')}")
            b.setObjectName("pay")
            b.clicked.connect(lambda _=False, method=m: self.add_line(method))
            quick.addWidget(b)
        lay.addLayout(quick)

        self.rows_box = QVBoxLayout()
        lay.addLayout(self.rows_box)
        self.rows = []
        lay.addStretch(1)
        btns, self.ok = _buttons(self, "Confirmar pagamento (Enter)")
        self.ok.setObjectName("cta")
        self.ok.clicked.connect(self._confirm)
        lay.addLayout(btns)
        self.add_line("Dinheiro", self.total)
        self.refresh()

    # linhas de pagamento -------------------------------------------------
    def paid(self):
        return sum(money_or_none(e.text()) or 0 for _, e, _ in self.rows)

    def add_line(self, method, cents=None):
        if cents is None:
            cents = max(0, self.total - self.paid())
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox()
        combo.addItems(PAYMENT_METHODS)
        combo.setCurrentText(method)
        edit = QLineEdit(cents_to_input(cents))
        edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit.textChanged.connect(self.refresh)
        rm = QPushButton("✕")
        rm.setFixedWidth(36)
        rm.clicked.connect(lambda _=False, w=wrap: self.remove_line(w))
        h.addWidget(combo, 2)
        h.addWidget(edit, 1)
        h.addWidget(rm)
        self.rows_box.addWidget(wrap)
        self.rows.append((combo, edit, wrap))
        edit.setFocus()
        edit.selectAll()
        self.refresh()

    def remove_line(self, wrap):
        for i, (_, _, w) in enumerate(self.rows):
            if w is wrap:
                self.rows.pop(i)
                break
        wrap.setParent(None)
        wrap.deleteLater()
        self.refresh()

    def refresh(self, *_):
        paid, diff = self.paid(), self.paid() - self.total
        if diff < 0:
            set_state(self.status, f"Faltam {format_brl(-diff)}", "warn")
        elif diff == 0:
            set_state(self.status, "Valor exato ✔", "ok")
        else:
            set_state(self.status, f"Troco {format_brl(diff)}", "info")
        self.ok.setEnabled(paid >= self.total)

    def _confirm(self):
        pays = []
        for combo, edit, _ in self.rows:
            v = money_or_none(edit.text())
            if v is None:
                return warn(self, "Há um valor de pagamento inválido.")
            pays.append((combo.currentText(), v))
        done, change = attempt(self, self.ctrl.pay, self.order_id, pays)
        if done:
            self.change = change
            self.accept()


# ------------------------------------------------------------- escolha / divisão
class ChoiceDialog(QDialog):
    """Escolhe uma opção de uma lista (mesa de destino, mesa para juntar...)."""

    def __init__(self, title, label, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(label))
        self.combo = QComboBox()
        for text, value in options:
            self.combo.addItem(text, value)
        lay.addWidget(self.combo)
        row, ok = _buttons(self)
        ok.clicked.connect(self.accept)
        ok.setEnabled(bool(options))
        lay.addLayout(row)

    def value(self):
        return self.combo.currentData()


class SplitDialog(QDialog):
    def __init__(self, ctrl, order_id, parent=None):
        super().__init__(parent)
        self.ctrl, self.order_id = ctrl, order_id
        self.setWindowTitle("Dividir a conta")
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.people = QSpinBox()
        self.people.setRange(1, 50)
        self.people.setValue(2)
        form.addRow("Dividir por", self.people)
        lay.addLayout(form)
        self.result = QLabel()
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        close = QPushButton("Fechar")
        close.clicked.connect(self.accept)
        lay.addWidget(close)
        self.people.valueChanged.connect(self.update_text)
        self.update_text()

    def update_text(self, *_):
        parts = self.ctrl.split_bill(self.order_id, self.people.value())
        lines = [f"Pessoa {i + 1}: {format_brl(v)}" for i, v in enumerate(parts)]
        self.result.setText("\n".join(lines) + "\n\nA divisão não perde centavos.")


# ------------------------------------------------------------------ impressão
def print_text(parent, text, width_mm=80) -> bool:
    """Imprime texto monoespaçado em bobina (ou 'Salvar como PDF' pelo diálogo do sistema)."""
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QSizeF(width_mm, 300), QPageSize.Unit.Millimeter, "Cupom"))
    if QPrintDialog(printer, parent).exec() != ACCEPTED:
        return False
    doc = QTextDocument()
    font = QFont("Consolas")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSizeF(7.5 if width_mm >= 80 else 6.5)
    doc.setDefaultFont(font)
    doc.setPlainText(text)
    (getattr(doc, "print_", None) or doc.print)(printer)
    return True


class ReceiptDialog(QDialog):
    def __init__(self, text, parent=None, title="Cupom"):
        super().__init__(parent)
        self.text = text
        self.setWindowTitle(title)
        self.setMinimumSize(430, 560)
        lay = QVBoxLayout(self)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(10)
        view.setFont(f)
        lay.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Fechar")
        close.clicked.connect(self.accept)
        pr = QPushButton("🖨  Imprimir")
        pr.setObjectName("primary")
        pr.clicked.connect(lambda: print_text(self, self.text))
        row.addWidget(close)
        row.addWidget(pr)
        lay.addLayout(row)


def hline():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    return f
