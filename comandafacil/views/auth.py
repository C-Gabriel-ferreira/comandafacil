import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db as dbm
from .. import google_auth, mailer
from .. import license as lic
from ..clock import now_dt
from ..db import get_db, now
from ..security import audit

bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,120}\.[^@\s.]{2,}$")
MIN_PASSWORD = 8
RESET_TTL_MIN = 30           # validade do link de recuperação
RESET_MAX_PER_HOUR = 3       # pedidos por conta
_fails: dict[str, list[float]] = {}  # tentativas recentes (login, recuperação, Google)


def _blocked(key, limit=5, window=300):
    recent = [t for t in _fails.get(key, []) if time.time() - t < window]
    _fails[key] = recent
    return len(recent) >= limit


def _hit(key):
    _fails.setdefault(key, []).append(time.time())


def _safe_next(url):
    ok = url and url.startswith("/") and not url.startswith("//") and "\\" not in url
    return url if ok else url_for("main.index")


def normalize_email(value):
    """E-mail em minúsculas, ou None se vazio. Levanta ValueError se for inválido."""
    value = (value or "").strip().lower()
    if not value:
        return None
    if len(value) > 120 or not EMAIL_RE.match(value):
        raise ValueError("E-mail inválido.")
    return value


def _base_url():
    """Endereço público do sistema (links de e-mail e retorno do Google).
    Em servidor na internet, defina COMANDAFACIL_PUBLIC_URL para não depender do cabeçalho Host."""
    return (os.environ.get("COMANDAFACIL_PUBLIC_URL", "").strip().rstrip("/")
            or request.host_url.rstrip("/"))


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------- setup
@bp.route("/setup", methods=["GET", "POST"])
def setup():
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        name = request.form.get("display_name", "").strip() or username
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        token = request.form.get("license", "").strip()
        err, email = None, None
        try:
            email = normalize_email(request.form.get("email"))
            if token:  # a licença manda: o e-mail do gerente é o do responsável
                email = lic.parse(token)["owner_email"] or email
                lic.validate(token, email or "")
        except (ValueError, lic.LicenseError) as exc:
            err = str(exc)
        if err:
            flash(err, "erro")
        elif not USERNAME_RE.match(username):
            flash("Usuário: 3 a 30 caracteres (letras, números, ponto, hífen ou _).", "erro")
        elif len(pw) < MIN_PASSWORD or pw != pw2:
            flash(f"A senha precisa ter ao menos {MIN_PASSWORD} caracteres e coincidir nos dois campos.", "erro")
        else:
            cur = conn.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at,email) VALUES (?,?,?,?,?,?)",
                (username, name, generate_password_hash(pw), "gerente", now(), email))
            session.clear()
            session["uid"], session.permanent = cur.lastrowid, True
            audit(conn, "setup", f"gerente inicial: {username}", who=username)
            if token:
                info = lic.save(conn, token, email or "")
                audit(conn, "licenca_ativada", f"{info['customer']}: {info['max_users']} usuários "
                                               f"até {info['expires']:%d/%m/%Y}", who=username)
            flash("Conta de gerente criada. Bem-vindo!", "sucesso")
            return redirect(url_for("main.index"))
    return render_template("setup.html")


# --------------------------------------------------------------------- login
def _login_page(**extra):
    return render_template("login.html", next=request.args.get("next", ""),
                           google_enabled=google_auth.is_configured(), **extra)


