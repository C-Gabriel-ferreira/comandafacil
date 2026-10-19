"""Pedidos online no PDV (sem Qt, com uma 'nuvem' falsa em memória).
Rodar:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import db as dbm, online, services as sv
from comandafacil.services import BusinessError
from desktop.controller import Controller


class FakeCloud:
    """Imita as funções cf_pdv_* (mesmo formato de resposta do Postgres)."""

    def __init__(self):
        self.orders, self.menu, self.settings, self.accepting = [], [], {}, False
        self.status_log, self.down = [], False

    def __call__(self, fn, p):
        if self.down:
            raise online.CloudError("Sem conexão com a internet.")
        if p.get("p_token") != "tok":
            raise online.CloudError("Acesso negado")
        if fn == "cf_pdv_publish_menu":
            self.menu, self.settings = p["p_items"], p["p_settings"]
            return {"ok": True, "items": len(p["p_items"])}
        if fn == "cf_pdv_set_accepting":
            self.accepting = p["p_flag"]
            return {"ok": True, "accepting": self.accepting}
        if fn == "cf_pdv_pull_orders":
            return {"ok": True, "accepting": self.accepting,
                    "orders": [o for o in self.orders if o["status"] == "novo"]}
        if fn == "cf_pdv_set_status":
            self.status_log.append((p["p_code"], p["p_status"]))
            for o in self.orders:
                if o["code"] == p["p_code"]:
                    o["status"] = p["p_status"]
            return {"ok": True}
        raise AssertionError(fn)

    def add(self, code, seq, items, kind="entrega", **kw):
        sub = sum(i["price_cents"] * i["qty"] for i in items)
        fee = 500 if kind == "entrega" else 0
        o = {"code": code, "seq": seq, "kind": kind, "name": "Ana", "phone": "11988887777",
             "address": "Rua A, 10" if kind == "entrega" else "", "note": "", "payment": "Dinheiro",
             "change_for_cents": 10000, "subtotal_cents": sub, "delivery_fee_cents": fee,
             "total_cents": sub + fee, "created_at": "2026-10-19T20:00:00-03:00", "items": items,
             "status": "novo"}
        o.update(kw)
        self.orders.append(o)
        return o


class OnlineBase(unittest.TestCase):
    def setUp(self):
        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        self.conn = dbm.connect()
        self.cloud = FakeCloud()
        self.c = Controller(self.conn)
        self.c.cloud_transport = self.cloud
        self.c.create_manager("gerente", "Gerente", "senha-forte-1")
        self.c.login("gerente", "senha-forte-1")
        self.c.online_activate(online.make_activation_code("https://x.supabase.co", "anon", "loja", "tok"))
        self.burger = self.pid("Hambúrguer")
        self.coffee = self.pid("Café")

    def tearDown(self):
        self.conn.close()

    def pid(self, name):
        return self.conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]

    def item(self, pid, qty, price, name="x", note=""):
        return {"id": pid, "qty": qty, "price_cents": price, "name": name, "note": note}


class ActivationTests(unittest.TestCase):
    def test_code_roundtrip_and_validation(self):
        code = online.make_activation_code("https://x.supabase.co", "k", "minha-loja", "t0k")
        self.assertEqual(online.parse_activation_code(code)["online_slug"], "minha-loja")
        for bad in ("", "abc", "CF1.@@@", online.make_activation_code("http://x", "k", "s", "t")):
            with self.assertRaises(BusinessError):
                online.parse_activation_code(bad)

    def test_not_configured_blocks_client(self):
        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        with self.assertRaises(BusinessError):
            online.CloudClient.from_conn(dbm.connect())


class FlowTests(OnlineBase):
    def test_publish_skips_out_of_stock_and_sends_rules(self):
        self.c.open_cash(0)
        self.c.save_product(self.coffee, "Café", "Bebidas", 400, True, True, 0, 0)   # estoque zerado
        self.c.online_save_settings(True, False, 500, 1500, "18h às 23h", "Sem fiado", "https://site.app")
        self.assertEqual(self.c.online_publish(), len(self.c.all_products()))
        by_id = {i["id"]: i for i in self.cloud.menu}
        self.assertFalse(by_id[self.coffee]["available"])
        self.assertTrue(by_id[self.burger]["available"])
        self.assertEqual(self.cloud.settings["delivery_fee_cents"], 500)
        self.assertFalse(self.cloud.settings["pickup_enabled"])
        self.assertEqual(self.cloud.settings["min_order_cents"], 1500)

    def test_only_manager_publishes_and_activates(self):
        self.c.logout()
        with self.assertRaises(BusinessError):
            self.c.online_publish()
        with self.assertRaises(BusinessError):
            self.c.online_deactivate()

    def test_cannot_accept_orders_with_cash_closed(self):
        with self.assertRaises(BusinessError):
            self.c.online_set_accepting(True)
        self.cloud.add("AAA", 1, [self.item(self.burger, 1, 2200, "Hambúrguer")])
        self.c.online_sync()
        with self.assertRaises(BusinessError):
            self.c.online_accept("AAA")

    def test_sync_is_idempotent_and_accept_creates_kitchen_order(self):
        self.c.open_cash(0)
        self.c.online_set_accepting(True)
        self.assertTrue(self.cloud.accepting)
        self.cloud.add("AAA", 7, [self.item(self.burger, 2, 2200, "Hambúrguer", "sem cebola"),
                                   self.item(self.coffee, 1, 400, "Café")], note="tocar a campainha")
        self.assertEqual(self.c.online_sync()["new"], ["AAA"])
        self.assertEqual(self.c.online_sync()["new"], [])            # sem duplicar
        self.assertEqual(self.c.online_new_count(), 1)
        oid = self.c.online_accept("AAA")
        self.assertEqual(self.c.online_new_count(), 0)
        order = sv.get_order(self.conn, oid)
        self.assertEqual((order["kind"], order["delivery_fee_cents"], order["address"]), ("delivery", 500, "Rua A, 10"))
        items = sv.get_items(self.conn, oid)
        self.assertEqual({i["kitchen_status"] for i in items}, {"enviado"})
        self.assertEqual(sv.order_totals(self.conn, order)["total"], 4800 + 500)
        self.assertTrue(any("tocar a campainha" in i["note"] for i in items))
        with self.assertRaises(BusinessError):
            self.c.online_accept("AAA")                              # já tratado
        self.c.online_sync()
        self.assertIn(("AAA", "aceito"), self.cloud.status_log)

    def test_customer_price_wins_over_current_local_price(self):
        self.c.open_cash(0)
        self.cloud.add("P1", 1, [self.item(self.burger, 1, 1900, "Hambúrguer")], kind="retirada")
        self.c.online_sync()
        oid = self.c.online_accept("P1")
        self.assertEqual(sv.get_items(self.conn, oid)[0]["unit_price_cents"], 1900)
        self.assertEqual(sv.get_order(self.conn, oid)["kind"], "balcao")

    def test_reject_sends_reason_and_survives_offline(self):
        self.c.open_cash(0)
        self.cloud.add("R1", 1, [self.item(self.coffee, 1, 400)])
        self.c.online_sync()
        self.c.online_reject("R1", "acabou o café")
        self.cloud.down = True
        with self.assertRaises(online.CloudError):
            self.c.online_sync()                                     # offline: nada se perde
        self.assertEqual(online.get(self.conn, "R1")["status"], "recusado")
        self.cloud.down = False
        self.c.online_sync()
        self.assertEqual(self.cloud.orders[0]["status"], "recusado")
        self.assertEqual(online.get(self.conn, "R1")["pushed_status"], "recusado")

    def test_progress_follows_kitchen_and_payment(self):
        self.c.open_cash(0)
        self.cloud.add("K1", 1, [self.item(self.coffee, 1, 400)], kind="retirada")
        self.c.online_sync()
        oid = self.c.online_accept("K1")
        self.c.kitchen_step(oid, "iniciar")
        self.c.online_sync()
        self.assertEqual(online.get(self.conn, "K1")["status"], "preparo")
        self.c.kitchen_step(oid, "pronto")
        self.c.online_sync()
        self.assertEqual(online.get(self.conn, "K1")["status"], "pronto")
        self.c.pay(oid, [("Dinheiro", 400)])
        self.c.online_sync()
        self.assertEqual(online.get(self.conn, "K1")["status"], "entregue")
        self.c.online_sync()
        self.assertEqual(self.cloud.orders[0]["status"], "entregue")
        self.assertEqual(self.c.online_orders(), [])                 # saiu da lista de ativos
        self.assertEqual(len(self.c.online_orders(False)), 1)

    def test_dispatch_only_for_delivery(self):
        self.c.open_cash(0)
        self.cloud.add("D1", 1, [self.item(self.coffee, 1, 400)], kind="entrega")
        self.cloud.add("D2", 2, [self.item(self.coffee, 1, 400)], kind="retirada")
        self.c.online_sync()
        self.c.online_accept("D1")
        self.c.online_accept("D2")
        self.c.online_dispatched("D1")
        with self.assertRaises(BusinessError):
            self.c.online_dispatched("D2")

    def test_ticket_and_whatsapp(self):
        self.c.open_cash(0)
        self.cloud.add("T1", 3, [self.item(self.burger, 1, 2200, "Hambúrguer", "bem passado")])
        self.c.online_sync()
        txt = self.c.online_ticket("T1")
        for s in ("PEDIDO ONLINE #3", "ENTREGA", "Obs: bem passado", "Troco para", "Levar de troco", "R$ 27,00", "R$ 73,00"):
            self.assertIn(s, txt)
        self.assertTrue(all(len(x) <= 42 for x in txt.splitlines()))
        self.c.online_accept("T1")
        phone, msg = self.c.online_wa("T1")
        self.assertTrue(phone.startswith("55"))
        self.assertIn("#3", msg)


class MigrationTests(unittest.TestCase):
    def test_v3_database_gets_online_table(self):
        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        c = dbm.connect()
        c.executescript("DROP TABLE online_orders; PRAGMA user_version=3;")
        c.close()
        dbm.init_db()
        c = dbm.connect()
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], dbm.SCHEMA_VERSION)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM online_orders").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
