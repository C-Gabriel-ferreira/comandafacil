"""Banco SQLite: conexão, esquema, migrações e backups."""
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .clock import now_dt

try:  # driver do Postgres (só necessário quando DATABASE_URL está definida)
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


def _pg_url():
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    if not url:
        return ""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "supa"]  # 'supa' não é aceito pelo libpq
    if parts.scheme == "postgres":
        parts = parts._replace(scheme="postgresql")
    return urlunsplit(parts._replace(query=urlencode(query)))


PG_URL = _pg_url()
USING_PG = bool(PG_URL)
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + ((psycopg.errors.IntegrityError,) if psycopg else ())
PG_LOCK_KEY = 74_206_001

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
_default_data = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("COMANDAFACIL_DATA_DIR") or _default_data)
DB_PATH = DATA_DIR / "comandas.db"
BACKUP_DIR = DATA_DIR / "backups"

SCHEMA_VERSION = 5

# Controle de caixa (v3, só SQLite — usado pelo programa desktop): turnos e sangria/suprimento.
CASH_SQL = """
CREATE TABLE IF NOT EXISTS cash_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at TEXT NOT NULL, opened_by INTEGER,
    opening_cents INTEGER NOT NULL CHECK(opening_cents >= 0),
    closed_at TEXT, closed_by INTEGER,
    expected_cents INTEGER, counted_cents INTEGER, note TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_cash ON cash_sessions((1)) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS cash_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES cash_sessions(id),
    kind TEXT NOT NULL CHECK(kind IN ('sangria','suprimento')),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    reason TEXT NOT NULL, user_id INTEGER, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cash_mov_session ON cash_movements(session_id);
"""

# Pedidos online (v4, só SQLite — programa desktop): caixa de entrada vinda do cardápio digital.
ONLINE_SQL = """
CREATE TABLE IF NOT EXISTS online_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('entrega','retirada')),
    customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL, address TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '', payment_method TEXT NOT NULL, change_for_cents INTEGER,
    subtotal_cents INTEGER NOT NULL, delivery_fee_cents INTEGER NOT NULL DEFAULT 0, total_cents INTEGER NOT NULL,
    items_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'novo',
    pushed_status TEXT NOT NULL DEFAULT 'novo',
    reject_reason TEXT NOT NULL DEFAULT '',
    order_id INTEGER REFERENCES orders(id),
    received_at TEXT NOT NULL, decided_at TEXT, seen INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_online_status ON online_orders(status);
"""

# Migrações: {versão: "SQL;"} — rodam em ordem, com backup antes.
MIGRATIONS: dict[int, str] = {
    2: """
    ALTER TABLE order_items ADD COLUMN kitchen_status TEXT NOT NULL DEFAULT 'novo';
    ALTER TABLE order_items ADD COLUMN sent_at TEXT;
    ALTER TABLE order_items ADD COLUMN ready_at TEXT;
    ALTER TABLE orders ADD COLUMN bill_requested_at TEXT;
    """,
    3: CASH_SQL,
    4: ONLINE_SQL,
    5: """
    ALTER TABLE products ADD COLUMN cost_cents INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE order_items ADD COLUMN unit_cost_cents INTEGER NOT NULL DEFAULT 0;
    """,
}

DEFAULT_SETTINGS = {
    "business_name": "ComandaFácil",
    "business_phone": "",
    "service_pct": "10",
    "service_auto_mesa": "0",
    "footer_message": "Obrigado pela preferência!",
}

# Identidade e suporte do fornecedor do sistema (aparece no rodapé, no cupom e no botão Suporte).
VENDOR = {
    "name": "HelpTech",
    "phone": "(94) 99101-6248",
    "whatsapp": "5594991016248",
    "email": "helptechcanaa@gmail.com",
}


def configure(data_dir):
    global DATA_DIR, DB_PATH, BACKUP_DIR
    DATA_DIR = Path(data_dir)
    DB_PATH = DATA_DIR / "comandas.db"
    BACKUP_DIR = DATA_DIR / "backups"


def now() -> str:
    return now_dt().isoformat(timespec="seconds")


