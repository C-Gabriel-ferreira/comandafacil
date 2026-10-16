"""Produtos, estoque e mesas (somente gerente)."""
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import db as dbm
from ..db import get_db
from ..money import parse_money
from ..security import audit, idempotent, manager_required
from .. import services as sv

bp = Blueprint("catalog", __name__)


@bp.before_request
@manager_required
def _only_manager():
    pass


def _int(name, default=0, lo=0, hi=1_000_000):
    try:
        return max(lo, min(int(request.form.get(name, default) or default), hi))
    except ValueError:
        return default


def _confirm(message, action, button, back):
    return render_template("confirmar.html", message=message, action=action, button=button, back=back)


# ----------------------------------------------------------------- produtos
@bp.get("/produtos/compras")
def compras():
    return render_template("compras.html", rows=sv.shopping_list(get_db()))


@bp.route("/produtos")
def produtos():
    conn = get_db()
    products = conn.execute("SELECT * FROM products ORDER BY category, name").fetchall()
    cats = [r[0] for r in conn.execute("SELECT DISTINCT category FROM products ORDER BY 1")]
    return render_template("produtos.html", products=products, categories=cats)


def _product_form():
    name = request.form.get("name", "").strip()[:80]
    category = request.form.get("category", "").strip()[:40] or "Outros"
    try:
        price = parse_money(request.form.get("price", ""))
    except ValueError:
        price = None
    if not name or price is None:
        raise sv.BusinessError("Informe um nome e um preço válido.")
    track = 1 if request.form.get("track_stock") else 0
    return name, category, price, track, _int("stock"), _int("stock_min")


@bp.post("/produtos/adicionar")
@idempotent
def adicionar_produto():
    conn = get_db()
    try:
        name, cat, price, track, stock, smin = _product_form()
    except sv.BusinessError as e:
        flash(str(e), "erro")
        return redirect(url_for("catalog.produtos"))
    cur = conn.execute("INSERT INTO products(name,category,price_cents,track_stock,stock,stock_min) "
                       "VALUES (?,?,?,?,?,?)", (name, cat, price, track, stock if track else 0, smin))
    audit(conn, "produto_criado", f"{name} ({price} centavos)")
    flash("Produto cadastrado.", "sucesso")
    return redirect(url_for("catalog.produtos"))


