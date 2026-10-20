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

        # pedidos online de exemplo (nuvem falsa: nada sai da máquina)
        from comandafacil import online

        def fake_cloud(fn, p):
            if fn == "cf_pdv_pull_orders":
                item = lambda i, q, pr, n: {"id": i, "qty": q, "price_cents": pr, "name": n, "note": ""}  # noqa: E731
                return {"ok": True, "accepting": True, "orders": [
                    {"code": "SELFTEST01", "seq": 12, "kind": "entrega", "name": "Carla Dias", "phone": "94988887777",
                     "address": "Av. Brasil, 250 - Centro", "note": "Tocar a campainha", "payment": "Dinheiro",
                     "change_for_cents": 10000, "subtotal_cents": 4400, "delivery_fee_cents": 500,
                     "total_cents": 4900, "created_at": "2026-01-01T19:30:00-03:00",
                     "items": [item(prods[0], 2, 2200, "Hambúrguer")]},
                    {"code": "SELFTEST02", "seq": 13, "kind": "retirada", "name": "Bruno Lima", "phone": "94977776666",
                     "address": "", "note": "", "payment": "Pix", "change_for_cents": None, "subtotal_cents": 600,
                     "delivery_fee_cents": 0, "total_cents": 600, "created_at": "2026-01-01T19:41:00-03:00",
                     "items": [item(prods[1], 1, 600, "Item")]}]}
            return {"ok": True}

        ctrl.cloud_transport = fake_cloud
        ctrl.online_activate(online.make_activation_code("https://exemplo.supabase.co", "chave", "loja-teste", "tok"))
        ctrl.online_save_settings(True, True, 500, 0, "18h às 23h", "", "https://exemplo.vercel.app")
        online.sync(ctrl.conn, online.CloudClient.from_conn(ctrl.conn, transport=fake_cloud))

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
        win.online.table.selectRow(0)
        win.online.show_detail()
        win.go(1)
        app.processEvents()
        win.grab().save(str(out / "pedidos-online-detalhe.png"))
        assert ctrl.online_new_count() == 2
        ticket = ctrl.online_ticket("SELFTEST01")
        assert "PEDIDO ONLINE #12" in ticket
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
