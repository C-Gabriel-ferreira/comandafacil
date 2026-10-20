"""Custo e margem (CMV). Rodar:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import db as dbm
from comandafacil.services import BusinessError
from desktop.controller import Controller


class CmvTests(unittest.TestCase):
    def setUp(self):
        dbm.configure(tempfile.mkdtemp())
        dbm.init_db()
        self.conn = dbm.connect()
        self.c = Controller(self.conn)
        self.c.create_manager("gerente", "Gerente", "senha-forte-1")
        self.c.login("gerente", "senha-forte-1")
        self.c.open_cash(0)

    def tearDown(self):
        self.conn.close()

    def pid(self, name):
        return self.conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]

    def set_cost(self, name, cost):
        p = self.conn.execute("SELECT * FROM products WHERE name=?", (name,)).fetchone()
        self.c.save_product(p["id"], p["name"], p["category"], p["price_cents"], True, False, 0, 0, cost)

    def sell(self, name, qty, table):
        oid = self.c.add_item(self.pid(name), qty, table_id=table)
        self.c.pay(oid, [("Pix", self.c.order_detail(oid)[2]["total"])])

    def test_margin_uses_cost_snapshot_and_ignores_items_without_cost(self):
        self.set_cost("Hambúrguer", 900)        # preço 22,00 -> custo 9,00
        self.sell("Hambúrguer", 2, 1)           # receita 44,00 / custo 18,00
        self.sell("Café", 1, 2)                 # sem custo: fica fora da margem
        rep = self.c.report(date.today(), date.today())
        self.assertEqual((rep["cogs"], rep["profit"]), (1800, 2600))
        self.assertEqual(rep["margin_pct"], 59.1)
        self.assertEqual(rep["cost_coverage_pct"], 92)   # 44 de 48 reais têm custo
        # mudar o custo depois não altera vendas antigas
        self.set_cost("Hambúrguer", 2000)
        self.assertEqual(self.c.report(date.today(), date.today())["cogs"], 1800)

    def test_no_costs_means_no_margin(self):
        self.sell("Café", 1, 1)
        rep = self.c.report(date.today(), date.today())
        self.assertIsNone(rep["margin_pct"])
        self.assertEqual(rep["cogs"], 0)

    def test_negative_cost_rejected(self):
        p = self.conn.execute("SELECT * FROM products WHERE name='Café'").fetchone()
        with self.assertRaises(BusinessError):
            self.c.save_product(p["id"], "Café", p["category"], p["price_cents"], True, False, 0, 0, -1)


if __name__ == "__main__":
    unittest.main()
