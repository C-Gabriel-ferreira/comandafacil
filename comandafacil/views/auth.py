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
from .. import security
from ..security import audit, login_user, pw_fingerprint, safe_path

bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,120}\.[^@\s.]{2,}$")
MIN_PASSWORD = 8
RESET_TTL_MIN = 30           # validade do link de recuperação
RESET_MAX_PER_HOUR = 3       # pedidos por conta
_fails: dict[str, list[float]] = {}  # tentativas recentes (login, recuperação, Google)
_DUMMY_HASH = generate_password_hash("sem-usuario")  # gasta o mesmo tempo quando o usuário não existe
COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "12341234", "11111111", "00000000", "87654321", "123123123",
    "senha123", "senha1234", "senha@123", "minhasenha", "mudar123", "mudar@123", "abc12345", "abcd1234",
    "qwerty123", "qwertyui", "qwertyuiop", "password", "password1", "password123", "iloveyou", "admin123",
    "admin1234", "comandafacil", "comanda123", "restaurante", "brasil123", "corinthians", "flamengo1",
}


def _recent(key, window):
    recent = [t for t in _fails.get(key, []) if time.time() - t < window]
    _fails[key] = recent
    return recent


def _blocked(key, limit=5, window=300):
    return len(_recent(key, window)) >= limit


def _wait_text(key, window):
    """"1 minuto" / "4 minutos": quanto falta para a tentativa mais antiga sair da janela."""
    recent = _recent(key, window)
    secs = max(1, int(window - (time.time() - min(recent)))) if recent else 60
    mins = max(1, -(-secs // 60))
    return f"{mins} minuto" + ("" if mins == 1 else "s")


def _hit(key):
    if len(_fails) > 5000:  # chaves antigas não podem encher a memória
        for k in [k for k, v in _fails.items() if not v or time.time() - max(v) > 3600]:
            _fails.pop(k, None)
    _fails.setdefault(key, []).append(time.time())


def password_problem(pw, username=""):
    """Mensagem de erro se a senha for fraca demais (curta, óbvia ou igual ao usuário); senão None."""
    if len(pw) < MIN_PASSWORD:
        return f"A senha precisa ter ao menos {MIN_PASSWORD} caracteres."
    low = pw.lower()
    if low in COMMON_PASSWORDS or len(set(low)) < 3 or (username and low == username.strip().lower()):
        return "Essa senha é fácil de adivinhar. Misture letras e números, ou use uma frase."
    return None


def _safe_next(url):
    return safe_path(url, url_for("main.index"))


def _public_url_ok():
    """Link de e-mail só é seguro se o endereço público for fixo: com HTTPS ligado e sem
    COMANDAFACIL_PUBLIC_URL, o link usaria o cabeçalho Host, que um atacante pode forjar."""
    return bool(os.environ.get("COMANDAFACIL_PUBLIC_URL", "").strip()) or not security.HTTPS


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
        elif pw != pw2:
            flash("As duas senhas precisam ser iguais.", "erro")
        elif (problem := password_problem(pw, username)):
            flash(problem, "erro")
        else:
            pw_hash = generate_password_hash(pw)
            cur = conn.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at,email) VALUES (?,?,?,?,?,?)",
                (username, name, pw_hash, "gerente", now(), email))
            login_user({"id": cur.lastrowid, "password_hash": pw_hash})
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
    extra.setdefault("error", False)
    return render_template("login.html", next=request.args.get("next", ""),
                           google_enabled=google_auth.is_configured(), **extra)


@bp.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    if request.method == "POST":
        ident = request.form.get("username", "").strip()[:120]
        ip = request.remote_addr
        # três limites: este IP + este usuário; este IP em geral (varredura de usuários);
        # este usuário vindo de qualquer IP (ataque distribuído)
        limits = ((f"{ip}|{ident.lower()}", 5, 300), (f"ip|{ip}", 20, 300), (f"acct|{ident.lower()}", 15, 900))
        hit = next(((k, w) for k, lim, w in limits if _blocked(k, lim, w)), None)
        if hit:
            flash(f"Muitas tentativas. Tente de novo em {_wait_text(*hit)}.", "erro")
            return _login_page(ident=ident, error=True)
        row = conn.execute(
            "SELECT * FROM users WHERE (lower(username)=lower(?) OR lower(email)=lower(?)) AND active=1",
            (ident, ident)).fetchone()
        password = request.form.get("password", "")
        # confere a senha mesmo sem usuário: o tempo de resposta não revela quais usuários existem
        ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password) and row
        if ok:
            _fails.pop(limits[0][0], None)
            _fails.pop(limits[2][0], None)
            login_user(row, remember=request.form.get("remember") == "1")
            audit(conn, "login", "", who=row["username"])
            return redirect(_safe_next(request.args.get("next")))
        for k, _lim, _w in limits:
            _hit(k)
        audit(conn, "login_falhou", ident[:30], who=None)
        flash("Usuário ou senha incorretos. Confira e tente de novo.", "erro")
        return _login_page(ident=ident, error=True)
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
    login_user(row)
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
    enabled = mailer.is_configured() and _public_url_ok()
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
        if pw != pw2:
            flash("As duas senhas precisam ser iguais.", "erro")
        elif (problem := password_problem(pw, row["username"])):
            flash(problem, "erro")
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
        elif pw != pw2:  # confere antes de gastar o código
            flash("As duas senhas precisam ser iguais.", "erro")
        elif (problem := password_problem(pw, row["username"])):
            flash(problem, "erro")
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
        elif new != new2:
            flash("As duas senhas novas precisam ser iguais.", "erro")
        elif (problem := password_problem(new, g.user["username"])):
            flash(problem, "erro")
        else:
            conn = get_db()
            new_hash = generate_password_hash(new)
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, g.user["id"]))
            session["pwv"] = pw_fingerprint({"password_hash": new_hash})  # esta sessão continua; as outras caem
            audit(conn, "senha_alterada")
            flash("Senha alterada.", "sucesso")
            return redirect(url_for("main.index"))
    return render_template("senha.html")
