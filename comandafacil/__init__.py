"""ComandaFácil v2 — fábrica da aplicação Flask."""
import os
from datetime import datetime

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db as dbm
from .clock import minutes_since
from .money import cents_to_input, format_brl
from .security import csrf_token, init_security, is_manager, new_rid
from .services import KINDS, PAYMENT_METHODS

STATUS_LABELS = {"aberta": "Aberta", "fechada": "Fechada", "cancelada": "Cancelada", "juntada": "Juntada"}


def create_app(data_dir=None):
    if data_dir:
        dbm.configure(data_dir)
    dbm.init_db()
    app = Flask(__name__, template_folder=str(dbm.BASE_DIR / "templates"),
                static_folder=str(dbm.BASE_DIR / "static"))
    if os.environ.get("COMANDAFACIL_BEHIND_PROXY") == "1":
        # atrás de Caddy/Nginx: usa o IP/protocolo reais (necessário para o bloqueio de login por IP)
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    init_security(app)
    app.teardown_appcontext(dbm.close_db)

    app.add_template_filter(format_brl, "brl")
    app.add_template_filter(cents_to_input, "cents_input")

    @app.template_filter("dt")
    def _dt(value, fmt="%d/%m/%Y %H:%M"):
        try:
            return datetime.fromisoformat(value).strftime(fmt)
        except (TypeError, ValueError):
            return value or ""

    def order_title(o):
        keys = o.keys()
        if o["kind"] == "mesa":
            return (o["table_name"] if "table_name" in keys and o["table_name"] else f"Comanda #{o['id']}")
        name = o["customer_name"] or f"#{o['id']}"
        return f"{KINDS[o['kind']]}: {name}"

    app.add_template_global(csrf_token, "csrf_token")
    app.add_template_global(new_rid, "new_rid")
    app.add_template_global(is_manager, "is_manager")
    app.add_template_global(order_title, "order_title")
    app.add_template_global(minutes_since, "minutes_since")
    app.jinja_env.globals.update(KINDS=KINDS, STATUS_LABELS=STATUS_LABELS, PAYMENT_METHODS=PAYMENT_METHODS)

    @app.context_processor
    def _ctx():
        from flask import g
        biz = "ComandaFácil"
        try:
            biz = dbm.get_setting(dbm.get_db(), "business_name", biz)
        except Exception:
            pass
        return {"business_name": biz, "current_user": getattr(g, "user", None)}

    from .views import admin, auth, catalog, main, reports
    for mod in (auth, main, catalog, reports, admin):
        app.register_blueprint(mod.bp)

    @app.get("/healthz")
    def healthz():
        dbm.get_db().execute("SELECT 1")
        return {"status": "ok"}

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def _error(e):
        return render_template("erro.html", error=e), e.code

    return app
