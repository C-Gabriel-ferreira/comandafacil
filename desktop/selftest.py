"""Autoteste da interface (usa o Qt de verdade, sem janela visível — plataforma 'offscreen').

    python comandafacil_desktop.py --selftest [pasta_dos_prints]

Monta o PDV com dados de exemplo, percorre todas as abas, salva um print de cada tela e
termina com código 0 (ok) ou 1 (erro). É usado pelo GitHub Actions antes de publicar o instalador.
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path


def run_selftest(outdir) -> int:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    log = out / "selftest.txt"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from comandafacil import db as dbm
        from . import dialogs as dlg
        from .controller import Controller
        from .main_window import MainWindow
        from .theme import stylesheet

        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        ctrl = Controller(dbm.connect())
        ctrl.create_manager("gerente", "Gerente", "senha-forte-1")
        ctrl.login("gerente", "senha-forte-1")
        ctrl.open_cash(10000)
        prods = [r["id"] for r in ctrl.conn.execute("SELECT id FROM products ORDER BY id LIMIT 3")]
        oid = ctrl.add_item(prods[0], 2, "sem cebola", table_id=1)
        ctrl.add_item(prods[1], 1, order_id=oid)
        ctrl.send_to_kitchen(oid)
        ctrl.add_item(prods[2], 1, order_id=oid)
        ctrl.new_counter_order("delivery", "Ana Souza", "94991016248", "Rua das Flores, 100", 500)

        app = QApplication.instance() or QApplication([])
        app.setStyleSheet(stylesheet(True))
        win = MainWindow(ctrl, "http://192.168.0.10:5000")
        win.resize(1360, 820)
        win.show()
        win.pdv.select_card(next(c for c in ctrl.board() if c["table_id"] == 1))
        for i, (name, _, _) in enumerate(win.pages):
            win.go(i)
            app.processEvents()
            win.grab().save(str(out / f"{i + 1}-{name}.png"))
        win.go(0)
        win.dark = False
        win.apply_theme()
        app.processEvents()
        win.grab().save(str(out / "1-PDV-claro.png"))
        pay = dlg.PaymentDialog(ctrl, oid)
        pay.show()
        app.processEvents()
        pay.grab().save(str(out / "pagamento.png"))
        pay.close()
        text = ctrl.receipt_text(oid)
        assert "CONFERENCIA" in text and "R$" in text
        log.write_text("SELFTEST OK\n", encoding="utf-8")
        return 0
    except BaseException:
        log.write_text("SELFTEST FALHOU\n" + traceback.format_exc(), encoding="utf-8")
        print(traceback.format_exc(), file=sys.stderr)
        return 1