@bp.route("/produtos/<int:pid>/editar", methods=["GET", "POST"])
def editar_produto(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        return redirect(url_for("catalog.produtos"))
    if request.method == "POST":
        try:
            name, cat, price, track, _stock, smin = _product_form()
        except sv.BusinessError as e:
            flash(str(e), "erro")
            return redirect(url_for("catalog.editar_produto", pid=pid))
        conn.execute("UPDATE products SET name=?,category=?,price_cents=?,track_stock=?,stock_min=? WHERE id=?",
                     (name, cat, price, track, smin, pid))
        audit(conn, "produto_editado", f"#{pid} {name} preço {p['price_cents']}->{price}")
        flash("Produto atualizado. Comandas já abertas mantêm o preço antigo dos itens.", "sucesso")
        return redirect(url_for("catalog.produtos"))
    return render_template("produto_editar.html", p=p)


@bp.post("/produtos/<int:pid>/alternar")
def alternar_produto(pid):
    get_db().execute("UPDATE products SET available = 1 - available WHERE id=?", (pid,))
    flash("Disponibilidade atualizada.", "sucesso")
    return redirect(url_for("catalog.produtos"))


@bp.post("/produtos/<int:pid>/estoque")
@idempotent
def estoque(pid):
    conn = get_db()
    try:
        delta = int(request.form.get("delta", "0"))
        if delta == 0 or abs(delta) > 100000:
            raise ValueError
    except ValueError:
        flash("Informe uma quantidade diferente de zero (use negativo para baixa).", "erro")
        return redirect(url_for("catalog.produtos"))
    try:
        sv.adjust_stock(conn, pid, delta, request.form.get("reason", "").strip()[:80] or
                        ("entrada" if delta > 0 else "baixa manual"), g.user["id"])
        audit(conn, "estoque_ajustado", f"produto #{pid}: {delta:+d}")
        flash("Estoque atualizado.", "sucesso")
    except sv.BusinessError as e:
        flash(str(e), "erro")
    return redirect(url_for("catalog.produtos"))


@bp.route("/produtos/<int:pid>/excluir", methods=["GET", "POST"])
def excluir_produto(pid):
    conn = get_db()
    p = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not p:
        return redirect(url_for("catalog.produtos"))
    if request.method == "POST":
        conn.execute("DELETE FROM products WHERE id=?", (pid,))
        audit(conn, "produto_excluido", f"#{pid} {p['name']}")
        flash("Produto excluído. Itens antigos nas comandas foram preservados.", "sucesso")
        return redirect(url_for("catalog.produtos"))
    return _confirm(f"Excluir o produto “{p['name']}” do catálogo? Os itens já lançados em comandas "
                    "continuam no histórico. Se só quiser parar de vender, prefira “Desativar”.",
                    url_for("catalog.excluir_produto", pid=pid), "Excluir produto", url_for("catalog.produtos"))


# -------------------------------------------------------------------- mesas
@bp.route("/mesas")
def mesas():
    rows = get_db().execute(
        "SELECT t.*, (SELECT id FROM orders o WHERE o.table_id=t.id AND o.status='aberta') AS open_order, "
        "(SELECT COUNT(*) FROM orders o WHERE o.table_id=t.id) AS history FROM tables t ORDER BY t.id").fetchall()
    return render_template("mesas.html", tables=rows)


@bp.post("/mesas/adicionar")
@idempotent
def adicionar_mesa():
    conn = get_db()
    name = request.form.get("name", "").strip()[:30]
    qty = _int("quantity", 1, 1, 100)
    try:
        if name:
            conn.execute("INSERT INTO tables(name) VALUES (?)", (name,))
            qty = 1
        else:  # sem nome: cria "Mesa N+1..."
            nums = [int(m.group(1)) for (n,) in conn.execute("SELECT name FROM tables")
                    if (m := re.fullmatch(r"Mesa (\d+)", n))]
            start = max(nums, default=0) + 1
            for i in range(start, start + qty):
                conn.execute("INSERT INTO tables(name) VALUES (?)", (f"Mesa {i}",))
        audit(conn, "mesas_criadas", f"{qty} mesa(s) {name}")
        flash(f"{qty} mesa(s) adicionada(s).", "sucesso")
    except Exception as e:
        if isinstance(e, dbm.INTEGRITY_ERRORS):
            flash("Já existe uma mesa com esse nome.", "erro")
        else:
            raise
    return redirect(url_for("catalog.mesas"))


@bp.post("/mesas/<int:tid>/renomear")
def renomear_mesa(tid):
    name = request.form.get("name", "").strip()[:30]
    if not name:
        flash("Informe o nome da mesa.", "erro")
    else:
        try:
            get_db().execute("UPDATE tables SET name=? WHERE id=?", (name, tid))
            audit(get_db(), "mesa_renomeada", f"#{tid} -> {name}")
            flash("Mesa renomeada.", "sucesso")
        except Exception as e:
            if not isinstance(e, dbm.INTEGRITY_ERRORS):
                raise
            flash("Já existe uma mesa com esse nome.", "erro")
    return redirect(url_for("catalog.mesas"))


@bp.post("/mesas/<int:tid>/alternar")
def alternar_mesa(tid):
    conn = get_db()
    if sv.open_order_for_table(conn, tid):
        flash("Esta mesa tem comanda aberta. Feche ou cancele antes de desativar.", "erro")
    else:
        conn.execute("UPDATE tables SET active = 1 - active WHERE id=?", (tid,))
        flash("Mesa atualizada.", "sucesso")
    return redirect(url_for("catalog.mesas"))


@bp.route("/mesas/<int:tid>/excluir", methods=["GET", "POST"])
def excluir_mesa(tid):
    conn = get_db()
    t = conn.execute("SELECT * FROM tables WHERE id=?", (tid,)).fetchone()
    if not t:
        return redirect(url_for("catalog.mesas"))
    if conn.execute("SELECT 1 FROM orders WHERE table_id=?", (tid,)).fetchone():
        flash("Esta mesa tem histórico de comandas e não pode ser excluída. Desative-a.", "erro")
        return redirect(url_for("catalog.mesas"))
    if request.method == "POST":
        conn.execute("DELETE FROM tables WHERE id=?", (tid,))
        audit(conn, "mesa_excluida", t["name"])
        flash("Mesa excluída.", "sucesso")
        return redirect(url_for("catalog.mesas"))
    return _confirm(f"Excluir “{t['name']}”?", url_for("catalog.excluir_mesa", tid=tid),
                    "Excluir mesa", url_for("catalog.mesas"))