class Row:
    """Linha de resultado com acesso por nome, índice e keys() (igual ao sqlite3.Row)."""
    __slots__ = ("_names", "_vals")

    def __init__(self, names, vals):
        self._names, self._vals = names, vals

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return self._vals[k]
        return self._vals[self._names.index(k)]

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __repr__(self):
        return f"Row({dict(zip(self._names, self._vals))})"


def _translate(sql):
    sql = sql.replace("%", "%%").replace("?", "%s")
    return re.sub(r"GROUP_CONCAT\(", "string_agg(", sql)


class PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid
        self.rowcount = cur.rowcount
        self._names = [d.name for d in cur.description] if cur.description else []

    def _wrap(self, r):
        return None if r is None else Row(self._names, r)

    def fetchone(self):
        return self._wrap(self._cur.fetchone()) if self._names else None

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()] if self._names else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConn:
    """Adaptador mínimo: expõe a API do sqlite3 (execute/executemany/'?'/lastrowid) sobre psycopg."""

    def __init__(self):
        self._c = psycopg.connect(PG_URL, autocommit=True, prepare_threshold=None, connect_timeout=15)

    def execute(self, sql, params=()):
        sql = _translate(sql)
        returning = False
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        if m and m.group(1).lower() != "settings" and "returning" not in sql.lower():
            sql += " RETURNING id"
            returning = True
        cur = self._c.execute(sql, tuple(params))
        last = None
        if returning:
            row = cur.fetchone()
            last = row[0] if row else None
            return PgCursor(_Done(cur.rowcount), last)
        return PgCursor(cur)

    def executemany(self, sql, seq):
        with self._c.cursor() as cur:
            cur.executemany(_translate(sql), [tuple(p) for p in seq])

    def close(self):
        self._c.close()


class _Done:
    description = None

    def __init__(self, rowcount):
        self.rowcount = rowcount


def connect(path=None):
    if USING_PG and path is None:
        return PgConn()
    conn = sqlite3.connect(str(path or DB_PATH), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db() -> sqlite3.Connection:
    from flask import g
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    from flask import g
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn):
    """Transação com bloqueio de escrita imediato (evita corrida entre cliques duplos)."""
    if USING_PG:
        conn.execute("BEGIN")
        conn.execute("SELECT pg_advisory_xact_lock(?)", (PG_LOCK_KEY,))  # serializa escritas (como o SQLite)
    else:
        conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, default)


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('gerente','garcom')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Outros',
    price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
    available INTEGER NOT NULL DEFAULT 1,
    track_stock INTEGER NOT NULL DEFAULT 0,
    stock INTEGER NOT NULL DEFAULT 0,
    stock_min INTEGER NOT NULL DEFAULT 0,
    cost_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'mesa' CHECK(kind IN ('mesa','balcao','delivery')),
    table_id INTEGER REFERENCES tables(id),
    customer_name TEXT, customer_phone TEXT, address TEXT,
    status TEXT NOT NULL DEFAULT 'aberta'
        CHECK(status IN ('aberta','fechada','cancelada','juntada')),
    service_enabled INTEGER NOT NULL DEFAULT 0,
    service_pct INTEGER NOT NULL DEFAULT 10,
    delivery_fee_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, created_by INTEGER REFERENCES users(id),
    closed_at TEXT, closed_by INTEGER,
    subtotal_cents INTEGER, service_cents INTEGER, total_cents INTEGER,
    cancelled_at TEXT, cancelled_by INTEGER, cancel_reason TEXT,
    merged_into INTEGER,
    bill_requested_at TEXT
);
CREATE UNIQUE INDEX idx_one_open_per_table ON orders(table_id)
    WHERE status = 'aberta' AND table_id IS NOT NULL;
