"""Segurança: chave secreta, CSRF, anti-duplo-clique, login e permissões."""
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import (abort, flash, g, has_request_context, redirect, request,
                   session, url_for)

from . import db as dbm

PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "auth.forgot", "auth.reset",
                    "auth.reset_code", "auth.google_start", "auth.google_callback", "healthz"}
HTTPS = os.environ.get("COMANDAFACIL_HTTPS") == "1"
SESSION_SHORT = 12 * 3600        # padrão: expira em 12 h
SESSION_LONG = 14 * 24 * 3600    # "manter conectado neste aparelho": 14 dias
MAX_BODY = 2 * 1024 * 1024       # nenhuma tela precisa de mais que isso (evita corpo gigante)
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


def load_secret_key() -> str:
    """Chave aleatória gerada na 1ª execução e guardada em secret.key (fora do código)."""
    env = os.environ.get("COMANDAFACIL_SECRET_KEY")
    if env:
        return env
    if dbm.USING_PG:  # sem disco persistente: a chave fica no banco
        conn = dbm.connect()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
            if row:
                return row["value"]
            key = secrets.token_hex(32)
            conn.execute("INSERT INTO settings(key,value) VALUES('secret_key',?) ON CONFLICT(key) DO NOTHING", (key,))
            return conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()["value"]
        finally:
            conn.close()
    dbm.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = dbm.DATA_DIR / "secret.key"
    if f.exists() and f.read_text().strip():
        return f.read_text().strip()
    key = secrets.token_hex(32)
    f.write_text(key)
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return key


# ------------------------------------------------- sessão e redirecionamentos
def pw_fingerprint(row) -> str:
    """Impressão da senha atual: ao trocar a senha, as sessões antigas deixam de valer."""
    return hashlib.sha256(row["password_hash"].encode()).hexdigest()[:16]


def login_user(row, remember=False):
    """Abre a sessão (sempre uma sessão nova, evitando fixação de sessão)."""
    session.clear()
    session["uid"] = row["id"]
    session["pwv"] = pw_fingerprint(row)
    session["until"] = int(time.time()) + (SESSION_LONG if remember else SESSION_SHORT)
    session.permanent = True


def safe_path(url, default=None):
    """Só aceita caminhos internos ("/algo"). Recusa //host, \\host, esquemas e caracteres de controle
    (o navegador ignora tab/quebra de linha e transformaria "/\t/host" em "//host")."""
    if not url or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        return default
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        return default
    parts = urlsplit(url)
    return default if parts.scheme or parts.netloc else url


def safe_referrer(default):
    """Volta para a página anterior só se ela for deste mesmo site."""
    ref = request.referrer
    if ref:
        parts = urlsplit(ref)
        if parts.netloc == request.host:
            return safe_path(parts.path + (f"?{parts.query}" if parts.query else ""), default)
    return default


# --------------------------------------------------------------------- CSRF
def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def check_csrf():
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, "Sessão expirada ou requisição inválida. Recarregue a página e tente de novo.")


# --------------------------------------------- idempotência (duplo clique)
_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


def new_rid() -> str:
    return secrets.token_hex(8)


def is_duplicate_request() -> bool:
    rid = request.form.get("_rid")
    if not rid:
        return False
    t = time.time()
    with _seen_lock:
        for k, ts in list(_seen.items()):
            if t - ts > 300:
                del _seen[k]
        if rid in _seen:
            return True
        _seen[rid] = t
    return False


def idempotent(view):
    """Cada formulário carrega um _rid único; reenvio do mesmo _rid é ignorado."""
    @wraps(view)
    def wrapper(*a, **kw):
        if is_duplicate_request():
            flash("Essa ação já tinha sido registrada (clique duplicado ignorado).", "info")
            return redirect(safe_referrer(url_for("main.index")))
        return view(*a, **kw)
    return wrapper


# -------------------------------------------------------------- auditoria
def audit(conn, action, details="", who=None):
    if who is None and has_request_context() and getattr(g, "user", None):
        who = g.user["username"]
    conn.execute("INSERT INTO audit_log(ts,user_name,action,details) VALUES (?,?,?,?)",
                 (dbm.now(), who, action, details))


# ------------------------------------------------------------ permissões
def manager_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not g.user or g.user["role"] != "gerente":
            abort(403)
        return view(*a, **kw)
    return wrapper


def is_manager() -> bool:
    return bool(getattr(g, "user", None) and g.user["role"] == "gerente")


def init_security(app):
    app.secret_key = load_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=HTTPS,
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_LONG),
        MAX_CONTENT_LENGTH=MAX_BODY,
    )

    @app.before_request
    def _guard():
        g.user = None
        if request.endpoint is None or request.endpoint == "static":
            return
        conn = dbm.get_db()
        uid = session.get("uid")
        if uid and time.time() > session.get("until", float("inf")):
            session.clear()  # sessão vencida
            uid = None
        if uid:
            row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            if row and session.get("pwv", pw_fingerprint(row)) == pw_fingerprint(row):
                g.user = row
                session.setdefault("pwv", pw_fingerprint(row))
            else:  # usuário desativado ou senha trocada: derruba a sessão
                session.clear()
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            check_csrf()
        if request.endpoint in PUBLIC_ENDPOINTS:
            return
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            return redirect(url_for("auth.setup"))
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if HTTPS:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp
