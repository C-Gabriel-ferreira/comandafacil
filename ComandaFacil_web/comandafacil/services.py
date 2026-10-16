"""Regras de negócio (sem Flask): totais, estoque, fechamento, cancelamento, mesas, relatórios."""
import re
import sqlite3
from datetime import date, datetime, timedelta

from .db import get_setting, now, transaction
from .money import format_brl

PAYMENT_METHODS = ["Dinheiro", "Pix", "Cartão de débito", "Cartão de crédito", "Vale-refeição"]
KINDS = {"mesa": "Mesa", "balcao": "Balcão", "delivery": "Delivery"}
MAX_QTY = 999


class BusinessError(Exception):
    """Erro de regra de negócio, mostrado ao usuário como aviso."""


# ------------------------------------------------------------------ leitura
def get_order(conn, order_id):
    return conn.execute(
        "SELECT o.*, t.name AS table_name FROM orders o "
        "LEFT JOIN tables t ON t.id=o.table_id WHERE o.id=?", (order_id,)).fetchone()


def get_items(conn, order_id):
    return conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()


def order_totals(conn, order) -> dict:
    """Comandas fechadas usam o valor gravado no fechamento; abertas são calculadas ao vivo."""
    count = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM order_items WHERE order_id=?",
                         (order["id"],)).fetchone()[0]
    if order["status"] == "fechada" and order["total_cents"] is not None:
        return {"subtotal": order["subtotal_cents"], "service": order["service_cents"],
                "delivery": order["delivery_fee_cents"], "total": order["total_cents"], "count": count}
    sub = conn.execute("SELECT COALESCE(SUM(unit_price_cents*quantity),0) FROM order_items "
                       "WHERE order_id=?", (order["id"],)).fetchone()[0]
    svc = (sub * order["service_pct"] + 50) // 100 if order["service_enabled"] else 0
    delivery = order["delivery_fee_cents"] or 0
    return {"subtotal": sub, "service": svc, "delivery": delivery, "total": sub + svc + delivery,
            "count": count}


