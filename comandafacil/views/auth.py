import re
import time

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db as dbm
from ..db import get_db, now
from ..security import audit

bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
MIN_PASSWORD = 8
_fails: dict[str, list[float]] = {}  # tentativas recentes por IP+usuário


def _blocked(key):
    recent = [t for t in _fails.get(key, []) if time.time() - t < 300]
    _fails[key] = recent
    return len(recent) >= 5


def _safe_next(url):
    return url if url and url.startswith("/") and not url.startswith("//") else url_for("main.index")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        name = request.form.get("display_name", "").strip() or username
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if not USERNAME_RE.match(username):
            flash("Usuário: 3 a 30 caracteres (letras, números, ponto, hífen ou _).", "erro")
        elif len(pw) < MIN_PASSWORD or pw != pw2:
            flash(f"A senha precisa ter ao menos {MIN_PASSWORD} caracteres e coincidir nos dois campos.", "erro")
        else:
            cur = conn.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                (username, name, generate_password_hash(pw), "gerente", now()))
            session.clear()
            session["uid"], session.permanent = cur.lastrowid, True
            audit(conn, "setup", f"gerente inicial: {username}", who=username)
            flash("Conta de gerente criada. Bem-vindo!", "sucesso")
            return redirect(url_for("main.index"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        key = f"{request.remote_addr}|{username.lower()}"
        if _blocked(key):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "erro")
        else:
            row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                _fails.pop(key, None)
                session.clear()
                session["uid"], session.permanent = row["id"], True
                audit(conn, "login", "", who=row["username"])
                return redirect(_safe_next(request.args.get("next")))
            _fails.setdefault(key, []).append(time.time())
            audit(conn, "login_falhou", username[:30], who=None)
            flash("Usuário ou senha incorretos.", "erro")
    return render_template("login.html", next=request.args.get("next", ""))


@bp.post("/logout")
def logout():
    audit(get_db(), "logout")
    session.clear()
    return redirect(url_for("auth.login"))


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
