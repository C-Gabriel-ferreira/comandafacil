"""Pedidos online (cardápio digital), lado do PDV. Sem Qt.

O cardápio público e a caixa de entrada ficam na nuvem (ver cloud/001_pedidos_online.sql). O PDV só faz
chamadas de SAÍDA (HTTPS), então não precisa de IP público nem de abrir portas no roteador:
  publicar cardápio -> nuvem; buscar pedidos novos <- nuvem; enviar status (aceito, pronto...) -> nuvem.

Os pedidos recebidos ficam na tabela local `online_orders`. Ao aceitar, vira uma comanda normal
(delivery ou balcão) que segue o fluxo de cozinha e pagamento do PDV. Mudanças de status são gravadas
localmente primeiro e enviadas depois (se a internet cair, são reenviadas no próximo ciclo).
"""
import base64
import json
import urllib.error
import urllib.request

from . import services as sv
from .db import get_setting, now, set_setting, transaction
from .services import BusinessError

CFG_KEYS = ("online_url", "online_key", "online_slug", "online_token")
STATUS_LABELS = {"novo": "Novo", "aceito": "Aceito", "preparo": "Em preparo", "pronto": "Pronto",
                 "saiu": "Saiu p/ entrega", "entregue": "Entregue", "recusado": "Recusado", "cancelado": "Cancelado"}
ACTIVE = ("novo", "aceito", "preparo", "pronto", "saiu")
CODE_PREFIX = "CF1."


class CloudError(Exception):
    """Falha de comunicação com a nuvem (sem internet, chave errada, etc.)."""