# ------------------------------------------------------------------ comandas
def open_order(conn, kind, table_id=None, customer_name="", phone="", address="",
               delivery_fee_cents=0, user_id=None):
    """Abre comanda. Para mesa, se já existir uma aberta devolve a existente (clique duplo seguro)."""
    if kind not in KINDS:
        raise BusinessError("Tipo de comanda inválido.")
    customer_name, phone, address = (customer_name or "").strip()[:80], _digits(phone), (address or "").strip()[:200]
    if kind == "mesa":
        if not conn.execute("SELECT 1 FROM tables WHERE id=? AND active=1", (table_id,)).fetchone():
            raise BusinessError("Mesa não encontrada ou desativada.")
    else:
        table_id = None
        if kind == "delivery" and (not customer_name or not address):
            raise BusinessError("Delivery precisa do nome do cliente e do endereço.")
    if kind != "delivery":
        delivery_fee_cents = 0
    pct = int(get_setting(conn, "service_pct", "10") or 0)
    auto = kind == "mesa" and get_setting(conn, "service_auto_mesa", "0") == "1"
    try:
        cur = conn.execute(
            "INSERT INTO orders(kind,table_id,customer_name,customer_phone,address,service_enabled,"
            "service_pct,delivery_fee_cents,created_at,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kind, table_id, customer_name, phone, address, int(auto), pct, delivery_fee_cents, now(), user_id))
        return cur.lastrowid, True
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id FROM orders WHERE table_id=? AND status='aberta'", (table_id,)).fetchone()
        if row:
            return row["id"], False
        raise


def open_order_for_table(conn, table_id):
    return conn.execute("SELECT * FROM orders WHERE table_id=? AND status='aberta'", (table_id,)).fetchone()


def _digits(s):
    return re.sub(r"\D", "", s or "")[:15]


def update_customer(conn, order_id, name, phone, address, delivery_fee_cents):
    order = get_order(conn, order_id)
    if not order or order["status"] != "aberta" or order["kind"] == "mesa":
        raise BusinessError("Não foi possível alterar os dados desta comanda.")
    if order["kind"] == "delivery" and (not name.strip() or not address.strip()):
        raise BusinessError("Delivery precisa do nome do cliente e do endereço.")
    fee = delivery_fee_cents if order["kind"] == "delivery" else 0
    conn.execute("UPDATE orders SET customer_name=?,customer_phone=?,address=?,delivery_fee_cents=? WHERE id=?",
                 (name.strip()[:80], _digits(phone), address.strip()[:200], fee, order_id))


def set_service(conn, order_id, enabled):
    pct = int(get_setting(conn, "service_pct", "10") or 0)
    cur = conn.execute("UPDATE orders SET service_enabled=?, service_pct=? WHERE id=? AND status='aberta'",
                       (int(enabled), pct, order_id))
    if cur.rowcount != 1:
        raise BusinessError("Esta comanda não está aberta.")


# -------------------------------------------------------------------- itens
def _stock_move(conn, product_id, delta, reason, order_id=None, user_id=None):
    conn.execute("UPDATE products SET stock = stock + ? WHERE id=?", (delta, product_id))
    conn.execute("INSERT INTO stock_movements(product_id,delta,reason,order_id,user_id,created_at) "
                 "VALUES (?,?,?,?,?,?)", (product_id, delta, reason, order_id, user_id, now()))


def add_item(conn, order_id, product_id, qty, note="", user_id=None):
    qty = max(1, min(int(qty or 1), MAX_QTY))
    note = (note or "").strip()[:140]
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order or order["status"] != "aberta":
            raise BusinessError("Esta comanda não está aberta.")
        p = conn.execute("SELECT * FROM products WHERE id=? AND available=1", (product_id,)).fetchone()
        if not p:
            raise BusinessError("Produto não encontrado ou indisponível.")
        if p["track_stock"]:
            if p["stock"] < qty:
                raise BusinessError(f"Estoque insuficiente de {p['name']} (restam {p['stock']}).")
            _stock_move(conn, p["id"], -qty, "venda", order_id, user_id)
        same = conn.execute(
            "SELECT id, quantity FROM order_items WHERE order_id=? AND product_id=? "
            "AND unit_price_cents=? AND note=?", (order_id, p["id"], p["price_cents"], note)).fetchone()
        if same and same["quantity"] + qty <= MAX_QTY:
            conn.execute("UPDATE order_items SET quantity=quantity+? WHERE id=?", (qty, same["id"]))
        else:
            conn.execute(
                "INSERT INTO order_items(order_id,product_id,product_name,unit_price_cents,quantity,note,created_at)"
                " VALUES (?,?,?,?,?,?,?)", (order_id, p["id"], p["name"], p["price_cents"], qty, note, now()))


def _open_item(conn, item_id):
    item = conn.execute("SELECT * FROM order_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        raise BusinessError("Item não encontrado.")
    order = get_order(conn, item["order_id"])
    if order["status"] != "aberta":
        raise BusinessError("Não é possível alterar uma comanda fechada.")
    return item, order


def _tracked(conn, product_id):
    if product_id is None:
        return None
    return conn.execute("SELECT * FROM products WHERE id=? AND track_stock=1", (product_id,)).fetchone()


def remove_item(conn, item_id, user_id=None):
    with transaction(conn):
        item, order = _open_item(conn, item_id)
        if _tracked(conn, item["product_id"]):
            _stock_move(conn, item["product_id"], item["quantity"], "item removido", order["id"], user_id)
        conn.execute("DELETE FROM order_items WHERE id=?", (item_id,))
    return order["id"]


def change_qty(conn, item_id, delta, user_id=None):
    with transaction(conn):
        item, order = _open_item(conn, item_id)
        new = item["quantity"] + delta
        if new <= 0:
            if _tracked(conn, item["product_id"]):
                _stock_move(conn, item["product_id"], item["quantity"], "item removido", order["id"], user_id)
            conn.execute("DELETE FROM order_items WHERE id=?", (item_id,))
            return order["id"]
        if new > MAX_QTY:
            raise BusinessError(f"Quantidade máxima por item: {MAX_QTY}.")
        p = _tracked(conn, item["product_id"])
        if p:
            if delta > 0 and p["stock"] < delta:
                raise BusinessError(f"Estoque insuficiente de {p['name']} (restam {p['stock']}).")
            _stock_move(conn, p["id"], -delta, "ajuste de quantidade", order["id"], user_id)
        conn.execute("UPDATE order_items SET quantity=? WHERE id=?", (new, item_id))
    return order["id"]


# ------------------------------------------------------------------ fechamento
def close_order(conn, order_id, payments, user_id=None) -> int:
    """Fecha a conta com 1+ pagamentos (pagamento dividido). Devolve o troco em centavos."""
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order or order["status"] != "aberta":
            raise BusinessError("Esta comanda já foi fechada ou não existe.")
        t = order_totals(conn, order)
        if t["count"] == 0:
            raise BusinessError("Adicione pelo menos um item antes de fechar a conta.")
        payments = [(m, c) for m, c in payments if c > 0]
        for m, _ in payments:
            if m not in PAYMENT_METHODS:
                raise BusinessError("Forma de pagamento inválida.")
        if t["total"] > 0 and not payments:
            raise BusinessError("Informe a forma de pagamento.")
        paid = sum(c for _, c in payments)
        change = 0
        if paid < t["total"]:
            raise BusinessError(f"Faltam {format_brl(t['total'] - paid)} para quitar a conta.")
        if paid > t["total"]:
            excess = paid - t["total"]
            cash = [i for i, (m, _) in enumerate(payments) if m == "Dinheiro"]
            if not cash or payments[cash[0]][1] < excess:
                raise BusinessError("O valor pago é maior que a conta. Só é possível dar troco em dinheiro.")
            i = cash[0]
            payments[i] = ("Dinheiro", payments[i][1] - excess)
            change = excess
            payments = [p for p in payments if p[1] > 0]
        cur = conn.execute(
            "UPDATE orders SET status='fechada', closed_at=?, closed_by=?, subtotal_cents=?, "
            "service_cents=?, total_cents=? WHERE id=? AND status='aberta'",
            (now(), user_id, t["subtotal"], t["service"], t["total"], order_id))
        if cur.rowcount != 1:
            raise BusinessError("Esta comanda já foi fechada.")
        conn.executemany("INSERT INTO payments(order_id,method,amount_cents) VALUES (?,?,?)",
                         [(order_id, m, c) for m, c in payments])
    return change


def cancel_order(conn, order_id, reason, user_id=None, restore_stock=True) -> str:
    """Cancela comanda aberta ou estorna uma fechada. Devolve o status anterior."""
    reason = (reason or "").strip()[:200]
    if not reason:
        raise BusinessError("Informe o motivo do cancelamento.")
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order or order["status"] not in ("aberta", "fechada"):
            raise BusinessError("Esta comanda não pode ser cancelada.")
        if order["status"] == "aberta":
            totals = order_totals(conn, order)
            conn.execute("UPDATE orders SET subtotal_cents=?, service_cents=?, total_cents=? WHERE id=?",
                         (totals["subtotal"], totals["service"], totals["total"], order_id))
        if restore_stock:
            for it in get_items(conn, order_id):
                if _tracked(conn, it["product_id"]):
                    _stock_move(conn, it["product_id"], it["quantity"],
                                "cancelamento" if order["status"] == "aberta" else "estorno",
                                order_id, user_id)
        conn.execute("UPDATE orders SET status='cancelada', cancelled_at=?, cancelled_by=?, cancel_reason=? "
                     "WHERE id=?", (now(), user_id, reason, order_id))
    return order["status"]


# ------------------------------------------------------- transferir / juntar
def transfer_order(conn, order_id, new_table_id):
    with transaction(conn):
        order = get_order(conn, order_id)
        if not order or order["status"] != "aberta" or order["kind"] != "mesa":
            raise BusinessError("Só é possível transferir comandas de mesa abertas.")
        if not conn.execute("SELECT 1 FROM tables WHERE id=? AND active=1", (new_table_id,)).fetchone():
            raise BusinessError("Mesa de destino não encontrada.")
        if open_order_for_table(conn, new_table_id):
            raise BusinessError("A mesa de destino já está ocupada. Use “Juntar mesas”.")
        conn.execute("UPDATE orders SET table_id=? WHERE id=?", (new_table_id, order_id))


def merge_orders(conn, target_id, source_id):
    """Move os itens de source para target; source vira 'juntada' e a mesa dela é liberada."""
    if target_id == source_id:
        raise BusinessError("Escolha outra comanda para juntar.")
    with transaction(conn):
        target, source = get_order(conn, target_id), get_order(conn, source_id)
        if not target or not source or target["status"] != "aberta" or source["status"] != "aberta":
            raise BusinessError("As duas comandas precisam estar abertas.")
        if source["kind"] != "mesa" or target["kind"] != "mesa":
            raise BusinessError("Só é possível juntar comandas de mesa.")
        subtotal = order_totals(conn, source)["subtotal"]
        conn.execute("UPDATE order_items SET order_id=? WHERE order_id=?", (target_id, source_id))
        conn.execute("UPDATE orders SET status='juntada', merged_into=?, closed_at=?, subtotal_cents=?, "
                     "service_cents=0, total_cents=0 WHERE id=?", (target_id, now(), subtotal, source_id))
    return source


# ------------------------------------------------------------------ estoque
def adjust_stock(conn, product_id, delta, reason, user_id=None):
    with transaction(conn):
        p = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not p or not p["track_stock"]:
            raise BusinessError("Este produto não controla estoque.")
        if p["stock"] + delta < 0:
            raise BusinessError("O estoque não pode ficar negativo.")
        _stock_move(conn, product_id, delta, reason or "ajuste manual", None, user_id)


def low_stock_products(conn):
    return conn.execute(
        "SELECT * FROM products WHERE track_stock=1 AND stock <= stock_min ORDER BY stock, name").fetchall()


# ----------------------------------------------------------------- WhatsApp
def phone_for_whatsapp(phone) -> str:
    d = _digits(phone)
    if len(d) in (10, 11):  # número brasileiro sem DDI
        d = "55" + d
    return d


def whatsapp_message(conn, order, items, totals) -> str:
    biz = get_setting(conn, "business_name", "ComandaFácil")
    first = (order["customer_name"] or "").split(" ")[0]
    lines = [f"Olá{', ' + first if first else ''}! Aqui é do {biz}.", "Confirmando o seu pedido:", ""]
    for it in items:
        extra = f" ({it['note']})" if it["note"] else ""
        lines.append(f"• {it['quantity']}x {it['product_name']}{extra} — "
                     f"{format_brl(it['unit_price_cents'] * it['quantity'])}")
    lines.append("")
    lines.append(f"Subtotal: {format_brl(totals['subtotal'])}")
    if totals["service"]:
        lines.append(f"Taxa de serviço ({order['service_pct']}%): {format_brl(totals['service'])}")
    if totals["delivery"]:
        lines.append(f"Entrega: {format_brl(totals['delivery'])}")
    lines.append(f"*Total: {format_brl(totals['total'])}*")
    if order["kind"] == "delivery" and order["address"]:
        lines += ["", f"Endereço de entrega: {order['address']}"]
    lines += ["", "Qualquer dúvida é só responder por aqui. Obrigado!"]
    return "\n".join(lines)


# ---------------------------------------------------------------- relatórios
def _range(start: date, end: date):
    return start.isoformat(), (end + timedelta(days=1)).isoformat()


def sales_report(conn, start: date, end: date) -> dict:
    a, b = _range(start, end)
    base = "FROM orders o WHERE o.status='fechada' AND o.closed_at >= ? AND o.closed_at < ?"
    r = conn.execute(f"SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) total, "
                     f"COALESCE(SUM(service_cents),0) svc, COALESCE(SUM(delivery_fee_cents),0) dlv {base}",
                     (a, b)).fetchone()
    n, total = r["n"], r["total"]
    rep = {"orders": n, "total": total, "service": r["svc"], "delivery": r["dlv"],
           "avg": total // n if n else 0}
    rep["by_payment"] = _with_pct(conn.execute(
        "SELECT p.method AS label, SUM(p.amount_cents) AS value, COUNT(DISTINCT p.order_id) AS n "
        "FROM payments p JOIN orders o ON o.id=p.order_id "
        "WHERE o.status='fechada' AND o.closed_at >= ? AND o.closed_at < ? "
        "GROUP BY p.method ORDER BY value DESC", (a, b)).fetchall())
    rep["by_day"] = _with_pct(conn.execute(
        f"SELECT substr(o.closed_at,1,10) AS label, SUM(total_cents) AS value, COUNT(*) AS n {base} "
        "GROUP BY label ORDER BY label", (a, b)).fetchall())
    rep["by_hour"] = _with_pct(conn.execute(
        f"SELECT substr(o.closed_at,12,2) || 'h' AS label, SUM(total_cents) AS value, COUNT(*) AS n {base} "
        "GROUP BY label ORDER BY label", (a, b)).fetchall())
    rep["by_kind"] = _with_pct(conn.execute(
        f"SELECT o.kind AS label, SUM(total_cents) AS value, COUNT(*) AS n {base} "
        "GROUP BY o.kind ORDER BY value DESC", (a, b)).fetchall())
    rep["top_products"] = _with_pct(conn.execute(
        "SELECT i.product_name AS label, SUM(i.unit_price_cents*i.quantity) AS value, SUM(i.quantity) AS n "
        "FROM order_items i JOIN orders o ON o.id=i.order_id "
        "WHERE o.status='fechada' AND o.closed_at >= ? AND o.closed_at < ? "
        "GROUP BY i.product_name ORDER BY n DESC, value DESC LIMIT 15", (a, b)).fetchall(), key="n")
    c = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) v FROM orders "
                     "WHERE status='cancelada' AND cancelled_at >= ? AND cancelled_at < ?", (a, b)).fetchone()
    rep["cancelled_n"], rep["cancelled_value"] = c["n"], c["v"]
    return rep


def _with_pct(rows, key="value"):
    out = [dict(r) for r in rows]
    top = max((r[key] for r in out), default=0) or 1
    for r in out:
        r["pct"] = round(r[key] * 100 / top)
    return out


def sales_rows(conn, start: date, end: date):
    a, b = _range(start, end)
    return conn.execute(
        "SELECT o.*, t.name AS table_name, "
        "(SELECT GROUP_CONCAT(method || ' ' || amount_cents, '; ') FROM payments WHERE order_id=o.id) AS pays "
        "FROM orders o LEFT JOIN tables t ON t.id=o.table_id "
        "WHERE o.status='fechada' AND o.closed_at >= ? AND o.closed_at < ? ORDER BY o.closed_at",
        (a, b)).fetchall()