CREATE INDEX idx_orders_status_closed ON orders(status, closed_at);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT,
    kitchen_status TEXT NOT NULL DEFAULT 'novo',
    sent_at TEXT, ready_at TEXT,
    unit_cost_cents INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    method TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0)
);
CREATE TABLE stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    delta INTEGER NOT NULL, reason TEXT NOT NULL,
    order_id INTEGER, user_id INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, user_name TEXT, action TEXT NOT NULL, details TEXT
);
"""

PG_SCHEMA = (SCHEMA
    .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    .replace("username TEXT NOT NULL UNIQUE COLLATE NOCASE,", "username TEXT NOT NULL,")
    + """
CREATE UNIQUE INDEX idx_users_username ON users(lower(username));
CREATE INDEX idx_payments_order ON payments(order_id);
CREATE INDEX idx_stock_product ON stock_movements(product_id);
CREATE INDEX idx_orders_table ON orders(table_id);
CREATE INDEX idx_orders_created_by ON orders(created_by);
""" + "".join(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;\n" for t in (
    "settings", "users", "products", "tables", "orders", "order_items", "payments",
    "stock_movements", "audit_log")))

SEED_PRODUCTS = [
    ("Água mineral", "Bebidas", 300), ("Refrigerante lata", "Bebidas", 600),
    ("Suco natural", "Bebidas", 800), ("Café", "Bebidas", 400),
    ("Hambúrguer", "Lanches", 2200), ("Batata frita", "Porções", 1800),
    ("Pizza individual", "Pratos", 2800), ("Prato feito", "Pratos", 2500),
]


def _create_schema(conn):
    conn.executescript(SCHEMA)
    conn.executescript(CASH_SQL)
    conn.executescript(ONLINE_SQL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    for k, v in DEFAULT_SETTINGS.items():
        set_setting(conn, k, v)


def _seed_examples(conn):
    conn.executemany("INSERT INTO tables(name) VALUES (?)", [(f"Mesa {i}",) for i in range(1, 13)])
    conn.executemany("INSERT INTO products(name,category,price_cents) VALUES (?,?,?)", SEED_PRODUCTS)


def _upgrade_legacy_database():
    """Converte o banco da versão antiga (preços REAL) para centavos, SEM alterar o original.

    Constrói um banco novo em arquivo temporário; só no fim guarda uma cópia do antigo
    (comandas_antes_da_v2_*.db) e troca os arquivos. Se algo falhar, nada muda.
    """
    tmp = DB_PATH.with_name("comandas.novo.tmp")
    tmp.unlink(missing_ok=True)
    conn = connect(tmp)
    try:
        _create_schema(conn)
        conn.execute("ATTACH DATABASE ? AS old", (str(DB_PATH),))
        with transaction(conn):
            conn.execute("""INSERT INTO products(id,name,category,price_cents,available)
                SELECT id,name,category,CAST(ROUND(price*100) AS INTEGER),available FROM old.products""")
            conn.execute("INSERT INTO tables(id,name) SELECT id,name FROM old.tables")
            conn.execute("""
                INSERT INTO orders(id,kind,table_id,status,created_at,closed_at,
                                   subtotal_cents,service_cents,total_cents,cancel_reason)
                SELECT o.id,'mesa',o.table_id,
                  CASE WHEN o.status='aberta' AND o.id < (SELECT MAX(x.id) FROM old.orders x
                       WHERE x.table_id=o.table_id AND x.status='aberta')
                       THEN 'cancelada' ELSE o.status END,
                  o.created_at,o.closed_at,
                  CASE WHEN o.status='fechada' THEN COALESCE(t.s,0) END,
                  CASE WHEN o.status='fechada' THEN 0 END,
                  CASE WHEN o.status='fechada' THEN COALESCE(t.s,0) END,
                  CASE WHEN o.status='aberta' AND o.id < (SELECT MAX(x.id) FROM old.orders x
                       WHERE x.table_id=o.table_id AND x.status='aberta')
                       THEN 'Comanda aberta duplicada (migração)' END
                FROM old.orders o
                LEFT JOIN (SELECT order_id, SUM(CAST(ROUND(unit_price*100) AS INTEGER)*quantity) AS s
                           FROM old.order_items GROUP BY order_id) t ON t.order_id=o.id""")
            conn.execute("""INSERT INTO order_items(id,order_id,product_id,product_name,
                                                   unit_price_cents,quantity,note,created_at)
                SELECT i.id,i.order_id,i.product_id,i.product_name,
                       CAST(ROUND(i.unit_price*100) AS INTEGER),i.quantity,'',o.created_at
                FROM old.order_items i JOIN old.orders o ON o.id=i.order_id""")
            conn.execute("""INSERT INTO payments(order_id,method,amount_cents)
                SELECT n.id, COALESCE(o.payment_method,'Dinheiro'), n.total_cents
                FROM orders n JOIN old.orders o ON o.id=n.id
                WHERE n.status='fechada' AND n.total_cents > 0""")
            for table in ("products", "tables", "orders", "order_items"):
                a = conn.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
                b = conn.execute(f"SELECT COUNT(*) FROM old.{table}").fetchone()[0]
                if a != b:
                    raise RuntimeError(f"Migração inconsistente em {table}: {b} -> {a}")
        conn.execute("DETACH DATABASE old")
        conn.close()
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    stamp = now_dt().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(DB_PATH, DB_PATH.with_name(f"comandas_antes_da_v2_{stamp}.db"))
    os.replace(tmp, DB_PATH)


def _init_pg():
    conn = connect()
    try:
        with transaction(conn):
            if conn.execute("SELECT to_regclass('public.settings')").fetchone()[0] is None:
                for stmt in PG_SCHEMA.split(";"):
                    if stmt.strip():
                        conn.execute(stmt)
                for k, v in DEFAULT_SETTINGS.items():
                    set_setting(conn, k, v)
                _seed_examples(conn)
    finally:
        conn.close()


def init_db():
    if USING_PG:
        return _init_pg()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    legacy = False
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        probe = sqlite3.connect(DB_PATH)
        try:
            version = probe.execute("PRAGMA user_version").fetchone()[0]
            has_products = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'").fetchone()
        finally:
            probe.close()
        if version > SCHEMA_VERSION:
            raise RuntimeError("Este banco é de uma versão mais nova do ComandaFácil.")
        legacy = version == 0 and bool(has_products)
    if legacy:
        _upgrade_legacy_database()
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            _create_schema(conn)
            _seed_examples(conn)
        else:
            for v in range(version + 1, SCHEMA_VERSION + 1):
                make_backup("pre_migracao")
                conn.executescript(f"BEGIN;{MIGRATIONS[v]};PRAGMA user_version={v};COMMIT;")
    finally:
        conn.close()


# ----------------------------------------------------------------- backups
def make_backup(label="manual") -> Path:
    if USING_PG:
        raise RuntimeError("Backups são gerenciados pelo Supabase.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now_dt().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"comandas_{stamp}_{label}.db"
    src, dst = connect(), sqlite3.connect(dest)
    try:
        src.backup(dst)  # cópia consistente mesmo com o sistema em uso
    finally:
        dst.close()
        src.close()
    prune_backups()
    return dest


def prune_backups(keep_auto=14):
    autos = sorted(BACKUP_DIR.glob("comandas_*_auto.db"))
    for old in autos[:-keep_auto]:
        old.unlink(missing_ok=True)


def list_backups():
    if USING_PG:
        return []
    if not BACKUP_DIR.exists():
        return []
    rows = []
    for f in sorted(BACKUP_DIR.glob("comandas_*.db"), reverse=True):
        st = f.stat()
        rows.append({"name": f.name, "size_kb": max(1, st.st_size // 1024),
                     "when": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M")})
    return rows


def daily_backup_if_needed():
    if USING_PG:
        return None
    today = now_dt().strftime("%Y-%m-%d")
    if BACKUP_DIR.exists() and any(BACKUP_DIR.glob(f"comandas_{today}_*_auto.db")):
        return None
    return make_backup("auto")


def start_backup_thread(interval_s=1800):
    def loop():
        while True:
            try:
                daily_backup_if_needed()
            except Exception as exc:  # nunca derrubar o sistema por causa do backup
                print("Falha no backup automático:", exc, file=sys.stderr)
            time.sleep(interval_s)
    threading.Thread(target=loop, daemon=True, name="backup").start()
