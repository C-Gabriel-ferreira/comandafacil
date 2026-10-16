"""Usuários, configurações, backups e auditoria (somente gerente)."""
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_from_directory, url_for
from werkzeug.security import generate_password_hash

from .. import db as dbm
from ..db import get_db, get_setting, now, set_setting
from ..security import audit, idempotent, manager_required
from .auth import MIN_PASSWORD, USERNAME_RE

bp = Blueprint("admin", __name__)


@bp.before_request
@manager_required
def _only_manager():
    pass


def _other_active_managers(conn, uid):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role='gerente' AND active=1 AND id<>?",
                        (uid,)).fetchone()[0]


# ----------------------------------------------------------------- usuários
@bp.route("/usuarios")
def usuarios():
    return render_template("usuarios.html",
                           users=get_db().execute("SELECT * FROM users ORDER BY active DESC, username").fetchall())


@bp.post("/usuarios/adicionar")
@idempotent
def adicionar_usuario():
    conn = get_db()
    f = request.form
    username, name, pw = f.get("username", "").strip(), f.get("display_name", "").strip(), f.get("password", "")
    role = f.get("role") if f.get("role") in ("gerente", "garcom") else "garcom"
    if not USERNAME_RE.match(username) or len(pw) < MIN_PASSWORD:
        flash(f"Usuário (3-30 letras/números) e senha (mínimo {MIN_PASSWORD} caracteres) são obrigatórios.", "erro")
    elif conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        flash("Esse nome de usuário já existe.", "erro")
    else:
        conn.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                     (username, name or username, generate_password_hash(pw), role, now()))
        audit(conn, "usuario_criado", f"{username} ({role})")
        flash("Usuário criado.", "sucesso")
    return redirect(url_for("admin.usuarios"))


@bp.post("/usuarios/<int:uid>/atualizar")
def atualizar_usuario(uid):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    role = request.form.get("role") if request.form.get("role") in ("gerente", "garcom") else u["role"]
    active = 1 if request.form.get("active") == "1" else 0
    if (role != "gerente" or not active) and u["role"] == "gerente" and u["active"] \
            and not _other_active_managers(conn, uid):
        flash("É preciso manter pelo menos um gerente ativo.", "erro")
        return redirect(url_for("admin.usuarios"))
    conn.execute("UPDATE users SET role=?, active=? WHERE id=?", (role, active, uid))
    new_pw = request.form.get("password", "")
    if new_pw:
        if len(new_pw) < MIN_PASSWORD:
            flash(f"A nova senha precisa de ao menos {MIN_PASSWORD} caracteres.", "erro")
            return redirect(url_for("admin.usuarios"))
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pw), uid))
    audit(conn, "usuario_atualizado", f"{u['username']}: perfil={role} ativo={active}"
          + (" senha redefinida" if new_pw else ""))
    flash("Usuário atualizado.", "sucesso")
    return redirect(url_for("admin.usuarios"))


# ------------------------------------------------------------ configurações
@bp.route("/config", methods=["GET", "POST"])
def config():
    conn = get_db()
    if request.method == "POST":
        try:
            pct = int(request.form.get("service_pct", "10"))
            if not 0 <= pct <= 30:
                raise ValueError
        except ValueError:
            flash("A taxa de serviço deve ficar entre 0 e 30%.", "erro")
            return redirect(url_for("admin.config"))
        set_setting(conn, "business_name", request.form.get("business_name", "").strip()[:60] or "ComandaFácil")
        set_setting(conn, "business_phone", request.form.get("business_phone", "").strip()[:30])
        set_setting(conn, "footer_message", request.form.get("footer_message", "").strip()[:120])
        set_setting(conn, "service_pct", pct)
        set_setting(conn, "service_auto_mesa", "1" if request.form.get("service_auto_mesa") else "0")
        audit(conn, "config_alterada", f"serviço {pct}%")
        flash("Configurações salvas.", "sucesso")
        return redirect(url_for("admin.config"))
    s = {k: get_setting(conn, k) for k in dbm.DEFAULT_SETTINGS}
    return render_template("config.html", s=s, backups=dbm.list_backups(), data_dir=str(dbm.DATA_DIR))


@bp.post("/backup/agora")
@idempotent
def backup_agora():
    dest = dbm.make_backup("manual")
    audit(get_db(), "backup_manual", dest.name)
    flash(f"Backup criado: {dest.name}", "sucesso")
    return redirect(url_for("admin.config"))


@bp.get("/backup/<name>")
def baixar_backup(name):
    if not re.fullmatch(r"comandas_[0-9_-]+_[a-z_]+\.db", name) or not (dbm.BACKUP_DIR / name).is_file():
        abort(404)
    audit(get_db(), "backup_baixado", name)
    return send_from_directory(dbm.BACKUP_DIR, name, as_attachment=True)


# ---------------------------------------------------------------- auditoria
@bp.route("/auditoria")
def auditoria():
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM audit_log", []
    if q:
        sql += " WHERE action LIKE ? OR details LIKE ? OR user_name LIKE ?"
        args = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY id DESC LIMIT 300", args).fetchall()
    return render_template("auditoria.html", rows=rows, q=q)
