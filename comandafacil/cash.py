"""Controle de caixa (sem Flask/Qt): abertura, sangria, suprimento e fechamento com conferência.

Dinheiro esperado na gaveta = fundo de troco + vendas em dinheiro (já líquidas de troco)
                              + suprimentos − sangrias.
Vendas contam pelo horário de fechamento da comanda dentro do turno do caixa.
"""
from .db import now, transaction
from .money import format_brl
from .services import BusinessError, PAYMENT_METHODS

MAX_CENTS = 100_000_000


def current_session(conn):
    """Turno de caixa aberto (Row) ou None."""
    return conn.execute("SELECT * FROM cash_sessions WHERE closed_at IS NULL").fetchone()


def get_session(conn, session_id):
    return conn.execute("SELECT * FROM cash_sessions WHERE id=?", (session_id,)).fetchone()


def open_session(conn, opening_cents, user_id=None) -> int:
    if opening_cents < 0 or opening_cents > MAX_CENTS:
        raise BusinessError("Valor do fundo de troco inválido.")
    with transaction(conn):
        if current_session(conn):
            raise BusinessError("Já existe um caixa aberto.")
        cur = conn.execute(
            "INSERT INTO cash_sessions(opened_at,opened_by,opening_cents) VALUES (?,?,?)",
            (now(), user_id, opening_cents))
        return cur.lastrowid


def add_movement(conn, kind, amount_cents, reason, user_id=None) -> int:
    """Sangria (retirada) ou suprimento (reforço de troco) no caixa aberto."""
    if kind not in ("sangria", "suprimento"):
        raise BusinessError("Tipo de movimentação inválido.")
    reason = (reason or "").strip()[:200]
    if amount_cents <= 0 or amount_cents > MAX_CENTS:
        raise BusinessError("Informe um valor maior que zero.")
    if not reason:
        raise BusinessError("Informe o motivo da movimentação.")
    with transaction(conn):
        sess = current_session(conn)
        if not sess:
            raise BusinessError("Abra o caixa antes de registrar movimentações.")
        if kind == "sangria":
            if amount_cents > summary(conn, sess["id"])["expected_cash"]:
                raise BusinessError("A sangria é maior que o dinheiro disponível no caixa.")
        cur = conn.execute(
            "INSERT INTO cash_movements(session_id,kind,amount_cents,reason,user_id,created_at) "
            "VALUES (?,?,?,?,?,?)", (sess["id"], kind, amount_cents, reason, user_id, now()))
        return cur.lastrowid


def movements(conn, session_id):
    return conn.execute("SELECT * FROM cash_movements WHERE session_id=? ORDER BY id",
                        (session_id,)).fetchall()


def summary(conn, session_id) -> dict:
    """Resumo do turno: vendas por forma de pagamento, movimentações e dinheiro esperado."""
    s = get_session(conn, session_id)
    if not s:
        raise BusinessError("Turno de caixa não encontrado.")
    end = s["closed_at"] or "9999-12-31T23:59:59"
    by_method = {m: 0 for m in PAYMENT_METHODS}
    for r in conn.execute(
            "SELECT p.method, SUM(p.amount_cents) v FROM payments p JOIN orders o ON o.id=p.order_id "
            "WHERE o.status='fechada' AND o.closed_at >= ? AND o.closed_at <= ? GROUP BY p.method",
            (s["opened_at"], end)):
        by_method[r["method"]] = by_method.get(r["method"], 0) + r["v"]
    n = conn.execute("SELECT COUNT(*) FROM orders WHERE status='fechada' AND closed_at >= ? "
                     "AND closed_at <= ?", (s["opened_at"], end)).fetchone()[0]
    cancelled = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) v FROM orders WHERE status='cancelada' "
        "AND cancelled_at >= ? AND cancelled_at <= ?", (s["opened_at"], end)).fetchone()
    sup = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM cash_movements "
                       "WHERE session_id=? AND kind='suprimento'", (session_id,)).fetchone()[0]
    san = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM cash_movements "
                       "WHERE session_id=? AND kind='sangria'", (session_id,)).fetchone()[0]
    sales = sum(by_method.values())
    expected = s["opening_cents"] + by_method.get("Dinheiro", 0) + sup - san
    return {"opening": s["opening_cents"], "by_method": by_method, "sales_total": sales,
            "orders": n, "avg": sales // n if n else 0, "suprimentos": sup, "sangrias": san,
            "cancelled_n": cancelled["n"], "cancelled_value": cancelled["v"],
            "expected_cash": expected}


def close_session(conn, session_id, counted_cents, user_id=None, note="") -> int:
    """Fecha o turno. Devolve a diferença (contado − esperado): negativa = falta, positiva = sobra."""
    if counted_cents < 0 or counted_cents > MAX_CENTS:
        raise BusinessError("Valor contado inválido.")
    with transaction(conn):
        s = get_session(conn, session_id)
        if not s or s["closed_at"] is not None:
            raise BusinessError("Este caixa já foi fechado.")
        open_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status='aberta'").fetchone()[0]
        if open_orders:
            raise BusinessError(f"Ainda há {open_orders} comanda(s) aberta(s). "
                                "Feche ou cancele antes de fechar o caixa.")
        expected = summary(conn, session_id)["expected_cash"]
        conn.execute("UPDATE cash_sessions SET closed_at=?, closed_by=?, expected_cents=?, "
                     "counted_cents=?, note=? WHERE id=? AND closed_at IS NULL",
                     (now(), user_id, expected, counted_cents, (note or "").strip()[:200], session_id))
    return counted_cents - expected


def difference_label(diff_cents) -> str:
    if diff_cents == 0:
        return "Caixa conferido: sem diferença"
    if diff_cents < 0:
        return f"Falta {format_brl(-diff_cents)}"
    return f"Sobra {format_brl(diff_cents)}"


def history(conn, limit=30):
    return conn.execute("SELECT * FROM cash_sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
