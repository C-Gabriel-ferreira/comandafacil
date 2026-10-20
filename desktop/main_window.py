"""Janela principal: menu lateral + páginas (PDV, Caixa, Produtos, Relatórios)."""
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import dialogs as dlg
from .cash_view import CashView
from .home_view import HomeView
from .kitchen_view import KitchenView
from .online_view import OnlineView
from .pdv_view import PdvView
from .products_view import ProductsView
from .reports_view import ReportsView
from .theme import stylesheet
from .ui import set_state
from .update_view import UpdateChecker, UpdateDialog


class MainWindow(QMainWindow):
    def __init__(self, ctrl, server_url=None):
        super().__init__()
        self.ctrl = ctrl
        self.server_url = server_url
        self.logged_out = False
        self.settings = QSettings("ComandaFacil", "Desktop")
        self.dark = str(self.settings.value("dark2", "false")).lower() == "true"  # claro por padrão (v2.4)
        biz = ctrl.setting("business_name", "ComandaFácil")
        self.setWindowTitle(f"{biz} — PDV")
        self.resize(1360, 820)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_sidebar(biz))

        self.stack = QStackedWidget()
        self.pdv = PdvView(ctrl)
        self.home = HomeView(ctrl, go=self.go_name, is_dark=lambda: self.dark)
        self.cash = CashView(ctrl, on_change=self.pdv.refresh_all)
        self.kitchen = KitchenView(ctrl)
        self.online = OnlineView(ctrl, on_new=self.set_online_badge, on_change=self.pdv.refresh_all)
        self.pages = [("Início", self.home, False), ("PDV", self.pdv, False), ("Pedidos online", self.online, False),
                      ("Cozinha", self.kitchen, False)]
        self.pages.append(("Caixa", self.cash, False))
        if ctrl.is_manager:
            self.products = ProductsView(ctrl, on_change=self.pdv.refresh_all)
            self.reports = ReportsView(ctrl)
            self.pages += [("Produtos", self.products, True), ("Relatórios", self.reports, True)]
        for _, w, _ in self.pages:
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)
        self._add_nav()
        self.update_info = None
        self.checker = UpdateChecker(ctrl, self.set_update)
        self.checker.start()
        self.go(0)
        self.apply_theme()

    # ------------------------------------------------------------- sidebar
    def _build_sidebar(self, biz):
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(224)
        self.side = QVBoxLayout(bar)
        self.side.setContentsMargins(10, 14, 10, 14)
        title = QLabel(biz)
        title.setObjectName("title")
        title.setWordWrap(True)
        self.side.addWidget(title)
        self.cash_lbl = QLabel()
        self.side.addWidget(self.cash_lbl)
        self.side.addSpacing(10)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = {}
        return bar

    def _add_nav(self):
        icons = {"Início": "🏠", "PDV": "🧾", "Pedidos online": "🛵", "Cozinha": "👨‍🍳", "Caixa": "💰", "Produtos": "🍔", "Relatórios": "📊"}
        for i, (name, _, _) in enumerate(self.pages):
            b = QPushButton(f"{icons.get(name, '')}  {name}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, idx=i: self.go(idx))
            self.nav_group.addButton(b, i)
            self.nav_buttons[name] = b
            self.side.addWidget(b)
        self.side.addStretch(1)
        if self.server_url:
            ph = QLabel(f"📱 Celulares dos garçons\n{self.server_url}")
            ph.setObjectName("muted")
            ph.setWordWrap(True)
            ph.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.side.addWidget(ph)
        who = QLabel(f"{self.ctrl.user['display_name']}\n({self.ctrl.user['role']})")
        who.setObjectName("muted")
        self.side.addWidget(who)
        self.btn_update = QPushButton(f"ℹ  v{self.ctrl.app_version()} · Novidades")
        self.btn_update.clicked.connect(self.show_updates)
        self.side.addWidget(self.btn_update)
        theme = QPushButton("☀/🌙  Tema")
        theme.clicked.connect(self.toggle_theme)
        out = QPushButton("Trocar usuário")
        out.clicked.connect(self.logout)
        self.side.addWidget(theme)
        self.side.addWidget(out)

    # ------------------------------------------------------------- ações
    def go(self, idx):
        self.stack.setCurrentIndex(idx)
        btn = self.nav_group.button(idx)
        if btn:
            btn.setChecked(True)
        page = self.pages[idx][1]
        for method in ("refresh_all", "refresh"):
            if hasattr(page, method):
                getattr(page, method)()
                break
        self.update_cash_label()

    def set_update(self, info):
        self.update_info = info
        if info:
            self.btn_update.setText(f"⬆  Atualização {info['version']} disponível")
            self.btn_update.setObjectName("primary")
        else:
            self.btn_update.setText(f"ℹ  v{self.ctrl.app_version()} · Novidades")
            self.btn_update.setObjectName("")
        self.btn_update.setStyleSheet(self.btn_update.styleSheet())  # reaplica o estilo do objectName
        self.btn_update.style().unpolish(self.btn_update)
        self.btn_update.style().polish(self.btn_update)

    def show_updates(self):
        UpdateDialog(self.ctrl, self.update_info, self).exec()

    def set_online_badge(self, n):
        b = self.nav_buttons.get("Pedidos online")
        if b:
            b.setText("🛵  Pedidos online" + (f"  ({n})" if n else ""))

    def go_name(self, name):
        for i, (n, _, _) in enumerate(self.pages):
            if n == name:
                return self.go(i)

    def update_cash_label(self):
        if self.ctrl.cash_session():
            set_state(self.cash_lbl, "● Caixa aberto", "ok")
        else:
            set_state(self.cash_lbl, "● Caixa fechado", "bad")

    def apply_theme(self):
        QApplication.instance().setStyleSheet(stylesheet(self.dark))
        if hasattr(self, "home"):
            self.home.refresh()

    def toggle_theme(self):
        self.dark = not self.dark
        self.settings.setValue("dark2", "true" if self.dark else "false")
        self.apply_theme()

    def logout(self):
        if dlg.confirm(self, "Sair e trocar de usuário?"):
            self.ctrl.logout()
            self.logged_out = True
            self.close()
