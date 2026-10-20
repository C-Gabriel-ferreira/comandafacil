"""Tela inicial: indicadores do dia, vendas por hora, mais vendidos e avisos que pedem atenção."""
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from comandafacil.clock import now_dt
from comandafacil.money import format_brl

from .theme import palette
from .ui import KpiCard, pill

WEEKDAYS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro"]


def greeting(hour: int) -> str:
    return "Bom dia" if hour < 12 else ("Boa tarde" if hour < 18 else "Boa noite")


def hour_series(by_hour):
    """[(rótulo, valor)] contínuo, com no mínimo 8 horas de janela para o gráfico não ficar esticado."""
    vals = {int(r["label"][:2]): r["value"] for r in by_hour}
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    while hi - lo < 7:
        if lo > 0:
            lo -= 1
        if hi - lo < 7 and hi < 23:
            hi += 1
        if lo == 0 and hi == 23:
            break
    return [(f"{h:02d}h", vals.get(h, 0)) for h in range(lo, hi + 1)]


class HourBars(QWidget):
    """Gráfico de barras simples (vendas por hora), desenhado à mão para não precisar de biblioteca extra."""

    def __init__(self, dark=False, parent=None):
        super().__init__(parent)
        self.dark, self.data = dark, []
        self.setMinimumHeight(190)

    def set_data(self, data, dark):
        self.data, self.dark = data, dark
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = palette(self.dark)
        w, h = self.width(), self.height()
        if not self.data:
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "Sem vendas fechadas hoje ainda")
            return
        n, top = len(self.data), max(v for _, v in self.data) or 1
        slot = w / n
        bar_w = min(34.0, slot * 0.6)
        base = h - 22
        for i, (label, v) in enumerate(self.data):
            bh = max(3.0, (base - 18) * v / top) if v else 3.0
            x = i * slot + (slot - bar_w) / 2
            p.setPen(Qt.PenStyle.NoPen)
            col = QColor(c["brand"] if v else c["border"])
            if v and v != top:
                col.setAlpha(150)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, base - bh, bar_w, bh), 5, 5)
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(i * slot, base + 4, slot, 16), Qt.AlignmentFlag.AlignCenter, label)
            if v and v == top:
                p.setPen(QColor(c["text"]))
                p.drawText(QRectF(i * slot - 20, base - bh - 18, slot + 40, 16), Qt.AlignmentFlag.AlignCenter,
                           format_brl(v))


