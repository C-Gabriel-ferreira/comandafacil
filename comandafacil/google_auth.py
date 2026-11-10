"""Login com Google (OAuth 2.0 — fluxo "código de autorização" com PKCE), só com a biblioteca padrão.

Configuração (variáveis de ambiente ou arquivo .env na pasta de dados):
  COMANDAFACIL_GOOGLE_CLIENT_ID      ID do cliente OAuth (Google Cloud Console > Credenciais)
  COMANDAFACIL_GOOGLE_CLIENT_SECRET  segredo do cliente OAuth
O botão "Entrar com Google" só aparece quando as duas estão preenchidas.
O Google NÃO cria contas aqui: só entra quem já tem usuário com o mesmo e-mail cadastrado.
"""
import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleError(Exception):
    """Falha no login com Google; a mensagem pode ser mostrada ao usuário."""


def client_id() -> str:
    return os.environ.get("COMANDAFACIL_GOOGLE_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("COMANDAFACIL_GOOGLE_CLIENT_SECRET", "").strip()


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def new_pkce() -> tuple[str, str]:
    """(verifier, challenge) do PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })


def _request(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise GoogleError("Não foi possível falar com o Google agora. Tente de novo em instantes.") from exc


def fetch_profile(code: str, redirect_uri: str, verifier: str) -> dict:
    """Troca o código por um token e devolve {sub, email, email_verified, name}.

    O token vem direto do Google por HTTPS (não de um navegador), então o perfil é confiável."""
    body = urllib.parse.urlencode({
        "code": code, "client_id": client_id(), "client_secret": client_secret(),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    token = _request(urllib.request.Request(TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded"}))
    access = token.get("access_token")
    if not access:
        raise GoogleError("O Google recusou o login. Tente de novo.")
    profile = _request(urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access}"}))
    if not profile.get("sub"):
        raise GoogleError("O Google não informou a conta. Tente de novo.")
    return profile
