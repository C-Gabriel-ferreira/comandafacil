"""Sugestão de venda adicional. Rodar:  python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import db as dbm
from desktop.controller import Controller


class SuggestionTests(unittest.TestCase):
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

    def sell(self, names, table):
        oid = None
        for n in names:
            oid = self.c.add_item(self.pid(n), 1, order_id=oid, table_id=table) if oid else self.c.add_item(self.pid(n), 1, table_id=table)
        self.c.pay(oid, [("Pix", self.c.order_detail(oid)[2]["total"])])

    def test_suggests_what_sells_together(self):
        for t in (1, 2, 3):
            self.sell(["Hambúrguer", "Refrigerante lata"], t)
        self.sell(["Hambúrguer", "Água mineral"], 4)
        oid = self.c.add_item(self.pid("Hambúrguer"), 1, table_id=5)
        names = [p["name"] for p in self.c.suggestions(oid)]
        self.assertEqual(names[0], "Refrigerante lata")
        self.assertEqual(names[1], "Água mineral")
        self.assertNotIn("Hambúrguer", names)

    def test_falls_back_to_best_sellers_and_skips_out_of_stock(self):
        self.sell(["Café"], 1)
        oid = self.c.add_item(self.pid("Hambúrguer"), 1, table_id=2)
        self.assertEqual(self.c.suggestions(oid)[0]["name"], "Café")
        self.conn.execute("UPDATE products SET track_stock=1, stock=0 WHERE name='Café'")
        self.assertNotIn("Café", [p["name"] for p in self.c.suggestions(oid)])

    def test_empty_order_has_no_suggestions(self):
        self.assertEqual(self.c.suggestions(999), [])


if __name__ == "__main__":
    unittest.main()
