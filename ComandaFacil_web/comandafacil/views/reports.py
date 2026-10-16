import csv
import io
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, render_template, request

from .. import clock
from ..db import get_db
from ..security import manager_required
from .. import services as sv

bp = Blueprint("reports", __name__)


def _parse_day(s, default):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return default


def parse_period():
    today = clock.today()
    p = request.args.get("periodo", "hoje")
    if p == "7d":
        start, end = today - timedelta(days=6), today
    elif p == "30d":
        start, end = today - timedelta(days=29), today
    elif p == "mes":
        start, end = today.replace(day=1), today
    elif p == "custom":
        start = _parse_day(request.args.get("inicio"), today)
        end = _parse_day(request.args.get("fim"), today)
        if end < start:
            start, end = end, start
        if (end - start).days > 366:
            start = end - timedelta(days=366)
    else:
        p, start, end = "hoje", today, today
    return p, start, end


@bp.route("/relatorios")
@manager_required
def relatorios():
    p, start, end = parse_period()
    return render_template("relatorios.html", periodo=p, start=start, end=end,
                           r=sv.sales_report(get_db(), start, end))


@bp.route("/relatorios/vendas.csv")
@manager_required
def exportar_csv():
    _, start, end = parse_period()
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Comanda", "Fechamento", "Tipo", "Mesa/Cliente", "Subtotal", "Serviço", "Entrega", "Total",
                "Pagamentos"])
    fmt = lambda c: f"{(c or 0) / 100:.2f}".replace(".", ",")
    for o in sv.sales_rows(get_db(), start, end):
        pays = "; ".join(f"{p.rsplit(' ', 1)[0]} {fmt(int(p.rsplit(' ', 1)[1]))}"
                         for p in (o["pays"] or "").split("; ") if p)
        w.writerow([o["id"], o["closed_at"].replace("T", " "), sv.KINDS[o["kind"]],
                    o["table_name"] or o["customer_name"] or "", fmt(o["subtotal_cents"]),
                    fmt(o["service_cents"]), fmt(o["delivery_fee_cents"]), fmt(o["total_cents"]), pays])
    name = f"vendas_{start}_{end}.csv"
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


@bp.route("/historico")
def historico():
    conn = get_db()
    today = clock.today()
    start = _parse_day(request.args.get("inicio"), today - timedelta(days=6))
    end = _parse_day(request.args.get("fim"), today)
    status = request.args.get("status", "fechada")
    kind = request.args.get("tipo", "")
    sql = ("SELECT o.*, t.name AS table_name, "
           "(SELECT GROUP_CONCAT(method, ' + ') FROM payments WHERE order_id=o.id) AS pays "
           "FROM orders o LEFT JOIN tables t ON t.id=o.table_id "
           "WHERE o.status=? AND substr(COALESCE(o.closed_at, o.cancelled_at, o.created_at),1,10) BETWEEN ? AND ?")
    args = [status if status in ("fechada", "cancelada", "juntada") else "fechada", start.isoformat(), end.isoformat()]
    if kind in sv.KINDS:
        sql += " AND o.kind=?"
        args.append(kind)
    rows = conn.execute(sql + " ORDER BY o.id DESC LIMIT 300", args).fetchall()
    return render_template("historico.html", orders=rows, start=start, end=end, status=args[0], kind=kind)
