"""Tema visual (QSS): claro por padrão, escuro opcional. Cores em tokens; a marca é o laranja do site."""

BRAND = "#d9480f"

LIGHT = dict(
    bg="#f5f4f0", panel="#ffffff", card="#ffffff", card2="#f0eee8", border="#e3e0d6", hover="#faf8f3",
    text="#1d1c19", muted="#6c685d", brand=BRAND, brand_h="#b83c0b", brand_soft="#fdeee6", on_brand="#ffffff",
    ok="#15803d", ok_bg="#dcfce7", warn="#b45309", warn_bg="#fef3c7", bad="#b91c1c", bad_bg="#fee2e2",
    info="#1d4ed8", info_bg="#dbeafe", input="#ffffff", shadow="#e9e6dc")
DARK = dict(
    bg="#141310", panel="#1c1b17", card="#22201c", card2="#2b2925", border="#38352f", hover="#272521",
    text="#f2efe7", muted="#9c978a", brand="#ff7a3d", brand_h="#ff935f", brand_soft="#3a2418", on_brand="#1a0d05",
    ok="#4ade80", ok_bg="#14351f", warn="#fbbf24", warn_bg="#3a2d0e", bad="#f87171", bad_bg="#3d1717",
    info="#60a5fa", info_bg="#14263f", input="#181714", shadow="#0d0c0a")

# acentos por categoria de produto (faixa colorida no cartão): cor + forma ajudam quem não distingue cores
CATEGORY_ACCENTS = ["#d97706", "#2563eb", "#059669", "#9333ea", "#db2777", "#0891b2", "#65a30d", "#64748b"]

def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def _pills(c) -> str:
    out = []
    for kind in ("ok", "warn", "bad", "info"):
        out.append(f"QLabel#pill_{kind} {{ color: {c[kind]}; background: {c[kind + '_bg']}; border-radius: 11px; "
                   f"padding: 3px 12px; font-weight: 700; font-size: 12px; }}")
        out.append(f"QLabel#state_{kind} {{ color: {c[kind]}; font-weight: 700; }}")
    out.append(f"QLabel#pill_muted {{ color: {c['muted']}; background: {c['card2']}; border-radius: 11px; "
               f"padding: 3px 12px; font-weight: 700; font-size: 12px; }}")
    out.append(f"QLabel#state_muted {{ color: {c['muted']}; font-weight: 700; }}")
    return "\n".join(out)


def _accents() -> str:
    return "\n".join(f"QPushButton#product[cat=\"{i}\"] {{ border-left: 5px solid {col}; }}"
                     for i, col in enumerate(CATEGORY_ACCENTS))


def _table_states(c) -> str:
    states = {"tableFree": (c["card"], c["border"], c["border"]), "tableBusy": (c["warn_bg"], c["warn"], c["warn"]),
              "tableBill": (c["bad_bg"], c["bad"], c["bad"]), "tableReady": (c["ok_bg"], c["ok"], c["ok"])}
    out = []
    for name, (bg, border, accent) in states.items():
        out.append(f"QPushButton#{name} {{ background: {bg}; border: 1px solid {border}; "
                   f"border-left: 6px solid {accent}; border-radius: 12px; min-height: 70px; text-align: left; "
                   f"padding: 8px 12px; font-weight: 600; }}")
        out.append(f"QPushButton#{name}:hover {{ border-color: {c['brand']}; border-left-color: {accent}; }}")
        out.append(f"QPushButton#{name}:checked {{ border: 2px solid {c['brand']}; border-left: 6px solid {accent}; }}")
    return "\n".join(out)


def stylesheet(dark: bool = False) -> str:
    c = palette(dark)
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Helvetica Neue", Arial; font-size: 14px;
    color: {c['text']}; }}
QMainWindow, QDialog, QWidget#root {{ background: {c['bg']}; }}
QWidget {{ background: transparent; }}
QDialog {{ background: {c['bg']}; }}
QLabel#title {{ font-size: 22px; font-weight: 800; letter-spacing: -0.3px; }}
QLabel#h2 {{ font-size: 15px; font-weight: 700; }}
QLabel#subtitle, QLabel#muted {{ color: {c['muted']}; }}
QLabel#total {{ font-size: 34px; font-weight: 800; color: {c['brand']}; }}
QLabel#bigvalue {{ font-size: 24px; font-weight: 800; }}
QLabel#kpi_caption {{ color: {c['muted']}; font-size: 12px; font-weight: 600; }}
QLabel#kpi_value {{ font-size: 26px; font-weight: 800; }}
QLabel#kpi_hint {{ color: {c['muted']}; font-size: 12px; }}
{_pills(c)}
QLabel#alert_bad {{ background: {c['bad_bg']}; color: {c['bad']}; border: 1px solid {c['bad']}; border-radius: 10px; padding: 10px 12px; font-weight: 700; }}
QLabel#alert_info {{ background: {c['info_bg']}; color: {c['info']}; border-radius: 10px; padding: 10px 12px; font-weight: 600; }}
QFrame#panel, QWidget#panel {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#card, QFrame#kpi {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#kpi_brand {{ background: {c['brand_soft']}; border: 1px solid {c['brand']}; border-radius: 14px; }}
QFrame#ticket_ok {{ background: {c['card']}; border: 1px solid {c['border']}; border-top: 5px solid {c['ok']}; border-radius: 14px; }}
QFrame#ticket_warn {{ background: {c['card']}; border: 1px solid {c['border']}; border-top: 5px solid {c['warn']}; border-radius: 14px; }}
QFrame#ticket_bad {{ background: {c['card']}; border: 1px solid {c['bad']}; border-top: 5px solid {c['bad']}; border-radius: 14px; }}
QFrame#sidebar {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px;
    padding: 9px 16px; font-weight: 600; min-height: 22px; }}