@bp.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    if request.method == "POST":
        ident = request.form.get("username", "").strip()[:120]
        key = f"{request.remote_addr}|{ident.lower()}"
        if _blocked(key):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "erro")
        else:
            row = conn.execute(
                "SELECT * FROM users WHERE (lower(username)=lower(?) OR lower(email)=lower(?)) AND active=1",
                (ident, ident)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                _fails.pop(key, None)
                session.clear()
                session["uid"], session.permanent = row["id"], True
                audit(conn, "login", "", who=row["username"])
                return redirect(_safe_next(request.args.get("next")))
            _hit(key)
            audit(conn, "login_falhou", ident[:30], who=None)
            flash("Usuário ou senha incorretos.", "erro")
        return _login_page(ident=ident)
    return _login_page(ident="")


@bp.post("/logout")
def logout():
    audit(get_db(), "logout")
    session.clear()
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------- login Google
def _google_redirect_uri():
    return _base_url() + url_for("auth.google_callback")


@bp.get("/login/google")
def google_start():
    if not google_auth.is_configured():
        flash("O login com Google ainda não foi configurado neste sistema.", "erro")
        return redirect(url_for("auth.login"))
    verifier, challenge = google_auth.new_pkce()
    state = secrets.token_urlsafe(24)
    session["g_state"], session["g_verifier"] = state, verifier
    session["g_next"] = request.args.get("next", "")
    return redirect(google_auth.authorization_url(_google_redirect_uri(), state, challenge))


@bp.get("/login/google/retorno")
def google_callback():
    state, verifier = session.pop("g_state", None), session.pop("g_verifier", None)
    nxt = session.pop("g_next", "")
    back = redirect(url_for("auth.login", next=nxt) if nxt else url_for("auth.login"))
    if request.args.get("error"):
        flash("Login com Google cancelado.", "info")
        return back
    code = request.args.get("code", "")
    if not (state and verifier and code and hmac.compare_digest(state, request.args.get("state", ""))):
        flash("A tentativa de login com Google expirou. Tente de novo.", "erro")
        return back
    key = f"google|{request.remote_addr}"
    if _blocked(key, limit=10):
        flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "erro")
        return back
    conn = get_db()
    try:
        profile = google_auth.fetch_profile(code, _google_redirect_uri(), verifier)
    except google_auth.GoogleError as exc:
        _hit(key)
        flash(str(exc), "erro")
        return back
    sub = str(profile["sub"])
    email = (profile.get("email") or "").strip().lower()
    verified = profile.get("email_verified") in (True, "true")
    row = conn.execute("SELECT * FROM users WHERE google_sub=? AND active=1 AND role='gerente'", (sub,)).fetchone()
    if not row and email and verified:
        # 1º acesso com Google: liga a conta pelo e-mail cadastrado pelo gerente
        row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND active=1 AND role='gerente' "
                           "AND google_sub IS NULL", (email,)).fetchone()
        if row:
            conn.execute("UPDATE users SET google_sub=? WHERE id=?", (sub, row["id"]))
            audit(conn, "google_vinculado", email[:60], who=row["username"])
    if not row:
        _hit(key)
        audit(conn, "login_google_falhou", email[:60], who=None)
        who = f"A conta Google {email} não" if email else "Essa conta Google não"
        flash(f"{who} está liberada neste sistema. O login com Google é só para o gerente, com o e-mail cadastrado "
              "no usuário dele. Os demais usuários entram com usuário e senha.", "erro")
        return back
    _fails.pop(key, None)
    session.clear()
    session["uid"], session.permanent = row["id"], True
    audit(conn, "login_google", "", who=row["username"])
    return redirect(_safe_next(nxt))


# ---------------------------------------------------- recuperação de senha
def _start_reset(conn, ident):
    """Cria o link e envia por e-mail, se houver conta ativa com esse usuário/e-mail. Sem revelar se existe."""
    row = conn.execute(
        "SELECT * FROM users WHERE (username=? OR lower(email)=lower(?)) AND active=1 AND email IS NOT NULL",
        (ident, ident)).fetchone()
    if not row:
        return
    since = (now_dt() - timedelta(hours=1)).isoformat(timespec="seconds")
    if conn.execute("SELECT COUNT(*) FROM password_resets WHERE user_id=? AND created_at>?",
                    (row["id"], since)).fetchone()[0] >= RESET_MAX_PER_HOUR:
        return
    conn.execute("DELETE FROM password_resets WHERE expires_at < ?",
                 ((now_dt() - timedelta(days=7)).isoformat(timespec="seconds"),))
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO password_resets(user_id,token_hash,created_at,expires_at) VALUES (?,?,?,?)",
                 (row["id"], _hash_token(token), now(),
                  (now_dt() + timedelta(minutes=RESET_TTL_MIN)).isoformat(timespec="seconds")))
    audit(conn, "recuperacao_solicitada", "", who=row["username"])
    link = _base_url() + url_for("auth.reset", token=token)
    biz = dbm.get_setting(conn, "business_name", "ComandaFácil")
    mailer.send_async(
        row["email"], f"{biz}: redefinir sua senha",
        f"Olá, {row['display_name']}!\n\n"
        f"Recebemos um pedido para redefinir a senha do usuário \"{row['username']}\" no {biz}.\n"
        f"Para criar uma nova senha, abra o link abaixo (vale por {RESET_TTL_MIN} minutos e só funciona uma vez):\n\n"
        f"{link}\n\n"
        "Se você não pediu isso, ignore este e-mail: sua senha continua a mesma.\n", biz)


