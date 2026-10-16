from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import clock
from ..db import get_db, get_setting
from ..money import parse_money, split_evenly
from ..security import audit, idempotent, is_manager
from .. import services as sv

bp = Blueprint("main", __name__)


def _back(order_id, anchor=""):
    return redirect(url_for("main.comanda", order_id=order_id) + anchor)


def _guard(fn, *args, **kw):
    """Executa uma regra de negócio; BusinessError vira aviso em tela."""
    try:
        return fn(*args, **kw), None
    except sv.BusinessError as e:
        flash(str(e), "erro")
        return None, e


@bp.route("/")
def index():
    conn = get_db()
    sub = ("(SELECT COALESCE(SUM(quantity),0) FROM order_items WHERE order_id=o.id) AS item_count, "
           "(SELECT COALESCE(SUM(unit_price_cents*quantity),0) FROM order_items WHERE order_id=o.id) AS subtotal")
    tables = conn.execute(
        f"SELECT t.*, o.id AS order_id, o.created_at AS opened_at, o.bill_requested_at, {sub} FROM tables t "
        "LEFT JOIN orders o ON o.table_id=t.id AND o.status='aberta' WHERE t.active=1 ORDER BY t.id").fetchall()
    others = conn.execute(
        f"SELECT o.*, NULL AS table_name, {sub} FROM orders o "
        "WHERE o.table_id IS NULL AND o.status='aberta' ORDER BY o.id").fetchall()
    today = clock.today().isoformat()
    row = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) v FROM orders "
                       "WHERE status='fechada' AND substr(closed_at,1,10)=?", (today,)).fetchone()
    return render_template(
        "index.html", tables=tables, others=others,
        open_count=sum(1 for t in tables if t["order_id"]) + len(others),
        sales_today=row["v"], orders_today=row["n"], low_stock=sv.low_stock_products(conn), ready=sv.ready_orders(conn))


@bp.post("/mesa/<int:table_id>/abrir")
def abrir_mesa(table_id):
    conn = get_db()
    result, err = _guard(sv.open_order, conn, "mesa", table_id, user_id=g.user["id"])
    if err:
        return redirect(url_for("main.index"))
    order_id, created = result
    if created:
        audit(conn, "abrir_comanda", f"#{order_id} mesa {table_id}")
    return _back(order_id)


@bp.get("/mesa/<int:table_id>")
def mesa(table_id):
    order = sv.open_order_for_table(get_db(), table_id)
    return _back(order["id"]) if order else redirect(url_for("main.index"))


@bp.post("/comanda/nova")
@idempotent
def nova_comanda():
    conn = get_db()
    kind = request.form.get("kind", "balcao")
    try:
        fee = parse_money(request.form.get("delivery_fee") or "0")
    except ValueError:
        flash("Taxa de entrega inválida.", "erro")
        return redirect(url_for("main.index"))
    result, err = _guard(sv.open_order, conn, kind, None, request.form.get("customer_name", ""),
                         request.form.get("customer_phone", ""), request.form.get("address", ""),
                         fee, g.user["id"])
    if err:
        return redirect(url_for("main.index"))
    audit(conn, "abrir_comanda", f"#{result[0]} {kind}")
    return _back(result[0])


@bp.get("/comanda/<int:order_id>")
def comanda(order_id):
    conn = get_db()
    order = sv.get_order(conn, order_id)
    if not order:
        flash("Comanda não encontrada.", "erro")
        return redirect(url_for("main.index"))
    items, totals = sv.get_items(conn, order_id), sv.order_totals(conn, order)
    products = conn.execute("SELECT * FROM products WHERE available=1 ORDER BY category, name").fetchall()
    payments = conn.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchall()
    free_tables = conn.execute(
        "SELECT t.* FROM tables t WHERE t.active=1 AND NOT EXISTS "
        "(SELECT 1 FROM orders o WHERE o.table_id=t.id AND o.status='aberta') ORDER BY t.id").fetchall()
    mergeable = conn.execute(
        "SELECT o.id, t.name AS table_name FROM orders o JOIN tables t ON t.id=o.table_id "
        "WHERE o.status='aberta' AND o.kind='mesa' AND o.id<>? ORDER BY t.id", (order_id,)).fetchall()
    wa_text = sv.whatsapp_message(conn, order, items, totals)
    merged_from = conn.execute("SELECT o.id, t.name AS table_name FROM orders o LEFT JOIN tables t "
                               "ON t.id=o.table_id WHERE o.merged_into=?", (order_id,)).fetchall()
    return render_template(
        "comanda.html", order=order, items=items, totals=totals, products=products,
        categories=sorted({p["category"] for p in products}), payments=payments,
        free_tables=free_tables, mergeable=mergeable, merged_from=merged_from,
        KITCHEN=sv.KITCHEN_LABELS, new_items=sum(1 for i in items if i["kitchen_status"] == "novo"),
        ready_items=sum(1 for i in items if i["kitchen_status"] == "pronto"),
        wa_text=wa_text, wa_phone=sv.phone_for_whatsapp(order["customer_phone"]),
        service_pct=int(get_setting(conn, "service_pct", "10") or 0))


@bp.post("/comanda/<int:order_id>/adicionar")
@idempotent
def adicionar_item(order_id):
    qty = request.form.get("quantity", type=int, default=1)
    _guard(sv.add_item, get_db(), order_id, request.form.get("product_id", type=int), qty,
           request.form.get("note", ""), g.user["id"])
    return _back(order_id, "#cardapio")