QPushButton:hover {{ border-color: {c['brand']}; background: {c['hover']}; }}
QPushButton:pressed {{ background: {c['card2']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['card2']}; border-color: {c['border']}; }}
QPushButton:focus {{ border: 2px solid {c['brand']}; }}
QPushButton#primary {{ background: {c['brand']}; border-color: {c['brand']}; color: {c['on_brand']}; font-weight: 700; }}
QPushButton#primary:hover {{ background: {c['brand_h']}; border-color: {c['brand_h']}; }}
QPushButton#primary:disabled {{ background: {c['card2']}; color: {c['muted']}; border-color: {c['border']}; }}
QPushButton#cta {{ background: {c['brand']}; border-color: {c['brand']}; color: {c['on_brand']}; font-weight: 800;
    font-size: 16px; min-height: 34px; border-radius: 12px; }}
QPushButton#cta:hover {{ background: {c['brand_h']}; }}
QPushButton#cta:disabled {{ background: {c['card2']}; color: {c['muted']}; border-color: {c['border']}; }}
QPushButton#danger {{ color: {c['bad']}; }}
QPushButton#danger:hover {{ border-color: {c['bad']}; background: {c['bad_bg']}; }}
QPushButton#nav {{ text-align: left; border: none; background: transparent; padding: 12px 14px;
    border-radius: 10px; font-size: 15px; font-weight: 600; color: {c['muted']}; }}
QPushButton#nav:hover {{ background: {c['card2']}; color: {c['text']}; }}
QPushButton#nav:checked {{ background: {c['brand_soft']}; color: {c['brand']}; font-weight: 800; }}
QPushButton#chip {{ border-radius: 17px; padding: 6px 16px; min-height: 20px; }}
QPushButton#chip:checked {{ background: {c['text']}; color: {c['bg']}; border-color: {c['text']}; }}
QPushButton#pay {{ text-align: left; padding: 12px 14px; border-radius: 12px; min-height: 28px; }}
QPushButton#pay:checked {{ background: {c['brand_soft']}; border: 2px solid {c['brand']}; color: {c['brand']}; }}
QPushButton#product {{ text-align: left; padding: 10px 12px; min-height: 58px; border-radius: 12px;
    background: {c['card']}; border-left: 5px solid {c['border']}; }}
QPushButton#product:hover {{ border-color: {c['brand']}; background: {c['hover']}; }}
QPushButton#product:disabled {{ color: {c['muted']}; background: {c['card2']}; }}
{_accents()}
{_table_states(c)}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 8px 10px;
    selection-background-color: {c['brand']}; selection-color: {c['on_brand']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 2px solid {c['brand']}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {c['panel']}; border: 1px solid {c['border']};
    selection-background-color: {c['brand_soft']}; selection-color: {c['brand']}; }}
QTableWidget, QListWidget {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px;
    gridline-color: {c['border']}; alternate-background-color: {c['hover']}; }}
QTableWidget::item {{ padding: 6px; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {c['brand_soft']}; color: {c['brand']}; }}
QHeaderView::section {{ background: {c['card2']}; border: none; border-bottom: 1px solid {c['border']};
    padding: 9px; font-weight: 700; color: {c['muted']}; }}
QProgressBar {{ background: {c['card2']}; border: none; border-radius: 6px; height: 12px; text-align: center; }}
QProgressBar::chunk {{ background: {c['brand']}; border-radius: 6px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 10px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 12px; top: -1px; }}
QTabBar::tab {{ padding: 9px 18px; border: 1px solid {c['border']}; background: {c['card']};
    border-top-left-radius: 10px; border-top-right-radius: 10px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {c['brand_soft']}; color: {c['brand']}; border-color: {c['brand']}; }}
QCheckBox::indicator {{ width: 20px; height: 20px; }}
QToolTip {{ background: {c['card2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; }}
QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
"""
