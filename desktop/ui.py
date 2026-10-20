"""Pequenos componentes visuais reutilizáveis (pílulas de status, cartões de indicador)."""
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


def repolish(widget):
    """Reaplica o estilo depois de mudar objectName/propriedade em tempo de execução."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def set_state(label, text, kind, pill=True):
    """Texto de status colorido pelo tema. kind: ok | warn | bad | info | muted."""
    label.setText(text)
    label.setObjectName(f"{'pill' if pill else 'state'}_{kind}")
    label.setStyleSheet("")
    repolish(label)


def pill(text, kind="muted"):
    lbl = QLabel()
    set_state(lbl, text, kind)
    return lbl


class KpiCard(QFrame):
    """Cartão de indicador: legenda pequena, valor grande e uma dica opcional."""

    def __init__(self, caption, highlight=False, parent=None):
        super().__init__(parent)
        self.setObjectName("kpi_brand" if highlight else "kpi")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)
        self.caption = QLabel(caption)
        self.caption.setObjectName("kpi_caption")
        self.value = QLabel("—")
        self.value.setObjectName("kpi_value")
        self.hint = QLabel("")
        self.hint.setObjectName("kpi_hint")
        for w in (self.caption, self.value, self.hint):
            lay.addWidget(w)

    def set(self, value, hint=""):
        self.value.setText(value)
        self.hint.setText(hint)
