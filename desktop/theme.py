"""Tema visual (QSS) claro/escuro. A cor da marca fica em BRAND."""

BRAND = "#16a34a"

DARK = dict(bg="#0f1419", panel="#171d25", card="#1e2630", card2="#252f3b", border="#2d3846",
            text="#e8edf3", muted="#8b98a8", brand=BRAND, brand_h="#22c55e", danger="#ef4444",
            warn="#f59e0b", busy="#7c2d12", busy_b="#f97316", free="#14532d", free_b="#22c55e",
            input="#10161d")
LIGHT = dict(bg="#f1f4f8", panel="#ffffff", card="#ffffff", card2="#eef2f7", border="#d5dce5",
             text="#17202b", muted="#617083", brand=BRAND, brand_h="#15803d", danger="#dc2626",
             warn="#d97706", busy="#ffedd5", busy_b="#f97316", free="#dcfce7", free_b="#16a34a",
             input="#ffffff")


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def stylesheet(dark: bool) -> str:
    c = palette(dark)
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", "Helvetica Neue", Arial; font-size: 13px; color: {c['text']}; }}
QMainWindow, QDialog, QWidget#root {{ background: {c['bg']}; }}
QWidget {{ background: transparent; }}
QDialog {{ background: {c['panel']}; }}
QLabel#title {{ font-size: 20px; font-weight: 700; }}
QLabel#subtitle, QLabel#muted {{ color: {c['muted']}; }}
QLabel#total {{ font-size: 30px; font-weight: 800; color: {c['brand_h']}; }}
QLabel#bigvalue {{ font-size: 22px; font-weight: 700; }}
QFrame#panel, QWidget#panel {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QFrame#sidebar {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
QPushButton {{ background: {c['card2']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 8px 14px; font-weight: 600; }}
QPushButton:hover {{ border-color: {c['brand']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['card']}; }}
QPushButton#primary {{ background: {c['brand']}; border-color: {c['brand']}; color: white; }}
QPushButton#primary:hover {{ background: {c['brand_h']}; }}
QPushButton#primary:disabled {{ background: {c['card2']}; color: {c['muted']}; border-color: {c['border']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QPushButton#danger:hover {{ border-color: {c['danger']}; }}
QPushButton#nav {{ text-align: left; border: none; background: transparent; padding: 11px 14px;
    border-radius: 8px; font-size: 14px; }}
QPushButton#nav:hover {{ background: {c['card2']}; }}
QPushButton#nav:checked {{ background: {c['brand']}; color: white; }}
QPushButton#chip {{ border-radius: 14px; padding: 5px 14px; }}
QPushButton#chip:checked {{ background: {c['brand']}; color: white; border-color: {c['brand']}; }}
QPushButton#product {{ text-align: left; padding: 10px; min-height: 54px; border-radius: 10px;
    background: {c['card']}; }}
QPushButton#product:hover {{ border-color: {c['brand']}; background: {c['card2']}; }}
QPushButton#tableFree {{ background: {c['free']}; border: 2px solid {c['free_b']}; border-radius: 10px;
    min-height: 66px; text-align: left; padding: 8px 10px; }}
QPushButton#tableBusy {{ background: {c['busy']}; border: 2px solid {c['busy_b']}; border-radius: 10px;
    min-height: 66px; text-align: left; padding: 8px 10px; }}
QPushButton#tableFree:checked, QPushButton#tableBusy:checked {{ border: 3px solid {c['text']}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 9px;
    selection-background-color: {c['brand']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {c['brand']}; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; border: 1px solid {c['border']};
    selection-background-color: {c['brand']}; }}
QTableWidget, QListWidget {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px;
    gridline-color: {c['border']}; alternate-background-color: {c['card']}; }}
QTableWidget::item {{ padding: 4px; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {c['brand']}; color: white; }}
QHeaderView::section {{ background: {c['card2']}; border: none; border-bottom: 1px solid {c['border']};
    padding: 7px; font-weight: 700; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; border: 1px solid {c['border']}; background: {c['card']};
    border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {c['brand']}; color: white; border-color: {c['brand']}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
QToolTip {{ background: {c['card2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
"""
