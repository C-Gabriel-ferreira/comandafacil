"""Segurança: chave secreta, CSRF, anti-duplo-clique, login e permissões."""
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps

from flask import (abort, flash, g, has_request_context, redirect, request,
                   session, url_for)

from . import db as dbm

PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "healthz"}
HTTPS = os.environ.get("COMANDAFACIL_HTTPS") == "1"


def load_secret_key() -> str:
    """Chave aleatória gerada na 1ª execução e guardada em secret.key (fora do código)."""
    env = os.environ.get("COMANDAFACIL_SECRET_KEY")
    if env:
        return env
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
            return redirect(request.referrer or url_for("main.index"))
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
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    @app.before_request
    def _guard():
        g.user = None
        if request.endpoint is None or request.endpoint == "static":
            return
        conn = dbm.get_db()
        uid = session.get("uid")
        if uid:
            row = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            if row:
                g.user = row
            else:
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
        if HTTPS:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp
