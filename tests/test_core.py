"""Rodar:  python -m unittest discover -s tests -v"""
import base64
import importlib.util
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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
        c.executescript("""DROP INDEX idx_users_email; DROP INDEX idx_users_google; DROP TABLE password_resets; ALTER TABLE users DROP COLUMN email; ALTER TABLE users DROP COLUMN google_sub; ALTER TABLE products DROP COLUMN cost_cents; ALTER TABLE order_items DROP COLUMN unit_cost_cents; ALTER TABLE order_items DROP COLUMN kitchen_status; ALTER TABLE order_items DROP COLUMN sent_at;
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
        self.post("/usuarios/adicionar", "/usuarios", username="joao", password="cardapio-77x", role="garcom", display_name="João")
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
        self.c.post("/login", data=dict(_csrf=t, username="joao", password="cardapio-77x"))
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


class SchemaV2Tests(unittest.TestCase):
    def test_v5_database_gets_email_google_and_resets(self):
        d = tempfile.mkdtemp()
        dbm.configure(d)
        dbm.init_db()
        old = dbm.connect()
        old.executescript("DROP INDEX idx_users_email; DROP INDEX idx_users_google; DROP TABLE password_resets;"
                          "ALTER TABLE users DROP COLUMN email; ALTER TABLE users DROP COLUMN google_sub;"
                          "INSERT INTO users(username,display_name,password_hash,role,created_at) "
                          "VALUES ('ana','Ana','x','gerente','2026-01-01'); PRAGMA user_version = 5;")
        old.close()
        dbm.init_db()
        c = dbm.connect()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        self.assertTrue({"email", "google_sub"} <= cols)
        self.assertEqual(tuple(c.execute("SELECT username, email FROM users WHERE username='ana'").fetchone()), ("ana", None))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0], 0)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], dbm.SCHEMA_VERSION)
        self.assertTrue(list((Path(d) / "backups").glob("*pre_migracao.db")))  # backup antes de migrar
        c.close()

    def test_fresh_database_has_same_columns(self):
        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        c = dbm.connect()
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        self.assertTrue({"email", "google_sub"} <= cols)
        c.close()


def _load_license_tool():
    spec = importlib.util.spec_from_file_location(
        "gerar_licenca", Path(__file__).resolve().parent.parent / "tools" / "gerar_licenca.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LicenseTests(unittest.TestCase):
    """Licença assinada: limite de usuários, validade, responsável e Google só para gerente."""

    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        cls.tool = _load_license_tool()
        cls.key = Ed25519PrivateKey.generate()
        cls.other_key = Ed25519PrivateKey.generate()

    def setUp(self):
        from comandafacil.views import auth
        auth._fails.clear()
        p = mock.patch.dict(os.environ, {"COMANDAFACIL_LICENSE_PUBKEY": self.tool.public_b64(self.key)})
        p.start()
        self.addCleanup(p.stop)
        self.app = create_app(tempfile.mkdtemp())
        self.c = self.app.test_client()
        t = self.token("/setup")
        self.c.post("/setup", data=dict(_csrf=t, username="gerente", password="segredo1", password2="segredo1",
                                        email="dono@exemplo.com"))

    def token(self, path):
        return re.search(r'name="_csrf" value="([^"]+)"', self.c.get(path).get_data(as_text=True)).group(1)

    def post(self, path, page="/usuarios", **data):
        data.setdefault("_csrf", self.token(page))
        return self.c.post(path, data=data)

    def lic(self, users=5, days=365, email="dono@exemplo.com", key=None):
        return self.tool.sign_license(key or self.key, "Restaurante Teste", email, users,
                                      clock.today() + timedelta(days=days))

    def add_user(self, name):
        return self.post("/usuarios/adicionar", username=name, password="cardapio-77x", role="garcom")

    def active_count(self):
        c = dbm.connect(); n = c.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]; c.close()
        return n

    def test_parse_accepts_valid_and_rejects_tampered_or_foreign(self):
        from comandafacil import license as L
        info = L.parse(self.lic(7))
        self.assertEqual((info["max_users"], info["owner_email"], info["customer"]),
                         (7, "dono@exemplo.com", "Restaurante Teste"))
        good = self.lic(7)
        head, payload, sig = good.split(".")
        forged = base64.urlsafe_b64encode(base64.urlsafe_b64decode(payload + "==").replace(b'"usuarios":7', b'"usuarios":9')).rstrip(b"=").decode()
        for bad in (f"{head}.{forged}.{sig}", self.lic(7, key=self.other_key), "lixo", "", "CF1.a.b"):
            with self.assertRaises(L.LicenseError):
                L.parse(bad)

    def test_evaluation_limit_without_license(self):
        self.post("/login", "/login", username="gerente", password="segredo1")
        self.add_user("ana"); self.add_user("bia")
        self.assertEqual(self.active_count(), 3)
        self.add_user("caio")  # 4º: acima do limite de avaliação
        self.assertEqual(self.active_count(), 3)
        page = self.c.get("/usuarios").get_data(as_text=True)
        self.assertIn("Avaliação", page)
        self.assertIn("3 de 3", page)

    def test_activating_license_raises_limit_and_requires_owner_email(self):
        self.post("/login", "/login", username="gerente", password="segredo1")
        self.add_user("ana"); self.add_user("bia")
        wrong = self.lic(5, email="outro@exemplo.com")
        self.post("/usuarios/licenca", license=wrong)  # e-mail do gerente não é o do responsável
        self.assertIn("Avaliação", self.c.get("/usuarios").get_data(as_text=True))
        self.post("/usuarios/licenca", license=self.lic(5, key=self.other_key))  # assinatura de outro vendedor
        self.assertIn("Avaliação", self.c.get("/usuarios").get_data(as_text=True))
        self.post("/usuarios/licenca", license=self.lic(5))
        page = self.c.get("/usuarios").get_data(as_text=True)
        self.assertIn("Ativa", page)
        self.assertIn("3 de 5", page)
        self.add_user("caio"); self.add_user("dani")
        self.assertEqual(self.active_count(), 5)
        self.add_user("edu")  # 6º
        self.assertEqual(self.active_count(), 5)

    def test_reactivating_user_uses_a_seat(self):
        self.post("/login", "/login", username="gerente", password="segredo1")
        self.post("/usuarios/licenca", license=self.lic(2))
        self.add_user("ana")
        c = dbm.connect(); uid = c.execute("SELECT id FROM users WHERE username='ana'").fetchone()[0]; c.close()
        self.post(f"/usuarios/{uid}/atualizar", role="garcom", active="0", email="")
        self.add_user("bia")
        self.assertEqual(self.active_count(), 2)
        self.post(f"/usuarios/{uid}/atualizar", role="garcom", active="1", email="")  # sem vaga
        c = dbm.connect(); n = c.execute("SELECT active FROM users WHERE id=?", (uid,)).fetchone()[0]; c.close()
        self.assertEqual(n, 0)

    def test_expired_license_keeps_users_working_but_blocks_new_ones(self):
        self.post("/login", "/login", username="gerente", password="segredo1")
        self.post("/usuarios/licenca", license=self.lic(10))
        expired = self.lic(10, days=-1)
        c = dbm.connect()
        c.execute("UPDATE settings SET value=? WHERE key='license_key'", (expired,))
        c.close()
        page = self.c.get("/usuarios").get_data(as_text=True)
        self.assertIn("Vencida", page)
        self.add_user("ana")
        self.assertEqual(self.active_count(), 1)
        self.assertEqual(self.c.get("/").status_code, 200)  # o expediente não para
        r = self.post("/usuarios/licenca", license=expired)  # não ativa licença já vencida
        self.assertIn("Vencida", self.c.get("/usuarios").get_data(as_text=True))

    def test_license_page_is_manager_only(self):
        self.post("/login", "/login", username="gerente", password="segredo1")
        self.add_user("ana")
        self.post("/logout", "/")
        self.post("/login", "/login", username="ana", password="cardapio-77x")
        self.assertEqual(self.c.get("/usuarios").status_code, 403)
        self.assertEqual(self.post("/usuarios/licenca", "/", license=self.lic(5)).status_code, 403)

    def test_google_login_is_manager_only(self):
        self.post("/logout", "/")
        c = dbm.connect()
        c.execute("INSERT INTO users(username,display_name,password_hash,role,created_at,email) "
                  "VALUES ('ana','Ana','x','garcom','2026-01-01','ana@exemplo.com')")
        c.close()
        env = {"COMANDAFACIL_GOOGLE_CLIENT_ID": "id", "COMANDAFACIL_GOOGLE_CLIENT_SECRET": "s"}
        with mock.patch.dict(os.environ, env):
            for email, sub in (("ana@exemplo.com", "1"), ("dono@exemplo.com", "2")):
                self.c.get("/login/google")
                with self.c.session_transaction() as sess:
                    state = sess["g_state"]
                with mock.patch("comandafacil.google_auth.fetch_profile",
                                return_value={"sub": sub, "email": email, "email_verified": True}):
                    self.c.get(f"/login/google/retorno?code=x&state={state}")
                if email.startswith("ana"):
                    self.assertEqual(self.c.get("/").status_code, 302)  # garçom não entra com Google
                else:
                    self.assertEqual(self.c.get("/").status_code, 200)  # gerente entra

    def test_tool_signs_verifiable_license(self):
        from comandafacil import license as L
        token = self.tool.sign_license(self.key, "Zé", "ze@exemplo.com", 3, clock.today() + timedelta(days=30))
        self.assertEqual(L.parse(token)["max_users"], 3)


class FirstAccessAndResetCodeTests(unittest.TestCase):
    """Primeiro acesso com licença e códigos de redefinição emitidos pelo suporte."""

    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        cls.tool = _load_license_tool()
        cls.key = Ed25519PrivateKey.generate()
        cls.other = Ed25519PrivateKey.generate()

    def setUp(self):
        from comandafacil.views import auth
        auth._fails.clear()
        p = mock.patch.dict(os.environ, {"COMANDAFACIL_LICENSE_PUBKEY": self.tool.public_b64(self.key)})
        p.start()
        self.addCleanup(p.stop)
        self.app = create_app(tempfile.mkdtemp())
        self.c = self.app.test_client()

    def token(self, path):
        return re.search(r'name="_csrf" value="([^"]+)"', self.c.get(path).get_data(as_text=True)).group(1)

    def post(self, path, page, **data):
        data.setdefault("_csrf", self.token(page))
        return self.c.post(path, data=data)

    def lic(self, email="dono@exemplo.com", days=365, key=None, users=5):
        return self.tool.sign_license(key or self.key, "Restaurante Teste", email, users,
                                      clock.today() + timedelta(days=days))

    def setup_manager(self, **extra):
        data = dict(username="gerente", password="segredo1", password2="segredo1")
        data.update(extra)
        return self.post("/setup", "/setup", **data)

    def users(self):
        c = dbm.connect(); rows = c.execute("SELECT username,email FROM users").fetchall(); c.close()
        return [tuple(r) for r in rows]

    def login(self, user, pw):
        return self.post("/login", "/login", username=user, password=pw)

    # ---- primeiro acesso
    def test_setup_with_license_uses_owner_email_and_activates_it(self):
        r = self.setup_manager(license=self.lic(), email="digitado@errado.com")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/"))
        self.assertEqual(self.users(), [("gerente", "dono@exemplo.com")])
        page = self.c.get("/usuarios").get_data(as_text=True)
        self.assertIn("Ativa", page)
        self.assertIn("1 de 5", page)

    def test_setup_rejects_bad_licenses_without_creating_user(self):
        for bad in (self.lic(key=self.other), self.lic(days=-1), "CF1.lixo.lixo"):
            r = self.setup_manager(license=bad)
            self.assertEqual(r.status_code, 200)  # volta ao formulário
            self.assertEqual(self.users(), [])

    def test_setup_without_license_still_works_in_evaluation_mode(self):
        self.setup_manager(email="Gerente@Exemplo.com")
        self.assertEqual(self.users(), [("gerente", "gerente@exemplo.com")])
        self.assertIn("Avaliação", self.c.get("/usuarios").get_data(as_text=True))

    # ---- códigos de redefinição
    def code(self, email="dono@exemplo.com", hours=24, key=None):
        return self.tool.sign_reset(key or self.key, email, hours)

    def prepare(self):
        self.setup_manager(license=self.lic())
        self.post("/logout", "/")

    def test_reset_code_flow_and_single_use(self):
        self.prepare()
        self.assertEqual(self.c.get("/login/codigo").status_code, 200)
        code = self.code()
        r = self.post("/login/codigo", "/login/codigo", code=code, password="novasenha1", password2="novasenha1")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/login"))
        self.assertEqual(self.login("gerente", "novasenha1").status_code, 302)
        self.post("/logout", "/")
        self.assertEqual(self.login("gerente", "segredo1").status_code, 200)  # a antiga não vale mais
        r = self.post("/login/codigo", "/login/codigo", code=code, password="outrasenha1", password2="outrasenha1")
        self.assertIn("já foi usado", r.get_data(as_text=True))
        self.assertEqual(self.login("gerente", "outrasenha1").status_code, 200)

    def test_reset_code_is_not_burned_by_a_typo(self):
        self.prepare()
        code = self.code()
        self.post("/login/codigo", "/login/codigo", code=code, password="novasenha1", password2="diferente1")
        self.post("/login/codigo", "/login/codigo", code=code, password="curta", password2="curta")
        r = self.post("/login/codigo", "/login/codigo", code=code, password="novasenha1", password2="novasenha1")
        self.assertEqual(r.headers["Location"], "/login")

    def test_reset_code_rejects_expired_forged_tampered_and_unknown_email(self):
        self.prepare()
        good = self.code()
        head, payload, sig = good.split(".")
        forged = base64.urlsafe_b64encode(base64.urlsafe_b64decode(payload + "==").replace(
            b"dono@exemplo.com", b"dono@exemplo.com ")).rstrip(b"=").decode()
        for bad in (self.code(hours=-1), self.code(key=self.other), f"{head}.{forged}.{sig}", "CR1.a.b", "",
                    self.lic()):  # licença não vale como código
            r = self.post("/login/codigo", "/login/codigo", code=bad, password="novasenha1", password2="novasenha1")
            self.assertEqual(r.status_code, 200, bad[:12])
        from comandafacil.views import auth
        auth._fails.clear()  # as tentativas inválidas acima já acionaram o limite
        r = self.post("/login/codigo", "/login/codigo", code=self.code(email="outro@exemplo.com"),
                      password="novasenha1", password2="novasenha1")
        self.assertIn("Não há usuário ativo", r.get_data(as_text=True))
        self.assertEqual(self.login("gerente", "novasenha1").status_code, 200)  # nada mudou
        self.assertEqual(self.login("gerente", "segredo1").status_code, 302)

    def test_forgot_page_links_to_code_page(self):
        self.prepare()
        with mock.patch("comandafacil.mailer.is_configured", return_value=False):
            self.assertIn("/login/codigo", self.c.get("/login/esqueci").get_data(as_text=True))
        with mock.patch("comandafacil.mailer.is_configured", return_value=True):
            self.assertIn("/login/codigo", self.c.get("/login/esqueci").get_data(as_text=True))


class AccountAccessTests(unittest.TestCase):
    """Login por e-mail, recuperação de senha e login com Google."""

    def setUp(self):
        from comandafacil.views import auth
        auth._fails.clear()
        self.app = create_app(tempfile.mkdtemp())
        self.c = self.app.test_client()
        self.mails = []
        self.patches = [
            mock.patch("comandafacil.mailer.is_configured", return_value=True),
            mock.patch("comandafacil.mailer.send_async", side_effect=lambda *a, **k: self.mails.append(a)),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.token_ = self.token("/setup")
        self.c.post("/setup", data=dict(_csrf=self.token_, username="gerente", password="segredo1",
                                        password2="segredo1", email="Gerente@Exemplo.com"))
        self.post("/logout", "/")

    def token(self, path):
        return re.search(r'name="_csrf" value="([^"]+)"', self.c.get(path).get_data(as_text=True)).group(1)

    def post(self, path, page="/", **data):
        data.setdefault("_csrf", self.token(page))
        return self.c.post(path, data=data)

    def login(self, user, pw):
        return self.post("/login", "/login", username=user, password=pw)

    def row(self, user="gerente"):
        c = dbm.connect()
        try:
            return c.execute("SELECT * FROM users WHERE username=?", (user,)).fetchone()
        finally:
            c.close()

    def link_from_mail(self):
        body = self.mails[-1][2]
        return re.search(r"http://localhost(/login/redefinir/[\w-]+)", body).group(1)

    # ---- login e telas
    def test_login_by_email_is_case_insensitive_and_new_pages_render(self):
        self.assertEqual(self.login("GERENTE@exemplo.com", "segredo1").status_code, 302)
        self.post("/logout", "/")
        page = self.c.get("/login").get_data(as_text=True)
        self.assertIn("Esqueci minha senha", page)
        self.assertNotIn("Entrar com o Google", page)  # sem credenciais, sem botão
        self.assertEqual(self.c.get("/login/esqueci").status_code, 200)

    def test_next_rejects_backslash_redirects(self):
        r = self.post("/login?next=/%5Cevil.com", "/login", username="gerente", password="segredo1")
        self.assertEqual(r.headers["Location"], "/")

    # ---- recuperação de senha
    def test_reset_flow_end_to_end(self):
        r = self.post("/login/esqueci", "/login/esqueci", ident="gerente@exemplo.com")
        self.assertIn("Verifique seu e-mail", r.get_data(as_text=True))
        self.assertEqual(len(self.mails), 1)
        self.assertEqual(self.mails[0][0], "gerente@exemplo.com")
        link = self.link_from_mail()
        self.assertEqual(self.c.get(link).status_code, 200)
        # senha curta ou diferente não passa
        self.post(link, link, password="curta", password2="curta")
        self.post(link, link, password="novasenha1", password2="outra-coisa")
        self.assertEqual(self.login("gerente", "segredo1").status_code, 302)  # antiga ainda vale
        self.post("/logout", "/")
        r = self.post(link, link, password="novasenha1", password2="novasenha1")
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/login"))
        self.assertEqual(self.c.get("/").status_code, 302)  # não entra sozinho
        self.assertIn("/", self.login("gerente", "novasenha1").headers["Location"])
        self.post("/logout", "/")
        self.assertEqual(self.c.get(link).status_code, 400)  # link de uso único
        self.assertEqual(self.login("gerente", "segredo1").status_code, 200)  # senha antiga não vale mais

    def test_reset_does_not_reveal_unknown_accounts(self):
        r = self.post("/login/esqueci", "/login/esqueci", ident="ninguem@exemplo.com")
        self.assertIn("Verifique seu e-mail", r.get_data(as_text=True))
        self.assertEqual(self.mails, [])

    def test_reset_by_username_and_token_stored_hashed(self):
        self.post("/login/esqueci", "/login/esqueci", ident="gerente")
        token = self.link_from_mail().rsplit("/", 1)[1]
        c = dbm.connect()
        stored = c.execute("SELECT token_hash FROM password_resets").fetchone()[0]
        c.close()
        self.assertNotEqual(stored, token)
        self.assertEqual(len(stored), 64)

    def test_expired_link_and_per_account_limit(self):
        self.post("/login/esqueci", "/login/esqueci", ident="gerente")
        link = self.link_from_mail()
        c = dbm.connect()
        c.execute("UPDATE password_resets SET expires_at=?",
                  ((clock.now_dt() - timedelta(minutes=1)).isoformat(timespec="seconds"),))
        c.close()
        self.assertEqual(self.c.get(link).status_code, 400)
        for _ in range(4):
            self.post("/login/esqueci", "/login/esqueci", ident="gerente")
        self.assertEqual(len(self.mails), 3)  # no máximo 3 pedidos por hora por conta

    def test_forgot_without_smtp_explains_and_sends_nothing(self):
        with mock.patch("comandafacil.mailer.is_configured", return_value=False):
            page = self.c.get("/login/esqueci").get_data(as_text=True)
            self.assertIn("ainda não está ativada", page)
            self.post("/login/esqueci", "/login", ident="gerente")
        self.assertEqual(self.mails, [])

    def test_inactive_user_cannot_reset(self):
        c = dbm.connect()
        c.execute("UPDATE users SET active=0")
        c.close()
        self.post("/login/esqueci", "/login/esqueci", ident="gerente")
        self.assertEqual(self.mails, [])

    # ---- Google
    GOOGLE_ENV = {"COMANDAFACIL_GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
                  "COMANDAFACIL_GOOGLE_CLIENT_SECRET": "segredo"}

    def google_login(self, profile, state=None):
        """Percorre /login/google -> retorno, com o Google simulado."""
        r = self.c.get("/login/google")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].startswith("https://accounts.google.com/"))
        q = dict(p.split("=", 1) for p in r.headers["Location"].split("?", 1)[1].split("&"))
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertIn("redirect_uri", q)
        with mock.patch("comandafacil.google_auth.fetch_profile", return_value=profile):
            return self.c.get(f"/login/google/retorno?code=abc&state={state or q['state']}", follow_redirects=False)

    def test_google_button_only_when_configured(self):
        with mock.patch.dict(os.environ, self.GOOGLE_ENV):
            self.assertIn("Entrar com o Google", self.c.get("/login").get_data(as_text=True))
        self.assertEqual(self.c.get("/login/google").headers["Location"], "/login")

    def test_google_links_by_verified_email_then_matches_by_sub(self):
        with mock.patch.dict(os.environ, self.GOOGLE_ENV):
            r = self.google_login({"sub": "111", "email": "gerente@exemplo.com", "email_verified": True})
            self.assertEqual(r.headers["Location"], "/")
            self.assertEqual(self.c.get("/").status_code, 200)
            self.assertEqual(self.row()["google_sub"], "111")
            self.post("/logout", "/")
            # mesmo sub entra mesmo que o e-mail da conta Google tenha mudado
            r = self.google_login({"sub": "111", "email": "novo@gmail.com", "email_verified": True})
            self.assertEqual(r.headers["Location"], "/")
            self.post("/logout", "/")
            # outra conta Google com o mesmo e-mail cadastrado não toma o lugar
            r = self.google_login({"sub": "222", "email": "gerente@exemplo.com", "email_verified": True})
            self.assertEqual(r.headers["Location"], "/login")
            self.assertEqual(self.c.get("/").status_code, 302)

    def test_google_rejects_unknown_unverified_inactive_and_bad_state(self):
        with mock.patch.dict(os.environ, self.GOOGLE_ENV):
            for profile in ({"sub": "9", "email": "estranho@gmail.com", "email_verified": True},
                            {"sub": "9", "email": "gerente@exemplo.com", "email_verified": False}):
                r = self.google_login(profile)
                self.assertEqual(r.headers["Location"], "/login")
                self.assertEqual(self.c.get("/").status_code, 302)
            self.assertIn("não está liberada", self.c.get("/login").get_data(as_text=True))
            self.assertIsNone(self.row()["google_sub"])  # nada é criado nem vinculado
            c = dbm.connect(); n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close()
            self.assertEqual(n, 1)
            r = self.google_login({"sub": "1", "email": "gerente@exemplo.com", "email_verified": True}, state="forjado")
            self.assertEqual(self.c.get("/").status_code, 302)
            c = dbm.connect(); c.execute("UPDATE users SET active=0"); c.close()
            r = self.google_login({"sub": "1", "email": "gerente@exemplo.com", "email_verified": True})
            self.assertEqual(self.c.get("/").status_code, 302)

    def test_google_cancel_and_state_is_single_use(self):
        with mock.patch.dict(os.environ, self.GOOGLE_ENV):
            self.c.get("/login/google")
            r = self.c.get("/login/google/retorno?error=access_denied")
            self.assertEqual(r.headers["Location"], "/login")
            r = self.c.get("/login/google/retorno?code=abc&state=qualquer")  # sem fluxo em andamento
            self.assertEqual(self.c.get("/").status_code, 302)

    # ---- gerente cadastra e-mails
    def test_manager_sets_email_and_change_unlinks_google(self):
        self.login("gerente", "segredo1")
        self.post("/usuarios/adicionar", "/usuarios", username="joao", password="cardapio-77x", role="garcom",
                  email="Joao@Exemplo.com")
        self.assertEqual(self.row("joao")["email"], "joao@exemplo.com")
        self.post("/usuarios/adicionar", "/usuarios", username="maria", password="cardapio-77x", role="garcom",
                  email="joao@exemplo.com")  # duplicado
        self.assertIsNone(self.row("maria"))
        self.post("/usuarios/adicionar", "/usuarios", username="pedro", password="cardapio-77x", role="garcom",
                  email="isto-nao-e-email")
        self.assertIsNone(self.row("pedro"))
        c = dbm.connect(); c.execute("UPDATE users SET google_sub='abc' WHERE username='joao'"); c.close()
        uid = self.row("joao")["id"]
        self.post(f"/usuarios/{uid}/atualizar", "/usuarios", role="garcom", active="1", email="joao@exemplo.com")
        self.assertEqual(self.row("joao")["google_sub"], "abc")  # e-mail igual: vínculo mantido
        self.post(f"/usuarios/{uid}/atualizar", "/usuarios", role="garcom", active="1", email="joao2@exemplo.com")
        self.assertEqual((self.row("joao")["email"], self.row("joao")["google_sub"]), ("joao2@exemplo.com", None))
        self.assertIn("Google vinculado" if False else "joao2@exemplo.com", self.c.get("/usuarios").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