@bp.post("/item/<int:item_id>/remover")
def remover_item(item_id):
    order_id, err = _guard(sv.remove_item, get_db(), item_id, g.user["id"])
    return _back(order_id) if order_id else redirect(request.referrer or url_for("main.index"))


@bp.post("/item/<int:item_id>/qtd")
def alterar_qtd(item_id):
    delta = 1 if request.form.get("delta") == "1" else -1
    order_id, err = _guard(sv.change_qty, get_db(), item_id, delta, g.user["id"])
    return _back(order_id) if order_id else redirect(request.referrer or url_for("main.index"))


@bp.post("/comanda/<int:order_id>/servico")
def servico(order_id):
    _guard(sv.set_service, get_db(), order_id, request.form.get("enabled") == "1")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/cliente")
def cliente(order_id):
    try:
        fee = parse_money(request.form.get("delivery_fee") or "0")
    except ValueError:
        flash("Taxa de entrega inválida.", "erro")
        return _back(order_id)
    _guard(sv.update_customer, get_db(), order_id, request.form.get("customer_name", ""),
           request.form.get("customer_phone", ""), request.form.get("address", ""), fee)
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/transferir")
@idempotent
def transferir(order_id):
    conn = get_db()
    dest = request.form.get("table_id", type=int)
    _, err = _guard(sv.transfer_order, conn, order_id, dest)
    if not err:
        audit(conn, "transferir_mesa", f"#{order_id} -> mesa {dest}")
        flash("Comanda transferida.", "sucesso")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/juntar")
@idempotent
def juntar(order_id):
    conn = get_db()
    src = request.form.get("source_id", type=int)
    source, err = _guard(sv.merge_orders, conn, order_id, src)
    if not err:
        audit(conn, "juntar_mesas", f"#{src} -> #{order_id}")
        flash(f"Itens de {source['table_name']} juntados nesta comanda.", "sucesso")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/fechar")
@idempotent
def fechar(order_id):
    conn = get_db()
    methods, amounts = request.form.getlist("pay_method"), request.form.getlist("pay_amount")
    pays = []
    try:
        for m, a in zip(methods, amounts):
            if a.strip():
                pays.append((m, parse_money(a)))
    except ValueError:
        flash("Há um valor de pagamento inválido.", "erro")
        return _back(order_id)
    change, err = _guard(sv.close_order, conn, order_id, pays, g.user["id"])
    if err:
        return _back(order_id)
    audit(conn, "fechar_comanda", f"#{order_id} " + ", ".join(f"{m}={c}" for m, c in pays))
    from ..money import format_brl
    flash("Conta fechada." + (f" Troco: {format_brl(change)}." if change else ""), "sucesso")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/cancelar")
@idempotent
def cancelar(order_id):
    conn = get_db()
    order = sv.get_order(conn, order_id)
    if not order:
        return redirect(url_for("main.index"))
    has_items = sv.order_totals(conn, order)["count"] > 0
    if (order["status"] == "fechada" or has_items) and not is_manager():
        flash("Somente o gerente pode cancelar uma comanda com itens ou já fechada.", "erro")
        return _back(order_id)
    prev, err = _guard(sv.cancel_order, conn, order_id, request.form.get("reason", ""), g.user["id"],
                       request.form.get("restore_stock", "1") == "1")
    if err:
        return _back(order_id)
    audit(conn, "estorno" if prev == "fechada" else "cancelar_comanda",
          f"#{order_id}: {request.form.get('reason', '')[:100]}")
    flash("Comanda estornada." if prev == "fechada" else "Comanda cancelada.", "sucesso")
    return redirect(url_for("main.index") if prev == "aberta" else url_for("reports.historico"))


@bp.get("/comanda/<int:order_id>/cupom")
def cupom(order_id):
    conn = get_db()
    order = sv.get_order(conn, order_id)
    if not order:
        return redirect(url_for("main.index"))
    return render_template(
        "cupom.html", order=order, items=sv.get_items(conn, order_id), totals=sv.order_totals(conn, order),
        payments=conn.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchall(),
        phone=get_setting(conn, "business_phone"), footer=get_setting(conn, "footer_message"))


# ------------------------------------------------------------------ cozinha
@bp.post("/comanda/<int:order_id>/cozinha")
@idempotent
def enviar_cozinha(order_id):
    conn = get_db()
    n, err = _guard(sv.send_to_kitchen, conn, order_id)
    if not err:
        audit(conn, "enviar_cozinha", f"#{order_id}: {n} item(ns)")
        flash(f"{n} item(ns) enviado(s) à cozinha.", "sucesso")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/entregue")
def entregue(order_id):
    _guard(sv.kitchen_step, get_db(), order_id, "entregue")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/conta")
def pedir_conta(order_id):
    _guard(sv.set_bill_requested, get_db(), order_id, request.form.get("on") == "1")
    return _back(order_id)


@bp.post("/comanda/<int:order_id>/reabrir")
@idempotent
def reabrir(order_id):
    if not is_manager():
        flash("Somente o gerente pode reabrir uma comanda.", "erro")
        return _back(order_id)
    conn = get_db()
    _, err = _guard(sv.reopen_order, conn, order_id)
    if not err:
        audit(conn, "reabrir_comanda", f"#{order_id}")
        flash("Comanda reaberta. Os pagamentos anteriores foram removidos.", "sucesso")
    return _back(order_id)


@bp.get("/cozinha")
def cozinha():
    return render_template("cozinha.html", tickets=sv.kitchen_tickets(get_db()))


@bp.post("/cozinha/<int:order_id>/<step>")
def cozinha_acao(order_id, step):
    _guard(sv.kitchen_step, get_db(), order_id, step)
    return redirect(url_for("main.cozinha"))
