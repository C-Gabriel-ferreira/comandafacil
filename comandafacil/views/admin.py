"""Usuários, configurações, backups e auditoria (somente gerente)."""
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_from_directory, url_for
from werkzeug.security import generate_password_hash

from .. import db as dbm
from .. import license as lic
from ..db import get_db, get_setting, now, set_setting
from ..security import audit, idempotent, manager_required
from .auth import MIN_PASSWORD, USERNAME_RE, normalize_email

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
    conn = get_db()
    return render_template("usuarios.html", lic=lic.status(conn),
                           users=conn.execute("SELECT * FROM users ORDER BY active DESC, username").fetchall())


@bp.post("/usuarios/adicionar")
@idempotent
def adicionar_usuario():
    conn = get_db()
    f = request.form
    username, name, pw = f.get("username", "").strip(), f.get("display_name", "").strip(), f.get("password", "")
    role = f.get("role") if f.get("role") in ("gerente", "garcom") else "garcom"
    try:
        email = normalize_email(f.get("email"))
    except ValueError as exc:
        flash(str(exc), "erro")
        return redirect(url_for("admin.usuarios"))
    if not USERNAME_RE.match(username) or len(pw) < MIN_PASSWORD:
        flash(f"Usuário (3-30 letras/números) e senha (mínimo {MIN_PASSWORD} caracteres) são obrigatórios.", "erro")
    elif conn.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone():
        flash("Esse nome de usuário já existe.", "erro")
    elif email and conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?)", (email,)).fetchone():
        flash("Esse e-mail já está em uso por outro usuário.", "erro")
    elif (limit_msg := lic.check_can_add_user(conn)):
        flash(limit_msg, "erro")
    else:
        conn.execute("INSERT INTO users(username,display_name,password_hash,role,created_at,email) VALUES (?,?,?,?,?,?)",
                     (username, name or username, generate_password_hash(pw), role, now(), email))
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
    try:
        email = normalize_email(request.form.get("email"))
    except ValueError as exc:
        flash(str(exc), "erro")
        return redirect(url_for("admin.usuarios"))
    if email and conn.execute("SELECT 1 FROM users WHERE lower(email)=lower(?) AND id<>?", (email, uid)).fetchone():
        flash("Esse e-mail já está em uso por outro usuário.", "erro")
        return redirect(url_for("admin.usuarios"))
    if active and not u["active"] and (limit_msg := lic.check_can_add_user(conn)):
        flash(limit_msg, "erro")  # reativar um usuário também ocupa uma vaga da licença
        return redirect(url_for("admin.usuarios"))
    conn.execute("UPDATE users SET role=?, active=? WHERE id=?", (role, active, uid))
    if (email or "") != (u["email"] or "").lower():
        # e-mail novo: o vínculo antigo com o Google deixa de valer
        conn.execute("UPDATE users SET email=?, google_sub=NULL WHERE id=?", (email, uid))
    new_pw = request.form.get("password", "")
    if new_pw:
        if len(new_pw) < MIN_PASSWORD:
            flash(f"A nova senha precisa de ao menos {MIN_PASSWORD} caracteres.", "erro")
            return redirect(url_for("admin.usuarios"))
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_pw), uid))
    audit(conn, "usuario_atualizado", f"{u['username']}: perfil={role} ativo={active}"
          + (" senha redefinida" if new_pw else "") + (" e-mail alterado" if (email or "") != (u["email"] or "").lower() else ""))
    flash("Usuário atualizado.", "sucesso")
    return redirect(url_for("admin.usuarios"))


@bp.post("/usuarios/licenca")
def ativar_licenca():
    conn = get_db()
    try:
        info = lic.save(conn, request.form.get("license", ""), g.user["email"] or "")
    except lic.LicenseError as exc:
        flash(str(exc), "erro")
    else:
        audit(conn, "licenca_ativada", f"{info['customer']}: {info['max_users']} usuários até {info['expires']:%d/%m/%Y}")
        flash(f"Licença ativada: {info['max_users']} usuários até {info['expires']:%d/%m/%Y}.", "sucesso")
        if lic.active_users(conn) > info["max_users"]:
            flash("Há mais usuários ativos do que a licença permite: desative alguns para poder criar novos.", "info")
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
    if dbm.USING_PG:
        flash("Os backups deste ambiente são automáticos, feitos pelo Supabase.", "erro")
        return redirect(url_for("admin.config"))
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
        sql += " WHERE lower(action) LIKE lower(?) OR lower(details) LIKE lower(?) OR lower(user_name) LIKE lower(?)"
        args = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY id DESC LIMIT 300", args).fetchall()
    return render_template("auditoria.html", rows=rows, q=q)