# ------------------------------------------------------------------------------ configuração
def make_activation_code(url, key, slug, token) -> str:
    raw = json.dumps({"u": url, "k": key, "s": slug, "t": token}, separators=(",", ":")).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_activation_code(code: str) -> dict:
    code = (code or "").strip()
    if not code.startswith(CODE_PREFIX):
        raise BusinessError("Código de ativação inválido.")
    body = code[len(CODE_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        out = {"online_url": data["u"], "online_key": data["k"], "online_slug": data["s"],
               "online_token": data["t"]}
    except (ValueError, KeyError, TypeError):
        raise BusinessError("Código de ativação inválido.") from None
    if not out["online_url"].startswith("https://") or not all(isinstance(v, str) and v for v in out.values()):
        raise BusinessError("Código de ativação inválido.")
    return out


def activate(conn, code):
    for k, v in parse_activation_code(code).items():
        set_setting(conn, k, v)
    set_setting(conn, "online_menu_hash", "")


def deactivate(conn):
    for k in CFG_KEYS:
        set_setting(conn, k, "")


def is_configured(conn) -> bool:
    return all(get_setting(conn, k, "") for k in CFG_KEYS)


def public_link(conn, site_url="") -> str:
    slug = get_setting(conn, "online_slug", "")
    base = (site_url or get_setting(conn, "online_site", "")).rstrip("/")
    return f"{base}/cardapio/?loja={slug}" if slug and base else ""


# ----------------------------------------------------------------------------------- cliente
class CloudClient:
    """Chama as funções RPC do Supabase (PostgREST). `transport` é injetável para testes."""

    def __init__(self, url, key, slug, token, transport=None, timeout=12):
        self.base = url.rstrip("/") + "/rest/v1/rpc/"
        self.key, self.slug, self.token, self.timeout = key, slug, token, timeout
        self.transport = transport or self._http

    @classmethod
    def from_conn(cls, conn, transport=None):
        if not is_configured(conn):
            raise BusinessError("Pedidos online ainda não foram ativados. Cole o código de ativação.")
        return cls(*(get_setting(conn, k) for k in CFG_KEYS), transport=transport)

    def _headers(self):
        h = {"apikey": self.key, "Content-Type": "application/json"}
        if self.key.startswith("eyJ"):       # chave legada (JWT); as novas "sb_publishable_" não vão em Authorization
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def _http(self, fn, payload):
        req = urllib.request.Request(
            self.base + fn, data=json.dumps(payload).encode(), method="POST",
            headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or "null")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read()).get("message", "")
            except (ValueError, AttributeError):
                pass
            if "acesso negado" in detail:
                raise CloudError("Acesso negado pela nuvem: o código de ativação não vale mais.") from None
            raise CloudError(f"A nuvem recusou o pedido (erro {e.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CloudError("Sem conexão com a internet.") from None

    def call(self, fn, **params):
        res = self.transport(fn, params)
        if isinstance(res, dict) and res.get("ok") is False:
            raise CloudError(res.get("error", "erro"))
        return res

    def _auth(self):
        return {"p_slug": self.slug, "p_token": self.token}

    def publish_menu(self, items, settings):
        return self.call("cf_pdv_publish_menu", **self._auth(), p_items=items, p_settings=settings)

    def set_accepting(self, flag):
        return self.call("cf_pdv_set_accepting", **self._auth(), p_flag=bool(flag))

    def pull_orders(self):
        return self.call("cf_pdv_pull_orders", **self._auth())

    def set_status(self, code, status, reason=""):
        return self.call("cf_pdv_set_status", **self._auth(), p_code=code, p_status=status, p_reason=reason)


# ---------------------------------------------------------------------------------- cardápio
def menu_items(conn):
    rows = conn.execute("SELECT id,name,category,price_cents,available,track_stock,stock FROM products "
                        "ORDER BY category, name").fetchall()
    return [{"id": r["id"], "name": r["name"], "category": r["category"], "price_cents": r["price_cents"],
             "available": bool(r["available"]) and not (r["track_stock"] and r["stock"] <= 0), "sort": i}
            for i, r in enumerate(rows)]


def store_settings(conn) -> dict:
    g = lambda k, d="": get_setting(conn, k, d)  # noqa: E731
    return {"name": g("business_name", "ComandaFácil"), "whatsapp": sv.phone_for_whatsapp(g("business_phone")),
            "delivery_enabled": g("online_delivery", "1") == "1", "pickup_enabled": g("online_pickup", "1") == "1",
            "delivery_fee_cents": int(g("online_fee_cents", "0") or 0),
            "min_order_cents": int(g("online_min_cents", "0") or 0),
            "hours_text": g("online_hours", ""), "notice": g("online_notice", "")}


def publish(conn, client):
    """Envia o cardápio atual (produtos disponíveis e com estoque) e as regras da loja."""
    items = menu_items(conn)
    res = client.publish_menu(items, store_settings(conn))
    return res.get("items", len(items))


# ------------------------------------------------------------------------------- recebimento
def _store_new(conn, o):
    if conn.execute("SELECT 1 FROM online_orders WHERE code=?", (o["code"],)).fetchone():
        return False
    conn.execute(
        "INSERT INTO online_orders(code,seq,kind,customer_name,customer_phone,address,note,payment_method,"
        "change_for_cents,subtotal_cents,delivery_fee_cents,total_cents,items_json,received_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (o["code"], o["seq"], o["kind"], o["name"], o["phone"], o.get("address") or "", o.get("note") or "",
         o["payment"], o.get("change_for_cents"), o["subtotal_cents"], o.get("delivery_fee_cents") or 0,
         o["total_cents"], json.dumps(o["items"], ensure_ascii=False), now()))
    return True


def sync(conn, client):
    """Um ciclo: envia status pendentes, busca pedidos novos e acompanha a cozinha.
    Devolve {'new': [códigos], 'accepting': bool}. Levanta CloudError se não houver conexão."""
    _derive_progress(conn)
    push_pending(conn, client)
    res = client.pull_orders()
    new = []
    with transaction(conn):
        for o in res.get("orders", []):
            if _store_new(conn, o):
                new.append(o["code"])
    return {"new": new, "accepting": bool(res.get("accepting"))}


def push_pending(conn, client):
    rows = conn.execute("SELECT code,status,reject_reason FROM online_orders WHERE status<>pushed_status").fetchall()
    for r in rows:
        try:
            client.set_status(r["code"], r["status"], r["reject_reason"])
        except CloudError as e:
            if str(e) in ("transicao_invalida", "pedido_nao_encontrado"):
                pass  # a nuvem já está adiante (ex.: cliente/outro PDV); não ficar repetindo
            else:
                raise
        conn.execute("UPDATE online_orders SET pushed_status=? WHERE code=?", (r["status"], r["code"]))


def _derive_progress(conn):
    """Aceito/preparo -> pronto quando toda a cozinha terminou; -> entregue quando a comanda é paga."""
    for r in conn.execute("SELECT id,status,kind,order_id FROM online_orders "
                          "WHERE order_id IS NOT NULL AND status IN ('aceito','preparo','pronto','saiu')").fetchall():
        o = sv.get_order(conn, r["order_id"])
        new = None
        if o["status"] == "fechada":
            new = "entregue"
        elif o["status"] in ("cancelada", "juntada"):
            new = "cancelado"
        elif r["status"] in ("aceito", "preparo"):
            ks = {i["kitchen_status"] for i in sv.get_items(conn, r["order_id"])}
            if ks and ks <= {"pronto", "servido"}:
                new = "pronto"
            elif ks & {"preparando", "pronto"}:
                new = "preparo" if r["status"] == "aceito" else None
        if new and new != r["status"]:
            conn.execute("UPDATE online_orders SET status=? WHERE id=?", (new, r["id"]))


# --------------------------------------------------------------------------------- consultas
def list_orders(conn, active_only=True):
    where = f"WHERE status IN ({','.join('?' * len(ACTIVE))})" if active_only else ""
    args = ACTIVE if active_only else ()
    rows = conn.execute(f"SELECT * FROM online_orders {where} ORDER BY received_at DESC, id DESC LIMIT 200",
                        args).fetchall()
    return [_decorate(r) for r in rows]


def get(conn, code):
    r = conn.execute("SELECT * FROM online_orders WHERE code=?", (code,)).fetchone()
    if not r:
        raise BusinessError("Pedido online não encontrado.")
    return _decorate(r)


def _decorate(r):
    d = dict(r)
    d["items"] = json.loads(d.pop("items_json"))
    d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
    return d


def unseen_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM online_orders WHERE status='novo'").fetchone()[0]


# ------------------------------------------------------------------------------------ decisões
def accept(conn, code, user_id=None):
    """Aceita: cria a comanda local com os itens (preço que o cliente viu) e manda para a cozinha."""
    o = conn.execute("SELECT * FROM online_orders WHERE code=?", (code,)).fetchone()
    if not o:
        raise BusinessError("Pedido online não encontrado.")
    if o["status"] != "novo":
        raise BusinessError("Este pedido já foi tratado.")
    items = json.loads(o["items_json"])
    _check_available(conn, items)
    delivery = o["kind"] == "entrega"
    label = f"{o['customer_name']} (online #{o['seq']})"
    oid, _ = sv.open_order(conn, "delivery" if delivery else "balcao", None, label, o["customer_phone"],
                           o["address"] if delivery else "", o["delivery_fee_cents"] if delivery else 0, user_id)
    try:
        for it in items:
            sv.add_item(conn, oid, it["id"], it["qty"], it.get("note") or "", user_id)
            # vale o preço que o cliente viu no cardápio
            conn.execute("UPDATE order_items SET unit_price_cents=? WHERE order_id=? AND product_id=? "
                         "AND note=? AND unit_price_cents<>?",
                         (it["price_cents"], oid, it["id"], it.get("note") or "", it["price_cents"]))
        if o["note"]:
            first = conn.execute("SELECT id,note FROM order_items WHERE order_id=? ORDER BY id LIMIT 1", (oid,)).fetchone()
            extra = f"[PEDIDO] {o['note']}"
            conn.execute("UPDATE order_items SET note=? WHERE id=?",
                         (f"{first['note']} | {extra}"[:140] if first["note"] else extra[:140], first["id"]))
        sv.send_to_kitchen(conn, oid)
    except BusinessError:
        sv.cancel_order(conn, oid, "pedido online não aceito", user_id)
        raise
    conn.execute("UPDATE online_orders SET status='aceito', order_id=?, decided_at=?, seen=1 WHERE code=?",
                 (oid, now(), code))
    return oid


def _check_available(conn, items):
    problems = []
    for it in items:
        p = conn.execute("SELECT name,available,track_stock,stock FROM products WHERE id=?", (it["id"],)).fetchone()
        if not p or not p["available"]:
            problems.append(f"{it['name']} está indisponível")
        elif p["track_stock"] and p["stock"] < it["qty"]:
            problems.append(f"{it['name']}: só restam {p['stock']}")
    if problems:
        raise BusinessError("Não dá para aceitar — " + "; ".join(problems) + ". Recuse ou ajuste o estoque.")


def reject(conn, code, reason=""):
    reason = (reason or "").strip()[:200]
    cur = conn.execute("UPDATE online_orders SET status='recusado', reject_reason=?, decided_at=?, seen=1 "
                       "WHERE code=? AND status='novo'", (reason, now(), code))
    if cur.rowcount < 1:
        raise BusinessError("Este pedido já foi tratado.")


def mark_dispatched(conn, code):
    """Entrega saiu para o cliente (ou retirada pronta, quando não há cozinha envolvida)."""
    cur = conn.execute("UPDATE online_orders SET status='saiu' WHERE code=? AND status IN ('aceito','preparo','pronto') "
                       "AND kind='entrega'", (code,))
    if cur.rowcount < 1:
        raise BusinessError("Só dá para marcar 'saiu' em entregas já aceitas.")


def mark_ready(conn, code):
    cur = conn.execute("UPDATE online_orders SET status='pronto' WHERE code=? AND status IN ('aceito','preparo')",
                       (code,))
    if cur.rowcount < 1:
        raise BusinessError("O pedido não está em preparo.")


def wa_message(conn, code) -> tuple[str, str]:
    """(telefone com DDI, texto) para avisar o cliente pelo WhatsApp com um clique (wa.me)."""
    o = get(conn, code)
    biz = get_setting(conn, "business_name", "ComandaFácil")
    texts = {
        "aceito": f"Olá, {o['customer_name']}! Recebemos seu pedido #{o['seq']} no {biz} e já estamos preparando. 🙌",
        "pronto": (f"Olá, {o['customer_name']}! Seu pedido #{o['seq']} está pronto para retirada. 😋"
                   if o["kind"] == "retirada" else f"Olá, {o['customer_name']}! Seu pedido #{o['seq']} está pronto "
                   "e já vai sair para entrega."),
        "saiu": f"Olá, {o['customer_name']}! Seu pedido #{o['seq']} saiu para entrega. Bom apetite! 🛵",
        "recusado": f"Olá, {o['customer_name']}. Infelizmente não conseguimos atender o pedido #{o['seq']}"
                    + (f" ({o['reject_reason']})." if o["reject_reason"] else "."),
    }
    return sv.phone_for_whatsapp(o["customer_phone"]), texts.get(
        o["status"], f"Olá, {o['customer_name']}! Sobre o seu pedido #{o['seq']} no {biz}.")
