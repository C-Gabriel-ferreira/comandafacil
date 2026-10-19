"""Testes do caixa, cupom e camada de aplicação do programa desktop (sem Qt).
Rodar:  python -m unittest discover -s tests -v"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comandafacil import cash, db as dbm, receipt, services as sv
from desktop.controller import Controller
from comandafacil.services import BusinessError


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        dbm.configure(self.dir)
        dbm.init_db()
        self.conn = dbm.connect()
        self.c = Controller(self.conn)
        self.c.create_manager("gerente", "Gerente", "senha-forte-1")
        self.c.login("gerente", "senha-forte-1")

    def tearDown(self):
        self.conn.close()

    def product(self, name):
        return self.conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()["id"]


class CashTests(Base):
    def test_schema_is_v3_and_old_v2_db_migrates(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 3)
        # simula banco v2 (sem tabelas de caixa) e confere a migração automática
        d = tempfile.mkdtemp()
        dbm.configure(d)
        dbm.init_db()
        c = dbm.connect()
        c.executescript("DROP TABLE cash_movements; DROP TABLE cash_sessions; PRAGMA user_version=2;")
        c.close()
        dbm.init_db()
        c = dbm.connect()
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertIsNone(cash.current_session(c))
        self.assertTrue(list(Path(d, "backups").glob("*pre_migracao.db")))
        c.close()

    def test_cannot_sell_with_cash_closed(self):
        with self.assertRaises(BusinessError):
            self.c.add_item(self.product("Café"), table_id=1)
        with self.assertRaises(BusinessError):
            self.c.new_counter_order("balcao")

    def test_only_one_open_cash(self):
        self.c.open_cash(5000)
        with self.assertRaises(BusinessError):
            self.c.open_cash(1000)

    def test_full_shift_expected_cash_and_difference(self):
        self.c.open_cash(10000)                                   # fundo R$ 100,00
        oid = self.c.add_item(self.product("Hambúrguer"), 2, table_id=1)   # 2 x 22,00 = 44,00
        self.c.pay(oid, [("Pix", 2000), ("Dinheiro", 3000)])      # paga 50, troco 6
        self.assertEqual(sv.get_order(self.conn, oid)["status"], "fechada")
        self.c.cash_movement("suprimento", 2000, "reforço de troco")
        self.c.cash_movement("sangria", 3000, "pagamento fornecedor")
        s, summ = self.c.cash_summary()
        self.assertEqual(summ["sales_total"], 4400)
        self.assertEqual(summ["by_method"]["Pix"], 2000)
        self.assertEqual(summ["by_method"]["Dinheiro"], 2400)     # 30,00 - troco 6,00
        # 100 + 24 + 20 - 30 = 114,00
        self.assertEqual(summ["expected_cash"], 11400)
        diff = self.c.close_cash(11300, "faltou 1 real")
        self.assertEqual(diff, -100)
        self.assertEqual(cash.difference_label(diff), "Falta R$ 1,00")
        self.assertIsNone(self.c.cash_session())

    def test_sangria_cannot_exceed_cash(self):
        self.c.open_cash(1000)
        with self.assertRaises(BusinessError):
            self.c.cash_movement("sangria", 5000, "retirada")
        with self.assertRaises(BusinessError):
            self.c.cash_movement("sangria", 500, "")

    def test_cannot_close_cash_with_open_orders(self):
        self.c.open_cash(1000)
        self.c.add_item(self.product("Café"), table_id=2)
        with self.assertRaises(BusinessError):
            self.c.close_cash(1000)

    def test_second_shift_does_not_include_first_shift_sales(self):
        self.c.open_cash(0)
        oid = self.c.add_item(self.product("Café"), table_id=1)
        self.c.pay(oid, [("Dinheiro", 400)])
        self.conn.execute("UPDATE orders SET closed_at='2020-01-01T10:00:00' WHERE id=?", (oid,))
        self.conn.execute("UPDATE cash_sessions SET opened_at='2019-12-31T08:00:00'")
        self.c.close_cash(400)
        self.conn.execute("UPDATE cash_sessions SET closed_at='2020-01-01T23:00:00'")
        self.c.open_cash(500)
        _, summ = self.c.cash_summary()
        self.assertEqual(summ["sales_total"], 0)
        self.assertEqual(summ["expected_cash"], 500)


class ControllerTests(Base):
    def setUp(self):
        super().setUp()
        self.c.open_cash(5000)

    def test_lazy_table_order_and_board(self):
        board = self.c.board()
        self.assertFalse(any(b["busy"] for b in board))
        oid = self.c.add_item(self.product("Café"), 1, "sem açúcar", table_id=3)
        b3 = [b for b in self.c.board() if b["table_id"] == 3][0]
        self.assertTrue(b3["busy"])
        self.assertEqual(b3["total"], 400)
        # segundo item entra na mesma comanda
        self.assertEqual(self.c.add_item(self.product("Água mineral"), 1, order_id=oid), oid)
        self.assertEqual(len(self.c.order_detail(oid)[1]), 2)

    def test_delivery_requires_name_and_address(self):
        with self.assertRaises(BusinessError):
            self.c.new_counter_order("delivery", "Ana", "", "")
        oid = self.c.new_counter_order("delivery", "Ana", "(11) 99999-0000", "Rua A, 10", 500)
        self.c.add_item(self.product("Hambúrguer"), order_id=oid)
        self.assertEqual(self.c.order_detail(oid)[2]["total"], 2700)   # 22 + 5 entrega
        names = [b["name"] for b in self.c.board() if b["kind"] == "delivery"]
        self.assertEqual(names, [f"Delivery #{oid}"])

    def test_waiter_permissions(self):
        self.conn.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) "
                          "VALUES ('joao','João','x','garcom','2020-01-01')")
        from werkzeug.security import generate_password_hash
        self.conn.execute("UPDATE users SET password_hash=? WHERE username='joao'",
                          (generate_password_hash("garcom-123"),))
        oid = self.c.add_item(self.product("Café"), table_id=4)
        self.c.logout()
        self.c.login("joao", "garcom-123")
        with self.assertRaises(BusinessError):
            self.c.cancel(oid, "teste")            # tem itens -> só gerente
        with self.assertRaises(BusinessError):
            self.c.open_cash(1)
        with self.assertRaises(BusinessError):
            self.c.report(None, None)
        # pode vender com caixa aberto e fechar conta
        self.c.pay(oid, [("Dinheiro", 400)])

    def test_login_throttle_and_wrong_password(self):
        self.c.logout()
        for _ in range(5):
            with self.assertRaises(BusinessError):
                self.c.login("gerente", "errada!!")
        with self.assertRaises(BusinessError) as cm:
            self.c.login("gerente", "senha-forte-1")
        self.assertIn("Muitas tentativas", str(cm.exception))

    def test_setup_only_once_and_short_password(self):
        with self.assertRaises(BusinessError):
            self.c.create_manager("outro", "Outro", "senha-forte-2")
        d = tempfile.mkdtemp()
        dbm.configure(d)
        dbm.init_db()
        c2 = Controller(dbm.connect())
        with self.assertRaises(BusinessError):
            c2.create_manager("abc", "Abc", "curta")

    def test_cancel_empty_order_and_transfer_merge(self):
        a = self.c.add_item(self.product("Café"), table_id=1)
        b = self.c.add_item(self.product("Água mineral"), table_id=2)
        self.c.merge(a, b)
        self.assertEqual(self.c.order_detail(a)[2]["subtotal"], 700)
        self.c.transfer(a, 5)
        self.assertEqual(self.c.order_for_table(5)["id"], a)
        self.c.cancel(a, "cliente desistiu")
        self.assertEqual(sv.get_order(self.conn, a)["status"], "cancelada")

    def test_split_bill_keeps_cents(self):
        oid = self.c.add_item(self.product("Café"), 1, table_id=1)       # 4,00
        self.c.add_item(self.product("Água mineral"), 2, order_id=oid)   # +6,00 = 10,00
        self.assertEqual(self.c.split_bill(oid, 3), [334, 333, 333])

    def test_stock_blocks_overselling_via_controller(self):
        pid = self.product("Café")
        self.c.save_product(pid, "Café", "Bebidas", 400, True, True, 2, 1)
        oid = self.c.add_item(pid, 2, table_id=1)
        with self.assertRaises(BusinessError):
            self.c.add_item(pid, 1, order_id=oid)

    def test_product_crud_manager_only_and_search(self):
        pid = self.c.save_product(None, "Pastel", "Lanches", 750, True, False, 0, 0)
        self.assertEqual([p["name"] for p in self.c.products("past")], ["Pastel"])
        self.assertIn("Lanches", self.c.categories())
        self.c.delete_product(pid)
        self.assertEqual(self.c.products("past"), [])


class ReceiptTests(Base):
    def test_receipt_open_and_closed(self):
        self.c.open_cash(0)
        oid = self.c.add_item(self.product("Hambúrguer"), 2, "sem cebola", table_id=1)
        self.c.set_service(oid, True)
        txt = self.c.receipt_text(oid)
        self.assertIn("CONFERENCIA DE CONTA", txt)
        self.assertIn("2x Hambúrguer", txt)
        self.assertIn("Obs: sem cebola", txt)
        self.assertIn("R$ 48,40", txt)          # 44,00 + 10% = 48,40
        self.assertTrue(all(len(line) <= receipt.WIDTH for line in txt.splitlines()))
        self.c.pay(oid, [("Pix", 4840)])
        txt = self.c.receipt_text(oid)
        self.assertNotIn("CONFERENCIA", txt)
        self.assertIn("Pix", txt)


if __name__ == "__main__":
    unittest.main()
