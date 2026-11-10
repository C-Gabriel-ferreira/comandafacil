"""Testes de segurança do servidor web: redirecionamento, limites de login, sessões, CSV, cabeçalhos."""
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from comandafacil import create_app, security
from comandafacil.views import auth

PW = "cardapio-77x"


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        auth._fails.clear()
        self.app = create_app(Path(self.tmp.name))
        self.c = self.app.test_client()
        self.token_page("/setup")
        self.c.post("/setup", data=dict(_csrf=self.tok, username="dono", password=PW, password2=PW))

    # ------------------------------------------------------------ ajudantes
    def token_page(self, path="/login", client=None):
        client = client or self.c
        self.tok = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)
        return self.tok

    def post(self, path, page="/", client=None, **data):
        client = client or self.c
        data.setdefault("_csrf", self.token_page(page, client))
        return client.post(path, data=data)

    def new_client(self, user, pw=PW, **extra):
        c = self.app.test_client()
        t = self.token_page("/login", c)
        r = c.post("/login", data=dict(_csrf=t, username=user, password=pw, **extra))
        return c, r

    def add_waiter(self, name="ana"):
        self.post("/usuarios/adicionar", "/usuarios", username=name, password=PW, role="garcom")

    # --------------------------------------------------- redirecionamento
    def test_next_only_accepts_internal_paths(self):
        for nxt in ("//evil.com", "/\\evil.com", "/\t/evil.com", "/%09/evil.com", "/%0a/evil.com",
                    "https://evil.com", "javascript:alert(1)", "/ /evil.com"):
            c = self.app.test_client()
            t = self.token_page("/login", c)
            r = c.post("/login?next=" + nxt, data=dict(_csrf=t, username="dono", password=PW))
            self.assertEqual(r.status_code, 302, nxt)
            self.assertEqual(r.headers["Location"], "/", nxt)

    def test_next_keeps_valid_internal_path(self):
        c = self.app.test_client()
        t = self.token_page("/login", c)
        r = c.post("/login?next=/historico?tipo=mesa", data=dict(_csrf=t, username="dono", password=PW))
        self.assertEqual(r.headers["Location"], "/historico?tipo=mesa")

    def test_safe_path_helper(self):
        self.assertEqual(security.safe_path("/ok?x=1", "d"), "/ok?x=1")
        for bad in ("", None, "//x", "/\\x", "http://x", "/a b", "/a\tb", "/a\nb", "x/y"):
            self.assertEqual(security.safe_path(bad, "d"), "d", repr(bad))

    # ------------------------------------------------------ força bruta
    def test_ip_wide_limit_stops_username_spraying(self):
        c = self.app.test_client()
        blocked = False
        for i in range(30):
            t = self.token_page("/login", c)
            r = c.post("/login", data=dict(_csrf=t, username=f"user{i}", password="x"))
            if b"Muitas tentativas" in r.data:
                blocked = True
                break
        self.assertTrue(blocked)
        self.assertLessEqual(i, 20)

    def test_account_limit_holds_across_ips_and_message_has_wait(self):
        for i in range(15):
            c = self.app.test_client()
            t = self.token_page("/login", c)
            c.post("/login", data=dict(_csrf=t, username="dono", password="errada"),
                   environ_overrides={"REMOTE_ADDR": f"10.0.0.{i}"})
        c = self.app.test_client()
        t = self.token_page("/login", c)
        r = c.post("/login", data=dict(_csrf=t, username="dono", password=PW),
                   environ_overrides={"REMOTE_ADDR": "10.9.9.9"})
        self.assertIn("Muitas tentativas", r.get_data(as_text=True))
        self.assertRegex(r.get_data(as_text=True), r"em \d+ minutos?")

    def test_unknown_user_still_hashes_password(self):
        with mock.patch.object(auth, "check_password_hash", return_value=False) as chk:
            self.new_client("ninguem")
        self.assertEqual(chk.call_count, 1)

    # --------------------------------------------------------- sessões
    def test_password_change_by_manager_revokes_other_sessions(self):
        self.add_waiter()
        waiter, r = self.new_client("ana")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(waiter.get("/").status_code, 200)
        self.post("/usuarios/2/atualizar", "/usuarios", role="garcom", active="1", password="outra-senha-88")
        self.assertEqual(waiter.get("/").status_code, 302)  # voltou para o login
        _, r = self.new_client("ana", "outra-senha-88")
        self.assertEqual(r.status_code, 302)

    def test_own_password_change_keeps_this_session_but_drops_others(self):
        other, _ = self.new_client("dono")
        r = self.post("/conta/senha", "/conta/senha", old=PW, new="nova-senha-55", new2="nova-senha-55")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(other.get("/").status_code, 302)

    def test_expired_session_is_dropped(self):
        with self.c.session_transaction() as s:
            s["until"] = int(time.time()) - 1
        self.assertEqual(self.c.get("/").status_code, 302)

    def test_remember_me_lasts_longer(self):
        short, _ = self.new_client("dono")
        long_, _ = self.new_client("dono", remember="1")
        with short.session_transaction() as a, long_.session_transaction() as b:
            self.assertLess(a["until"] - time.time(), security.SESSION_SHORT + 5)
            self.assertGreater(b["until"] - time.time(), security.SESSION_LONG - 5)

    # ------------------------------------------------------------ senhas
    def test_weak_passwords_are_rejected(self):
        for weak in ("12345678", "senha123", "aaaaaaaa", "dono"):
            r = self.post("/usuarios/adicionar", "/usuarios", username="novo", password=weak, role="garcom")
            self.assertEqual(r.status_code, 302)
            with self.app.app_context():
                from comandafacil import db as dbm
                self.assertIsNone(dbm.get_db().execute("SELECT 1 FROM users WHERE username='novo'").fetchone(), weak)
        self.assertIsNotNone(auth.password_problem("12345678"))
        self.assertIsNotNone(auth.password_problem("curta"))
        self.assertIsNotNone(auth.password_problem("Joao.Silva", "joao.silva"))
        self.assertIsNone(auth.password_problem("pastel de queijo"))

    # --------------------------------------------------------------- CSV
    def test_csv_export_neutralizes_formulas(self):
        r = self.post("/comanda/nova", "/", kind="balcao", customer_name='=HYPERLINK("http://x","clique")', _rid="r1")
        oid = r.headers["Location"].rsplit("/", 1)[1]
        self.post(f"/comanda/{oid}/adicionar", f"/comanda/{oid}", product_id="1", quantity="1", _rid="r2")
        self.post(f"/comanda/{oid}/fechar", f"/comanda/{oid}", pay_method="Dinheiro", pay_amount="1000", _rid="r3")
        text = self.c.get("/relatorios/vendas.csv?periodo=hoje").get_data(as_text=True)
        lines = text.splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertNotRegex(text, r'(^|;)"?=', "uma célula começa com =")
        self.assertIn("'=HYPERLINK", text)

    # ------------------------------------------------ cabeçalhos e limites
    def test_security_headers_and_no_inline_script(self):
        r = self.app.test_client().get("/login")
        csp = r.headers["Content-Security-Policy"]
        for part in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'", "object-src 'none'",
                     "form-action 'self'"):
            self.assertIn(part, csp)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn("camera=()", r.headers["Permissions-Policy"])
        html = r.get_data(as_text=True)
        self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "script embutido na página")
        self.assertNotIn("onclick=", html)

    def test_app_pages_have_no_inline_handlers(self):
        for path in ("/", "/produtos/compras", "/mesas", "/produtos", "/usuarios", "/config", "/relatorios",
                     "/cozinha", "/historico", "/auditoria"):
            html = self.c.get(path).get_data(as_text=True)
            self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), path)
            self.assertNotRegex(html, r"\son(click|change|submit|load)=", path)

    def test_huge_body_is_refused(self):
        t = self.token_page("/")
        r = self.c.post("/comanda/nova", data=dict(_csrf=t, kind="balcao", customer_name="x" * (3 * 1024 * 1024)))
        self.assertEqual(r.status_code, 413)

    def test_referrer_redirect_stays_on_site(self):
        self.post("/comanda/nova", "/", kind="balcao", _rid="dup1")
        r = self.c.post("/comanda/nova", data=dict(_csrf=self.tok, kind="balcao", _rid="dup1"),
                        headers={"Referer": "https://evil.com/x"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/")

    # ------------------------------------------- link de e-mail confiável
    def test_email_reset_disabled_online_without_public_url(self):
        with mock.patch.object(auth.mailer, "is_configured", return_value=True), \
                mock.patch.object(security, "HTTPS", True), \
                mock.patch.dict("os.environ", {"COMANDAFACIL_PUBLIC_URL": ""}):
            self.assertNotIn("Enviar link de recuperação", self.app.test_client().get("/login/esqueci").get_data(as_text=True))
        with mock.patch.object(auth.mailer, "is_configured", return_value=True), \
                mock.patch.object(security, "HTTPS", True), \
                mock.patch.dict("os.environ", {"COMANDAFACIL_PUBLIC_URL": "https://comandas.exemplo.com"}):
            self.assertIn("Enviar link de recuperação", self.app.test_client().get("/login/esqueci").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
