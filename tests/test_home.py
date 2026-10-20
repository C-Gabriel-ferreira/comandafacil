"""Indicadores da tela inicial e faixas de tempo da cozinha (sem Qt)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import db as dbm, services as sv
from desktop.controller import Controller


class HomeTests(unittest.TestCase):
    def setUp(self):
        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        self.conn = dbm.connect()
        self.c = Controller(self.conn)
        self.c.create_manager("gerente", "Gerente", "senha-forte-1")
        self.c.login("gerente", "senha-forte-1")

    def tearDown(self):
        self.conn.close()

    def pid(self, name):
        return self.conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]

    def test_empty_day(self):
        s = self.c.home_stats()
        self.assertEqual((s["total"], s["orders"], s["open_count"], s["kitchen"]), (0, 0, 0, 0))
        self.assertFalse(s["cash_open"])
        self.assertIsNone(s["delta_pct"])
        self.assertEqual(s["top"], [])

    def test_sales_open_and_kitchen(self):
        self.c.open_cash(0)
        a = self.c.add_item(self.pid("Hambúrguer"), 2, table_id=1)        # 44,00
        self.c.pay(a, [("Pix", 4400)])
        b = self.c.add_item(self.pid("Café"), 1, table_id=2)              # aberta, 4,00
        self.c.send_to_kitchen(b)
        s = self.c.home_stats()
        self.assertEqual((s["total"], s["orders"], s["avg"]), (4400, 1, 4400))
        self.assertEqual((s["open_count"], s["open_total"], s["kitchen"], s["kitchen_late"]), (1, 400, 1, 0))
        self.assertEqual(s["top"][0]["label"], "Hambúrguer")
        self.assertTrue(s["cash_open"])
        self.assertEqual(len(s["by_hour"]), 1)

    def test_low_stock_and_late_kitchen(self):
        self.c.open_cash(0)
        pid = self.pid("Café")
        self.c.save_product(pid, "Café", "Bebidas", 400, True, True, 3, 5)
        oid = self.c.add_item(self.pid("Água mineral"), 1, table_id=1)
        self.c.send_to_kitchen(oid)
        self.conn.execute("UPDATE order_items SET sent_at='2000-01-01T10:00:00' WHERE order_id=?", (oid,))
        s = self.c.home_stats()
        self.assertEqual(s["kitchen_late"], 1)
        self.assertEqual(s["low_stock"][0]["name"], "Café")

    def test_kitchen_levels_and_custom_limits(self):
        self.assertEqual([sv.kitchen_level(self.conn, m) for m in (0, 9, 10, 19, 20, 90)],
                         ["ok", "ok", "warn", "warn", "bad", "bad"])
        dbm.set_setting(self.conn, "kitchen_warn_min", 5)
        dbm.set_setting(self.conn, "kitchen_late_min", 8)
        self.assertEqual([sv.kitchen_level(self.conn, m) for m in (4, 5, 8)], ["ok", "warn", "bad"])


if __name__ == "__main__":
    unittest.main()
