"""Camada de aplicação do programa desktop (sem Qt): login, permissões, caixa e operações do PDV.

A interface só chama este módulo. As regras de negócio continuam em comandafacil/services.py.
"""
import time

from werkzeug.security import check_password_hash, generate_password_hash

from comandafacil import cash, db as dbm, online, receipt, services, updater
from comandafacil import clock
from comandafacil.clock import minutes_since
from comandafacil.db import transaction
from comandafacil.money import format_brl
from comandafacil.services import BusinessError

MIN_PASSWORD = 8
MAX_FAILS, LOCK_SECONDS = 5, 300


class Controller:
    def __init__(self, conn):
        self.conn = conn
        self.user = None
        self._fails: dict[str, tuple[int, float]] = {}
        self.cloud_transport = None  # injetável nos testes
        self.update_opener = None    # idem, para o atualizador

    # ------------------------------------------------------------ sessão
    @property
    def is_manager(self) -> bool:
        return bool(self.user and self.user["role"] == "gerente")

    @property
    def user_id(self):
        return self.user["id"] if self.user else None

    def has_users(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0

    def audit(self, action, details=""):
        who = self.user["username"] if self.user else None
        self.conn.execute("INSERT INTO audit_log(ts,user_name,action,details) VALUES (?,?,?,?)",
                          (dbm.now(), who, action, details))

    def create_manager(self, username, display_name, password):
        """Primeiro acesso: cria o gerente. Só funciona se ainda não houver usuários."""
        if self.has_users():
            raise BusinessError("O cadastro inicial já foi feito.")
        username, display_name = (username or "").strip().lower(), (display_name or "").strip()
        if len(username) < 3:
            raise BusinessError("O usuário precisa ter ao menos 3 caracteres.")
        if len(password or "") < MIN_PASSWORD:
            raise BusinessError(f"A senha precisa ter ao menos {MIN_PASSWORD} caracteres.")
        self.conn.execute(
            "INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES (?,?,?,?,?)",
            (username, display_name or username, generate_password_hash(password), "gerente", dbm.now()))
        self.audit("setup", f"gerente {username} criado")

    def login(self, username, password):
        key = (username or "").strip().lower()
        fails, until = self._fails.get(key, (0, 0.0))
        if fails >= MAX_FAILS and time.time() < until:
            raise BusinessError("Muitas tentativas. Aguarde alguns minutos para tentar de novo.")
        row = self.conn.execute("SELECT * FROM users WHERE username=? AND active=1", (key,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password or ""):
            fails += 1
            self._fails[key] = (fails, time.time() + LOCK_SECONDS)
            self.audit("login_falhou", key)
            raise BusinessError("Usuário ou senha incorretos.")
        self._fails.pop(key, None)
        self.user = row
        self.audit("login", key)
        return row

    def logout(self):
        if self.user:
            self.audit("logout", self.user["username"])
        self.user = None

    def require_manager(self):
        if not self.is_manager:
            raise BusinessError("Esta ação é permitida apenas ao gerente.")

    def setting(self, key, default=""):
        return dbm.get_setting(self.conn, key, default)

    # ------------------------------------------------------------- caixa
    def cash_session(self):
        return cash.current_session(self.conn)

    def require_cash(self):
        if not cash.current_session(self.conn):
            raise BusinessError("O caixa está fechado. Peça ao gerente para abrir o caixa.")

    def open_cash(self, opening_cents):
        self.require_manager()
        sid = cash.open_session(self.conn, opening_cents, self.user_id)
        self.audit("caixa_aberto", f"fundo {format_brl(opening_cents)}")
        return sid

    def cash_movement(self, kind, cents, reason):
        self.require_manager()
        cash.add_movement(self.conn, kind, cents, reason, self.user_id)
        self.audit(f"caixa_{kind}", f"{format_brl(cents)} — {reason}")

    def cash_summary(self):
        s = cash.current_session(self.conn)
        return (s, cash.summary(self.conn, s["id"])) if s else (None, None)

    def close_cash(self, counted_cents, note=""):
        self.require_manager()
        s = cash.current_session(self.conn)
        if not s:
            raise BusinessError("Não há caixa aberto.")
        diff = cash.close_session(self.conn, s["id"], counted_cents, self.user_id, note)
        self.audit("caixa_fechado", cash.difference_label(diff))
        return diff

    # --------------------------------------------------------- atendimento
    def board(self):
        """Cartões do painel: todas as mesas ativas + comandas abertas de balcão/delivery."""
        cards = []
        ready = services.ready_orders(self.conn)
        rows = self.conn.execute(
            "SELECT t.id tid, t.name, o.id oid FROM tables t "
            "LEFT JOIN orders o ON o.table_id=t.id AND o.status='aberta' "
            "WHERE t.active=1 ORDER BY t.id").fetchall()
        for r in rows:
            card = {"kind": "mesa", "table_id": r["tid"], "order_id": r["oid"], "name": r["name"],
                    "busy": r["oid"] is not None, "total": 0, "minutes": 0, "who": "", "ready": False,
                    "bill": False}
            if r["oid"]:
                o = services.get_order(self.conn, r["oid"])
                card.update(total=services.order_totals(self.conn, o)["total"],
                            minutes=minutes_since(o["created_at"]), who=o["customer_name"] or "",
                            ready=r["oid"] in ready, bill=bool(o["bill_requested_at"]))
            cards.append(card)
        for o in self.conn.execute("SELECT * FROM orders WHERE status='aberta' AND kind!='mesa' "
                                   "ORDER BY id").fetchall():
            cards.append({"kind": o["kind"], "table_id": None, "order_id": o["id"],
                          "name": f"{services.KINDS[o['kind']]} #{o['id']}", "busy": True,
                          "total": services.order_totals(self.conn, o)["total"],
                          "minutes": minutes_since(o["created_at"]), "who": o["customer_name"] or "",
                          "ready": o["id"] in ready, "bill": bool(o["bill_requested_at"])})
        return cards

    def new_counter_order(self, kind, name="", phone="", address="", fee_cents=0):
        self.require_cash()
        oid, _ = services.open_order(self.conn, kind, None, name, phone, address, fee_cents, self.user_id)
        return oid

    def order_detail(self, order_id):
        o = services.get_order(self.conn, order_id)
        if not o:
            raise BusinessError("Comanda não encontrada.")
        return o, services.get_items(self.conn, order_id), services.order_totals(self.conn, o)

    def order_for_table(self, table_id):
        return services.open_order_for_table(self.conn, table_id)

    def categories(self):
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT category FROM products WHERE available=1 ORDER BY category")]

    def products(self, text="", category=None):
        sql, args = "SELECT * FROM products WHERE available=1", []
        if category:
            sql += " AND category=?"
            args.append(category)
        if text.strip():
            sql += " AND name LIKE ?"
            args.append(f"%{text.strip()}%")
        return self.conn.execute(sql + " ORDER BY category, name", args).fetchall()

    def add_item(self, product_id, qty=1, note="", order_id=None, table_id=None):
        """Lança item. Para mesa livre, a comanda é aberta junto com o primeiro item."""
        self.require_cash()
        if order_id is None:
            if table_id is None:
                raise BusinessError("Selecione uma mesa ou abra um pedido de balcão/delivery.")
            order_id, _ = services.open_order(self.conn, "mesa", table_id, user_id=self.user_id)
        services.add_item(self.conn, order_id, product_id, qty, note, self.user_id)
        return order_id

    def change_qty(self, item_id, delta):
        services.change_qty(self.conn, item_id, delta, self.user_id)

    def remove_item(self, item_id):
        services.remove_item(self.conn, item_id, self.user_id)

    def set_service(self, order_id, enabled):
        services.set_service(self.conn, order_id, enabled)

    def update_customer(self, order_id, name, phone, address, fee_cents):
        services.update_customer(self.conn, order_id, name, phone, address, fee_cents)

    def pay(self, order_id, payments):
        self.require_cash()
        change = services.close_order(self.conn, order_id, payments, self.user_id)
        self.audit("fechamento", f"comanda {order_id}")
        return change

    def cancel(self, order_id, reason):
        o, items, _ = self.order_detail(order_id)
        if (items or o["status"] == "fechada") and not self.is_manager:
            raise BusinessError("Cancelar comanda com itens ou estornar venda exige o gerente.")
        prev = services.cancel_order(self.conn, order_id, reason, self.user_id)
        self.audit("cancelamento" if prev == "aberta" else "estorno", f"comanda {order_id}: {reason}")

    def transfer(self, order_id, table_id):
        services.transfer_order(self.conn, order_id, table_id)
        self.audit("transferencia", f"comanda {order_id} -> mesa {table_id}")

    def merge(self, target_id, source_id):
        services.merge_orders(self.conn, target_id, source_id)
        self.audit("juntar", f"comanda {source_id} -> {target_id}")

    # --------------------------------------------------------------- cozinha
    def send_to_kitchen(self, order_id):
        n = services.send_to_kitchen(self.conn, order_id)
        self.audit("cozinha_enviar", f"comanda {order_id}: {n} item(ns)")
        return n

    def kitchen_tickets(self):
        return services.kitchen_tickets(self.conn)

    def kitchen_level(self, minutes):
        return services.kitchen_level(self.conn, minutes)

    def kitchen_step(self, order_id, step):
        services.kitchen_step(self.conn, order_id, step)

    def request_bill(self, order_id, flag=True):
        services.set_bill_requested(self.conn, order_id, flag)

    def free_tables(self):
        return [c for c in self.board() if c["kind"] == "mesa" and not c["busy"]]

    def busy_tables(self, except_order=None):
        return [c for c in self.board() if c["kind"] == "mesa" and c["busy"] and c["order_id"] != except_order]

    def receipt_text(self, order_id, width=receipt.WIDTH):
        o = services.get_order(self.conn, order_id)
        if not o:
            raise BusinessError("Comanda não encontrada.")
        return receipt.build_receipt(self.conn, o, width)

    def split_bill(self, order_id, people):
        from comandafacil.money import split_evenly
        _, _, t = self.order_detail(order_id)
        return split_evenly(t["total"], people)

    # --------------------------------------------------- produtos (gerente)
    def all_products(self):
        return self.conn.execute("SELECT * FROM products ORDER BY category, name").fetchall()

    def save_product(self, product_id, name, category, price_cents, available, track_stock, stock, stock_min,
                     cost_cents=0):
        self.require_manager()
        name, category = (name or "").strip(), (category or "").strip() or "Outros"
        if not name:
            raise BusinessError("Informe o nome do produto.")
        if price_cents < 0:
            raise BusinessError("Preço inválido.")
        if cost_cents < 0:
            raise BusinessError("Custo inválido.")
        if product_id:
            with transaction(self.conn):
                old = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
                if not old:
                    raise BusinessError("Produto não encontrado.")
                self.conn.execute(
                    "UPDATE products SET name=?,category=?,price_cents=?,available=?,track_stock=?,stock_min=?,cost_cents=? "
                    "WHERE id=?", (name, category, price_cents, int(available), int(track_stock),
                                   int(stock_min), int(cost_cents), product_id))
                if track_stock and stock != old["stock"]:
                    services._stock_move(self.conn, product_id, stock - old["stock"], "ajuste manual",
                                         None, self.user_id)
        else:
            cur = self.conn.execute(
                "INSERT INTO products(name,category,price_cents,available,track_stock,stock,stock_min,cost_cents) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (name, category, price_cents, int(available), int(track_stock),
                 int(stock) if track_stock else 0, int(stock_min), int(cost_cents)))
            product_id = cur.lastrowid
        self.audit("produto_salvo", f"{name} ({format_brl(price_cents)})")
        return product_id

    def delete_product(self, product_id):
        self.require_manager()
        self.conn.execute("DELETE FROM products WHERE id=?", (product_id,))
        self.audit("produto_excluido", str(product_id))

    # ------------------------------------------------------------- relatórios
    def report(self, start, end):
        self.require_manager()
        return services.sales_report(self.conn, start, end)

    def suggestions(self, order_id, limit=3):
        return services.suggestions(self.conn, order_id, limit)

    def low_stock(self):
        return services.low_stock_products(self.conn)

    # ----------------------------------------------------------- pedidos online
    def _client(self):
        return online.CloudClient.from_conn(self.conn, transport=self.cloud_transport)

    def online_configured(self) -> bool:
        return online.is_configured(self.conn)

    def online_activate(self, code):
        self.require_manager()
        online.activate(self.conn, code)
        self.audit("online_ativado")

    def online_deactivate(self):
        self.require_manager()
        online.deactivate(self.conn)
        self.audit("online_desativado")

    def online_save_settings(self, delivery, pickup, fee_cents, min_cents, hours, notice, site_url="", autoprint=False, printer=""):
        self.require_manager()
        d = self.conn
        dbm.set_setting(d, "online_delivery", "1" if delivery else "0")
        dbm.set_setting(d, "online_pickup", "1" if pickup else "0")
        dbm.set_setting(d, "online_fee_cents", max(0, int(fee_cents)))
        dbm.set_setting(d, "online_min_cents", max(0, int(min_cents)))
        dbm.set_setting(d, "online_hours", (hours or "").strip()[:200])
        dbm.set_setting(d, "online_notice", (notice or "").strip()[:300])
        dbm.set_setting(d, "online_site", (site_url or "").strip())
        dbm.set_setting(d, "online_autoprint", "1" if autoprint else "0")
        dbm.set_setting(d, "online_printer", (printer or "").strip()[:120])

    def online_publish(self):
        self.require_manager()
        n = online.publish(self.conn, self._client())
        self.audit("cardapio_publicado", f"{n} itens")
        return n

    def online_set_accepting(self, flag):
        if flag:
            self.require_cash()
        self._client().set_accepting(flag)
        dbm.set_setting(self.conn, "online_accepting", "1" if flag else "0")

    def online_accepting(self) -> bool:
        return self.setting("online_accepting", "0") == "1"

    def online_sync(self):
        res = online.sync(self.conn, self._client())
        dbm.set_setting(self.conn, "online_accepting", "1" if res["accepting"] else "0")
        return res

    def online_orders(self, active_only=True):
        return online.list_orders(self.conn, active_only)

    def online_new_count(self) -> int:
        return online.unseen_count(self.conn)

    def online_accept(self, code):
        self.require_cash()
        oid = online.accept(self.conn, code, self.user_id)
        self.audit("online_aceito", code)
        return oid

    def online_reject(self, code, reason=""):
        online.reject(self.conn, code, reason)
        self.audit("online_recusado", f"{code} {reason}")

    def online_dispatched(self, code):
        online.mark_dispatched(self.conn, code)

    def online_ready(self, code):
        online.mark_ready(self.conn, code)

    def online_wa(self, code):
        return online.wa_message(self.conn, code)

    def online_autoprint(self):
        """(ligado, nome da impressora; vazio = padrão do Windows)."""
        return self.setting("online_autoprint", "0") == "1", self.setting("online_printer", "")

    def online_ticket(self, code):
        return receipt.build_online_ticket(self.conn, online.get(self.conn, code))

    # -------------------------------------------------------------- atualização
    def app_version(self) -> str:
        return updater.current_version()

    def update_fetch(self):
        """Só rede (seguro em thread): None se está na última versão; senão o dicionário da Release."""
        return updater.check_for_update(opener=self.update_opener)

    def update_checked(self):
        dbm.set_setting(self.conn, "update_last_check", dbm.now())

    def update_history(self) -> str:
        parts = [f"VERSÃO {v}\n{body}" for v, body in updater.changelog_sections(updater.changelog_text())]
        return "\n\n".join(parts) or "Sem histórico de novidades."

    def update_authorize(self, info):
        """Roda na thread da tela, antes de baixar."""
        self.require_manager()
        self.audit("atualizacao_iniciada", info["version"])

    def update_fetch_installer(self, info, progress=None):
        """Só rede/disco (seguro em thread): baixa e confere o instalador."""
        import tempfile
        return updater.download_installer(info, tempfile.gettempdir() + "/ComandaFacil-update", progress,
                                          self.update_opener)

    def update_launch(self, path):
        updater.launch_installer(path)

    # ------------------------------------------------------------------- início
    def home_stats(self):
        """Indicadores do dia para a tela inicial."""
        from datetime import timedelta
        today = clock.today()
        r = services.sales_report(self.conn, today, today)
        y = services.sales_report(self.conn, today - timedelta(days=1), today - timedelta(days=1))
        busy = [b for b in self.board() if b["busy"]]
        tickets = services.kitchen_tickets(self.conn)
        late = sum(1 for t in tickets if t["state"] != "pronto"
                   and services.kitchen_level(self.conn, minutes_since(t["since"])) == "bad")
        sess = cash.current_session(self.conn)
        return {
            "today": today, "total": r["total"], "orders": r["orders"], "avg": r["avg"],
            "delta_pct": None if not y["total"] else round((r["total"] - y["total"]) * 100 / y["total"]),
            "yesterday_total": y["total"], "open_count": len(busy), "open_total": sum(b["total"] for b in busy),
            "bills": sum(1 for b in busy if b["bill"]), "ready": sum(1 for b in busy if b["ready"]),
            "by_hour": r["by_hour"], "top": r["top_products"][:5], "by_payment": r["by_payment"],
            "cancelled": r["cancelled_n"], "kitchen": len(tickets), "kitchen_late": late,
            "low_stock": [dict(p) for p in services.low_stock_products(self.conn)][:5],
            "cash_open": sess is not None,
            "online_new": online.unseen_count(self.conn), "online_active": len(online.list_orders(self.conn)),
            "online_enabled": online.is_configured(self.conn),
        }