@bp.route("/login/esqueci", methods=["GET", "POST"])
def forgot():
    enabled = mailer.is_configured()
    sent, ident = False, ""
    if request.method == "POST" and enabled:
        ident = request.form.get("ident", "").strip()[:120]
        key = f"reset|{request.remote_addr}"
        if not ident:
            flash("Informe seu e-mail ou usuário.", "erro")
        elif _blocked(key, limit=5, window=900):
            flash("Muitos pedidos seguidos. Aguarde alguns minutos e tente de novo.", "erro")
        else:
            _hit(key)
            _start_reset(get_db(), ident)
            sent = True
    return render_template("esqueci.html", enabled=enabled, sent=sent, ident=ident)


def _valid_reset(conn, token):
    return conn.execute(
        """SELECT r.id, r.user_id, u.username FROM password_resets r JOIN users u ON u.id=r.user_id
           WHERE r.token_hash=? AND r.used_at IS NULL AND r.expires_at>? AND u.active=1""",
        (_hash_token(token), now())).fetchone()


@bp.route("/login/redefinir/<token>", methods=["GET", "POST"])
def reset(token):
    conn = get_db()
    row = _valid_reset(conn, token)
    if not row:
        return render_template("redefinir.html", invalid=True, token=token), 400
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < MIN_PASSWORD or pw != pw2:
            flash(f"A senha precisa ter ao menos {MIN_PASSWORD} caracteres e coincidir nos dois campos.", "erro")
        else:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), row["user_id"]))
            conn.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                         (now(), row["user_id"]))
            audit(conn, "senha_redefinida", "por e-mail", who=row["username"])
            session.clear()
            flash("Senha redefinida. Entre com a nova senha.", "sucesso")
            return redirect(url_for("auth.login"))
    return render_template("redefinir.html", invalid=False, token=token, username=row["username"])


@bp.route("/login/codigo", methods=["GET", "POST"])
def reset_code():
    """Redefinição com código emitido pelo suporte (funciona sem e-mail configurado)."""
    conn = get_db()
    if request.method == "POST":
        key = f"code|{request.remote_addr}"
        code = "".join(request.form.get("code", "").split())
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if _blocked(key, limit=5, window=900):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "erro")
            return render_template("codigo.html")
        try:
            info = lic.parse_reset_code(code)
        except lic.LicenseError as exc:
            _hit(key)
            flash(str(exc), "erro")
            return render_template("codigo.html")
        row = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND active=1", (info["email"],)).fetchone()
        if not row:
            _hit(key)
            flash("Não há usuário ativo com o e-mail deste código.", "erro")
        elif len(pw) < MIN_PASSWORD or pw != pw2:  # confere antes de gastar o código
            flash(f"A senha precisa ter ao menos {MIN_PASSWORD} caracteres e coincidir nos dois campos.", "erro")
        else:
            try:  # token_hash é UNIQUE: o mesmo código só vale uma vez
                conn.execute("INSERT INTO password_resets(user_id,token_hash,created_at,expires_at,used_at) "
                             "VALUES (?,?,?,?,?)",
                             (row["id"], _hash_token(code), now(),
                              time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(info["exp"])), now()))
            except sqlite3.IntegrityError:
                _hit(key)
                flash("Este código já foi usado. Peça um novo ao suporte.", "erro")
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), row["id"]))
                conn.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                             (now(), row["id"]))
                audit(conn, "senha_redefinida", "por código do suporte", who=row["username"])
                session.clear()
                flash("Senha redefinida. Entre com a nova senha.", "sucesso")
                return redirect(url_for("auth.login"))
    return render_template("codigo.html")


# ------------------------------------------------------------ trocar senha
@bp.route("/conta/senha", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
        if not check_password_hash(g.user["password_hash"], old):
            flash("Senha atual incorreta.", "erro")
        elif len(new) < MIN_PASSWORD or new != new2:
            flash(f"A nova senha precisa ter ao menos {MIN_PASSWORD} caracteres e coincidir.", "erro")
        else:
            conn = get_db()
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), g.user["id"]))
            audit(conn, "senha_alterada")
            flash("Senha alterada.", "sucesso")
            return redirect(url_for("main.index"))
    return render_template("senha.html")
