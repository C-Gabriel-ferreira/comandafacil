"""Rodar:  python -m unittest discover -s tests -v"""
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import clock, create_app, db as dbm, services as sv
from comandafacil.money import format_brl, parse_money, split_evenly


class MoneyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_money("12,50"), 1250)
        self.assertEqual(parse_money("1.234,56"), 123456)
        self.assertEqual(parse_money("R$ 3"), 300)
        self.assertEqual(parse_money("0.1") + parse_money("0.2"), 30)  # sem erro de float
        for bad in ("", "abc", "-1", "nan", "inf", "99999999999"):
            with self.assertRaises(ValueError):
                parse_money(bad)

    def test_format_and_split(self):
        self.assertEqual(format_brl(123456), "R$ 1.234,56")
        self.assertEqual(split_evenly(1000, 3), [334, 333, 333])
        self.assertEqual(sum(split_evenly(9999, 7)), 9999)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        dbm.configure(self.dir)
        dbm.init_db()
        self.conn = dbm.connect()

    def tearDown(self):
        self.conn.close()

    def order_with(self, *items, table=1):
        oid, _ = sv.open_order(self.conn, "mesa", table)
        for pid, q in items:
            sv.add_item(self.conn, oid, pid, q)
        return oid

    def test_open_is_idempotent_per_table(self):
        a, created_a = sv.open_order(self.conn, "mesa", 1)
        b, created_b = sv.open_order(self.conn, "mesa", 1)
        self.assertEqual(a, b)
        self.assertTrue(created_a and not created_b)

    def test_totals_service_and_close_with_split_payment_and_change(self):
        oid = self.order_with((5, 2), (1, 1))  # 2x hambúrguer 22,00 + água 3,00 = 47,00
        sv.set_service(self.conn, oid, True)  # 10% = 4,70
        t = sv.order_totals(self.conn, sv.get_order(self.conn, oid))
        self.assertEqual((t["subtotal"], t["service"], t["total"]), (4700, 470, 5170))
        with self.assertRaises(sv.BusinessError):  # falta dinheiro
            sv.close_order(self.conn, oid, [("Pix", 3000)])
        change = sv.close_order(self.conn, oid, [("Pix", 3000), ("Dinheiro", 2500)])
        self.assertEqual(change, 330)
        paid = self.conn.execute("SELECT SUM(amount_cents) FROM payments WHERE order_id=?", (oid,)).fetchone()[0]
        self.assertEqual(paid, 5170)

    def test_change_only_in_cash_and_double_close_blocked(self):
        oid = self.order_with((1, 1))
        with self.assertRaises(sv.BusinessError):
            sv.close_order(self.conn, oid, [("Pix", 1000)])
        sv.close_order(self.conn, oid, [("Pix", 300)])
        with self.assertRaises(sv.BusinessError):
            sv.close_order(self.conn, oid, [("Pix", 300)])
        with self.assertRaises(sv.BusinessError):
            sv.add_item(self.conn, oid, 1, 1)

    def test_same_item_lines_merge_but_notes_separate(self):
        oid = self.order_with((5, 1), (5, 1))
        sv.add_item(self.conn, oid, 5, 1, "sem cebola")
        items = sv.get_items(self.conn, oid)
        self.assertEqual(sorted(i["quantity"] for i in items), [1, 2])

    def test_stock_decrement_block_and_restore(self):
        self.conn.execute("UPDATE products SET track_stock=1, stock=3, stock_min=1 WHERE id=1")
        oid = self.order_with((1, 2))
        self.assertEqual(self.conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0], 1)
        with self.assertRaises(sv.BusinessError):
            sv.add_item(self.conn, oid, 1, 2)
        self.assertEqual([p["id"] for p in sv.low_stock_products(self.conn)], [1])
        sv.cancel_order(self.conn, oid, "teste")
        self.assertEqual(self.conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0], 3)

    def test_cancel_closed_sale_is_excluded_from_reports(self):
        oid = self.order_with((5, 1))
        sv.close_order(self.conn, oid, [("Dinheiro", 2200)])
        rep = sv.sales_report(self.conn, clock.today(), clock.today())
        self.assertEqual((rep["orders"], rep["total"]), (1, 2200))
        self.assertEqual(rep["top_products"][0]["label"], "Hambúrguer")
        with self.assertRaises(sv.BusinessError):
            sv.cancel_order(self.conn, oid, "  ")
        sv.cancel_order(self.conn, oid, "erro de lançamento")
        rep = sv.sales_report(self.conn, clock.today(), clock.today())
        self.assertEqual((rep["orders"], rep["total"], rep["cancelled_n"]), (0, 0, 1))

    def test_transfer_and_merge(self):
        a = self.order_with((1, 1), table=1)
        b = self.order_with((2, 1), table=2)
        with self.assertRaises(sv.BusinessError):
            sv.transfer_order(self.conn, a, 2)  # ocupada
        sv.transfer_order(self.conn, a, 3)
        self.assertEqual(sv.get_order(self.conn, a)["table_id"], 3)
        sv.merge_orders(self.conn, a, b)
        self.assertEqual(len(sv.get_items(self.conn, a)), 2)
        self.assertEqual(sv.get_order(self.conn, b)["status"], "juntada")
        self.assertIsNone(sv.open_order_for_table(self.conn, 2))

    def test_delivery_requires_data_and_adds_fee(self):
        with self.assertRaises(sv.BusinessError):
            sv.open_order(self.conn, "delivery", None, "Ana", "", "")
        oid, _ = sv.open_order(self.conn, "delivery", None, "Ana", "(94) 99999-0000", "Rua A, 10", 700)
        sv.add_item(self.conn, oid, 5, 1)
        o = sv.get_order(self.conn, oid)
        self.assertEqual(sv.order_totals(self.conn, o)["total"], 2900)
        msg = sv.whatsapp_message(self.conn, o, sv.get_items(self.conn, oid), sv.order_totals(self.conn, o))
        self.assertIn("R$ 29,00", msg)
        self.assertEqual(sv.phone_for_whatsapp(o["customer_phone"]), "5594999990000")

    def test_backup_is_valid_database(self):
        dest = dbm.make_backup("manual")
        c = sqlite3.connect(dest)
        self.assertGreater(c.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)
        c.close()


class KitchenAndBillTests(ServiceTests):
    def test_kitchen_flow(self):
        oid = self.order_with((5, 1), (1, 2))
        with self.assertRaises(sv.BusinessError):
            sv.kitchen_step(self.conn, oid, "pronto")  # nada enviado ainda
        self.assertEqual(sv.send_to_kitchen(self.conn, oid), 2)
        with self.assertRaises(sv.BusinessError):
            sv.send_to_kitchen(self.conn, oid)  # nada novo
        t = sv.kitchen_tickets(self.conn)
        self.assertEqual((len(t), t[0]["state"]), (1, "enviado"))
        sv.kitchen_step(self.conn, oid, "iniciar")
        self.assertEqual(sv.kitchen_tickets(self.conn)[0]["state"], "preparando")
        # item novo depois do envio não se mistura com o já enviado
        sv.add_item(self.conn, oid, 5, 1)
        self.assertEqual(len(sv.get_items(self.conn, oid)), 3)
        sv.send_to_kitchen(self.conn, oid)
        sv.kitchen_step(self.conn, oid, "pronto")
        self.assertEqual(sv.kitchen_tickets(self.conn)[0]["state"], "pronto")
        self.assertIn(oid, sv.ready_orders(self.conn))
        sv.kitchen_step(self.conn, oid, "entregue")
        self.assertEqual(sv.kitchen_tickets(self.conn), [])
        self.assertNotIn(oid, sv.ready_orders(self.conn))

    def test_bill_request_and_reopen(self):
        oid = self.order_with((5, 1))
        sv.set_bill_requested(self.conn, oid, True)
        self.assertIsNotNone(sv.get_order(self.conn, oid)["bill_requested_at"])
        sv.close_order(self.conn, oid, [("Pix", 2200)])
        with self.assertRaises(sv.BusinessError):
            sv.set_bill_requested(self.conn, oid, True)  # já fechada
        sv.reopen_order(self.conn, oid)
        o = sv.get_order(self.conn, oid)
        self.assertEqual((o["status"], o["total_cents"], o["bill_requested_at"]), ("aberta", None, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payments WHERE order_id=?", (oid,)).fetchone()[0], 0)
        sv.close_order(self.conn, oid, [("Pix", 2200)])  # pode fechar de novo
        other, _ = sv.open_order(self.conn, "mesa", 1)  # mesa ocupada: não reabre
        with self.assertRaises(sv.BusinessError):
            sv.reopen_order(self.conn, oid)

    def test_shopping_list(self):
        self.conn.execute("UPDATE products SET track_stock=1, stock=2, stock_min=5 WHERE id=1")
        rows = sv.shopping_list(self.conn)
        self.assertEqual([(r["id"], r["buy"]) for r in rows], [(1, 8)])  # repõe até 10


class V1UpgradeTests(unittest.TestCase):
    def test_v1_database_gets_kitchen_columns(self):
        d = tempfile.mkdtemp()
        dbm.configure(d)
        dbm.init_db()
        c = dbm.connect()
        c.executescript("""ALTER TABLE products DROP COLUMN cost_cents; ALTER TABLE order_items DROP COLUMN unit_cost_cents; ALTER TABLE order_items DROP COLUMN kitchen_status; ALTER TABLE order_items DROP COLUMN sent_at;
            ALTER TABLE order_items DROP COLUMN ready_at; ALTER TABLE orders DROP COLUMN bill_requested_at;
            PRAGMA user_version=1;""")
        c.close()
        dbm.init_db()  # migra v1 -> versão atual com backup
        c = dbm.connect()
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], dbm.SCHEMA_VERSION)
        c.execute("SELECT kitchen_status, sent_at, ready_at FROM order_items")
        c.execute("SELECT bill_requested_at FROM orders")
        self.assertTrue(list((Path(d) / "backups").glob("*pre_migracao.db")))
        c.close()


class LegacyMigrationTests(unittest.TestCase):
    def test_old_float_database_is_converted_to_cents(self):
        d = tempfile.mkdtemp()
        old = sqlite3.connect(Path(d) / "comandas.db")
        old.executescript("""
        CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'Outros', price REAL NOT NULL, available INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE tables (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, table_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'aberta', created_at TEXT NOT NULL, closed_at TEXT, payment_method TEXT);
        CREATE TABLE order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, product_id INTEGER, product_name TEXT NOT NULL, unit_price REAL NOT NULL, quantity INTEGER NOT NULL);
        INSERT INTO products(name,category,price) VALUES ('Suco','Bebidas',8.1),('Pizza','Pratos',28.0);
        INSERT INTO tables(name) VALUES ('Mesa 1'),('Mesa 2');
        INSERT INTO orders(table_id,status,created_at,closed_at,payment_method) VALUES (1,'fechada','2025-01-01T12:00:00','2025-01-01T13:00:00','Pix'),(2,'aberta','2025-01-02T12:00:00',NULL,NULL),(2,'aberta','2025-01-02T12:05:00',NULL,NULL);
        INSERT INTO order_items(order_id,product_id,product_name,unit_price,quantity) VALUES (1,1,'Suco',8.1,3),(1,2,'Pizza',28.0,1),(3,2,'Pizza',28.0,1);
        """)
        old.commit(); old.close()
        dbm.configure(d)
        dbm.init_db()
        c = dbm.connect()
        self.assertEqual(c.execute("SELECT price_cents FROM products WHERE name='Suco'").fetchone()[0], 810)
        o = c.execute("SELECT * FROM orders WHERE id=1").fetchone()
        self.assertEqual((o["status"], o["total_cents"]), ("fechada", 5230))
        self.assertEqual(c.execute("SELECT method, amount_cents FROM payments").fetchone()[:], ("Pix", 5230))
        self.assertEqual(c.execute("SELECT status FROM orders WHERE id=2").fetchone()[0], "cancelada")  # duplicada
        self.assertEqual(c.execute("SELECT status FROM orders WHERE id=3").fetchone()[0], "aberta")
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], dbm.SCHEMA_VERSION)
        self.assertTrue(list(Path(d).glob("comandas_antes_da_v2_*.db")))  # original preservado
        c.close()


