"""Texto do cupom/conferência para bobina térmica (monoespaçado, sem Qt)."""
from datetime import datetime

from .db import get_setting
from .money import format_brl
from .services import KINDS, get_items, order_totals

WIDTH = 42  # colunas para bobina de 80 mm (use 32 para 58 mm)


def _center(s, w=WIDTH):
    return s[:w].center(w).rstrip()


def _two(left, right, w=WIDTH):
    left = left[: max(1, w - len(right) - 1)]
    return left + " " * (w - len(left) - len(right)) + right


def _wrap(text, w, indent=""):
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + (1 if line else 0) > w:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return [indent + x for x in out]


def _fmt_dt(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def build_receipt(conn, order, width=WIDTH) -> str:
    """Cupom de uma comanda. Se estiver aberta, imprime como CONFERÊNCIA (não fiscal)."""
    items = get_items(conn, order["id"])
    t = order_totals(conn, order)
    biz = get_setting(conn, "business_name", "ComandaFácil")
    phone = get_setting(conn, "business_phone", "")
    footer = get_setting(conn, "footer_message", "")
    bar = "-" * width
    L = [_center(biz.upper(), width)]
    if phone:
        L.append(_center(phone, width))
    L += [bar]
    if order["status"] == "aberta":
        L.append(_center("*** CONFERENCIA DE CONTA ***", width))
    L.append(_center("NAO E DOCUMENTO FISCAL", width))
    L.append(bar)
    kind = KINDS.get(order["kind"], order["kind"])
    head = f"{kind}: {order['table_name']}" if order["kind"] == "mesa" and order["table_name"] else kind
    L.append(_two(f"Comanda #{order['id']}", head, width))
    L.append(f"Abertura: {_fmt_dt(order['created_at'])}")
    if order["closed_at"]:
        L.append(f"Fechamento: {_fmt_dt(order['closed_at'])}")
    if order["customer_name"]:
        L.append(f"Cliente: {order['customer_name']}")
    if order["customer_phone"]:
        L.append(f"Telefone: {order['customer_phone']}")
    if order["address"]:
        L += _wrap(f"Entrega: {order['address']}", width)
    L += [bar, _two("QTD ITEM", "VALOR", width), bar]
    for it in items:
        total = it["unit_price_cents"] * it["quantity"]
        L.append(_two(f"{it['quantity']}x {it['product_name']}", format_brl(total), width))
        if it["quantity"] > 1:
            L.append(f"   ({format_brl(it['unit_price_cents'])} cada)")
        if it["note"]:
            L += _wrap(f"Obs: {it['note']}", width - 3, "   ")
    L.append(bar)
    L.append(_two("Subtotal", format_brl(t["subtotal"]), width))
    if t["service"]:
        L.append(_two(f"Servico ({order['service_pct']}%)", format_brl(t["service"]), width))
    if t["delivery"]:
        L.append(_two("Taxa de entrega", format_brl(t["delivery"]), width))
    L.append(_two("TOTAL", format_brl(t["total"]), width))
    if order["status"] == "fechada":
        pays = conn.execute("SELECT method, amount_cents FROM payments WHERE order_id=? ORDER BY id",
                            (order["id"],)).fetchall()
        if pays:
            L.append(bar)
            for p in pays:
                L.append(_two(p["method"], format_brl(p["amount_cents"]), width))
    L.append(bar)
    if footer:
        L += [_center(x, width) for x in _wrap(footer, width)]
    L.append("")
    return "\n".join(L)


def build_cash_report(conn, session_id, width=WIDTH) -> str:
    """Relatório de fechamento de caixa (também serve como parcial com o caixa aberto)."""
    from . import cash
    s = cash.get_session(conn, session_id)
    sm = cash.summary(conn, session_id)
    biz = get_setting(conn, "business_name", "ComandaFácil")
    bar = "-" * width
    L = [_center(biz.upper(), width), _center("FECHAMENTO DE CAIXA" if s["closed_at"] else "CAIXA — PARCIAL", width),
         bar, f"Caixa #{s['id']}", f"Abertura:   {_fmt_dt(s['opened_at'])}"]
    if s["closed_at"]:
        L.append(f"Fechamento: {_fmt_dt(s['closed_at'])}")
    L += [bar, _two("Fundo de troco", format_brl(sm["opening"]), width), bar, "VENDAS POR FORMA DE PAGAMENTO"]
    for method, v in sm["by_method"].items():
        if v:
            L.append(_two(method, format_brl(v), width))
    L.append(_two("TOTAL VENDIDO", format_brl(sm["sales_total"]), width))
    L.append(_two(f"Comandas ({sm['orders']})  ticket", format_brl(sm["avg"]), width))
    if sm["cancelled_n"]:
        L.append(_two(f"Cancelamentos ({sm['cancelled_n']})", format_brl(sm["cancelled_value"]), width))
    L += [bar, "MOVIMENTACOES"]
    for m in cash.movements(conn, session_id):
        sign = "+" if m["kind"] == "suprimento" else "-"
        L.append(_two(f"{m['kind'].capitalize()}: {m['reason']}"[: width - 12], f"{sign}{format_brl(m['amount_cents'])}", width))
    L += [_two("Suprimentos", format_brl(sm["suprimentos"]), width), _two("Sangrias", format_brl(sm["sangrias"]), width), bar,
          _two("Dinheiro esperado", format_brl(sm["expected_cash"]), width)]
    if s["closed_at"]:
        L.append(_two("Dinheiro contado", format_brl(s["counted_cents"]), width))
        L.append(_two("Diferenca", format_brl(s["counted_cents"] - s["expected_cents"]), width))
        if s["note"]:
            L += _wrap(f"Obs: {s['note']}", width)
    L += [bar, "", "Assinatura: ______________________", ""]
    return "\n".join(L)


def build_online_ticket(conn, o, width=WIDTH) -> str:
    """Ficha do pedido online para a cozinha/entregador (inclui forma de pagamento e troco)."""
    biz = get_setting(conn, "business_name", "ComandaFácil")
    bar = "-" * width
    L = [_center(biz.upper(), width), _center(f"PEDIDO ONLINE #{o['seq']}", width), bar,
         _two("Tipo", "ENTREGA" if o["kind"] == "entrega" else "RETIRADA", width),
         f"Recebido: {_fmt_dt(o['received_at'])}", f"Cliente: {o['customer_name']}",
         f"Telefone: {o['customer_phone']}"]
    if o["address"]:
        L += _wrap(f"Endereco: {o['address']}", width)
    L += [bar, _two("QTD ITEM", "VALOR", width), bar]
    for it in o["items"]:
        L.append(_two(f"{it['qty']}x {it['name']}", format_brl(it["price_cents"] * it["qty"]), width))
        if it.get("note"):
            L += _wrap(f"Obs: {it['note']}", width - 3, "   ")
    L.append(bar)
    if o["note"]:
        L += _wrap(f"Obs do pedido: {o['note']}", width) + [bar]
    L.append(_two("Subtotal", format_brl(o["subtotal_cents"]), width))
    if o["delivery_fee_cents"]:
        L.append(_two("Taxa de entrega", format_brl(o["delivery_fee_cents"]), width))
    L.append(_two("TOTAL", format_brl(o["total_cents"]), width))
    L.append(bar)
    L.append(_two("Pagamento", o["payment_method"], width))
    if o["change_for_cents"]:
        L.append(_two("Troco para", format_brl(o["change_for_cents"]), width))
        L.append(_two("Levar de troco", format_brl(o["change_for_cents"] - o["total_cents"]), width))
    L.append("")
    return "\n".join(L)