def _panel(title):
    f = QFrame()
    f.setObjectName("panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(16, 14, 16, 14)
    t = QLabel(title)
    t.setObjectName("h2")
    v.addWidget(t)
    return f, v


class HomeView(QWidget):
    def __init__(self, ctrl, go=None, is_dark=lambda: False, parent=None):
        super().__init__(parent)
        self.ctrl, self.go, self.is_dark = ctrl, go or (lambda name: None), is_dark
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(14)

        head = QHBoxLayout()
        col = QVBoxLayout()
        self.hello = QLabel()
        self.hello.setObjectName("title")
        self.date = QLabel()
        self.date.setObjectName("subtitle")
        col.addWidget(self.hello)
        col.addWidget(self.date)
        head.addLayout(col)
        head.addStretch(1)
        for text, page, primary in (("🧾  Abrir o PDV", "PDV", True), ("🛵  Pedidos online", "Pedidos online", False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(lambda _=False, p=page: self.go(p))
            head.addWidget(b)
        lay.addLayout(head)

        row = QGridLayout()
        row.setSpacing(12)
        self.k_sales = KpiCard("VENDIDO HOJE", highlight=True)
        self.k_orders = KpiCard("COMANDAS FECHADAS")
        self.k_open = KpiCard("EM ABERTO AGORA")
        self.k_kitchen = KpiCard("NA COZINHA")
        for i, k in enumerate((self.k_sales, self.k_orders, self.k_open, self.k_kitchen)):
            row.addWidget(k, 0, i)
        lay.addLayout(row)

        mid = QHBoxLayout()
        mid.setSpacing(12)
        chart_panel, cv = _panel("Vendas por hora")
        self.chart = HourBars()
        cv.addWidget(self.chart, 1)
        mid.addWidget(chart_panel, 3)
        top_panel, tv = _panel("Mais vendidos hoje")
        self.top_box = QVBoxLayout()
        tv.addLayout(self.top_box)
        tv.addStretch(1)
        mid.addWidget(top_panel, 2)
        lay.addLayout(mid, 1)

        alert_panel, av = _panel("Precisa de atenção")
        self.alert_box = QVBoxLayout()
        av.addLayout(self.alert_box)
        lay.addWidget(alert_panel)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(30000)
        self.refresh()

    @staticmethod
    def _clear(box):
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def refresh(self, *_):
        s = self.ctrl.home_stats()
        now = now_dt()
        name = (self.ctrl.user["display_name"] if self.ctrl.user else "").split(" ")[0]
        self.hello.setText(f"{greeting(now.hour)}{', ' + name if name else ''}!")
        self.date.setText(f"{WEEKDAYS[now.weekday()].capitalize()}, {now.day} de {MONTHS[now.month - 1]}")

        hint = "Primeiro dia com vendas" if s["delta_pct"] is None and s["total"] else ""
        if s["delta_pct"] is not None:
            sign = "+" if s["delta_pct"] >= 0 else ""
            hint = f"{sign}{s['delta_pct']}% vs. ontem ({format_brl(s['yesterday_total'])})"
        self.k_sales.set(format_brl(s["total"]), hint)
        self.k_orders.set(str(s["orders"]), f"Ticket médio {format_brl(s['avg'])}")
        self.k_open.set(str(s["open_count"]), f"{format_brl(s['open_total'])} a receber")
        self.k_kitchen.set(str(s["kitchen"]), f"{s['kitchen_late']} atrasado(s)" if s["kitchen_late"] else "Tudo no prazo")

        self.chart.set_data(hour_series(s["by_hour"]), self.is_dark())
        self._clear(self.top_box)
        if not s["top"]:
            self.top_box.addWidget(QLabel("Ainda não há vendas hoje."))
        for r in s["top"]:
            line = QHBoxLayout()
            line.addWidget(QLabel(r["label"]), 3)
            n = QLabel(f"{r['n']} un.")
            n.setObjectName("muted")
            line.addWidget(n)
            holder = QWidget()
            holder.setLayout(line)
            self.top_box.addWidget(holder)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(r["pct"])
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            self.top_box.addWidget(bar)

        self._clear(self.alert_box)
        alerts = []
        if not s["cash_open"]:
            alerts.append(("bad", "Caixa fechado", "Peça ao gerente para abrir o caixa antes de vender.", None))
        if s["online_new"]:
            alerts.append(("warn", f"{s['online_new']} pedido(s) online novo(s)", "Aguardando aceitar ou recusar.",
                           "Pedidos online"))
        if s["kitchen_late"]:
            alerts.append(("bad", f"{s['kitchen_late']} pedido(s) atrasado(s) na cozinha", "", "Cozinha"))
        if s["ready"]:
            alerts.append(("ok", f"{s['ready']} pedido(s) pronto(s) para servir", "", "PDV"))
        if s["bills"]:
            alerts.append(("info", f"{s['bills']} mesa(s) pediram a conta", "", "PDV"))
        for p in s["low_stock"]:
            alerts.append(("warn", f"Estoque baixo: {p['name']}", f"restam {p['stock']} (mínimo {p['stock_min']})",
                           "Produtos" if self.ctrl.is_manager else None))
        if not alerts:
            alerts.append(("ok", "Tudo certo por aqui", "Nada precisa da sua atenção agora.", None))
        for kind, title, detail, page in alerts:
            r = QHBoxLayout()
            r.addWidget(pill(title, kind))
            d = QLabel(detail)
            d.setObjectName("muted")
            r.addWidget(d, 1)
            if page:
                b = QPushButton("Abrir")
                b.clicked.connect(lambda _=False, pg=page: self.go(pg))
                r.addWidget(b)
            holder = QWidget()
            holder.setLayout(r)
            self.alert_box.addWidget(holder)