class HttpFlowTests(unittest.TestCase):
    def setUp(self):
        from comandafacil.views import auth
        auth._fails.clear()
        self.app = create_app(tempfile.mkdtemp())
        self.c = self.app.test_client()

    def token(self, path="/"):
        return re.search(r'name="_csrf" value="([^"]+)"', self.c.get(path).get_data(as_text=True)).group(1)

    def post(self, path, page="/", **data):
        data.setdefault("_csrf", self.token(page))
        return self.c.post(path, data=data)

    def login_manager(self):
        t = self.token("/setup")
        self.c.post("/setup", data=dict(_csrf=t, username="gerente", password="segredo1", password2="segredo1"))

    def test_requires_login_and_csrf(self):
        self.assertEqual(self.c.get("/").status_code, 302)
        self.login_manager()
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.post("/mesa/1/abrir").status_code, 400)  # sem CSRF
        self.assertEqual(self.c.post("/mesa/1/abrir", data={"_csrf": "errado"}).status_code, 400)

    def test_full_flow_and_waiter_permissions(self):
        self.login_manager()
        self.post("/usuarios/adicionar", "/usuarios", username="joao", password="senha123", role="garcom", display_name="João")
        # fluxo do gerente: abrir, adicionar, fechar
        r = self.post("/mesa/1/abrir")
        self.assertEqual(r.status_code, 302)
        oid = int(r.headers["Location"].rsplit("/", 1)[1])
        page = f"/comanda/{oid}"
        rid = "abc123"
        self.post(f"{page}/adicionar", page, product_id=5, quantity=2, note="bem passado", _rid=rid)
        self.post(f"{page}/adicionar", page, product_id=5, quantity=2, _rid=rid)  # duplo clique ignorado
        html = self.c.get(page).get_data(as_text=True)
        self.assertIn("bem passado", html)
        self.assertEqual(self.c.get(f"{page}/cupom").status_code, 200)
        self.post(f"{page}/fechar", page, pay_method=["Pix", "Dinheiro"], pay_amount=["20,00", "24,00"])
        conn = dbm.connect()
        o = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        self.assertEqual((o["status"], o["total_cents"]), ("fechada", 4400))
        conn.close()
        self.assertEqual(self.c.get("/relatorios?periodo=hoje").status_code, 200)
        csv = self.c.get("/relatorios/vendas.csv?periodo=hoje")
        text = csv.data.decode("utf-8-sig")
        self.assertIn("44,00", text)
        self.assertIn("Pix 20,00; Dinheiro 24,00", text)
        # garçom não acessa áreas de gerente
        self.post("/logout", "/")
        t = self.token("/login")
        self.c.post("/login", data=dict(_csrf=t, username="joao", password="senha123"))
        for p in ("/produtos", "/usuarios", "/relatorios", "/config", "/auditoria"):
            self.assertEqual(self.c.get(p).status_code, 403, p)
        self.assertEqual(self.c.get("/").status_code, 200)
        r = self.post(f"{page}/cancelar", page, reason="tentativa")  # estorno é só do gerente
        conn = dbm.connect()
        self.assertEqual(conn.execute("SELECT status FROM orders WHERE id=?", (oid,)).fetchone()[0], "fechada")
        conn.close()

    def test_kitchen_and_support_pages(self):
        self.login_manager()
        r = self.post("/mesa/2/abrir")
        oid = int(r.headers["Location"].rsplit("/", 1)[1])
        page = f"/comanda/{oid}"
        self.post(f"{page}/adicionar", page, product_id=5, quantity=1)
        self.assertIn("Enviar à cozinha", self.c.get(page).get_data(as_text=True))
        self.post(f"{page}/cozinha", page)
        html = self.c.get("/cozinha").get_data(as_text=True)
        self.assertIn("Hambúrguer", html)
        self.post(f"/cozinha/{oid}/pronto", "/cozinha")
        self.assertIn("Pedido pronto", self.c.get("/").get_data(as_text=True))
        self.post(f"{page}/conta", page, on="1")
        self.assertIn("Pediu a conta", self.c.get("/").get_data(as_text=True))
        self.assertIn("HelpTech", self.c.get("/").get_data(as_text=True))
        self.assertEqual(self.c.get("/produtos/compras").status_code, 200)
        self.assertIn("HelpTech", self.c.get(f"{page}/cupom").get_data(as_text=True))

    def test_healthz_is_public(self):
        r = self.c.get("/healthz")
        self.assertEqual((r.status_code, r.get_json()["status"]), (200, "ok"))

    def test_https_mode_sets_secure_cookie_and_hsts(self):
        from comandafacil import security
        security.HTTPS = True
        try:
            app = create_app(tempfile.mkdtemp())
            c = app.test_client()
            r = c.get("/setup")
            self.assertIn("Strict-Transport-Security", r.headers)
            t = re.search(r'name="_csrf" value="([^"]+)"', r.get_data(as_text=True)).group(1)
            r = c.post("/setup", data=dict(_csrf=t, username="g", password="segredo1", password2="segredo1"))
            self.assertTrue(any("Secure" in h for h in r.headers.getlist("Set-Cookie")))
        finally:
            security.HTTPS = False

    def test_short_password_rejected(self):
        t = self.token("/setup")
        self.c.post("/setup", data=dict(_csrf=t, username="g", password="curta", password2="curta"))
        self.assertEqual(self.c.get("/").status_code, 302)  # ainda sem usuário

    def test_login_throttle(self):
        self.login_manager()
        self.post("/logout", "/")
        t = self.token("/login")
        for _ in range(6):
            self.c.post("/login", data=dict(_csrf=t, username="gerente", password="errada"))
        r = self.c.post("/login", data=dict(_csrf=t, username="gerente", password="segredo1"), follow_redirects=True)
        self.assertIn("Muitas tentativas", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
