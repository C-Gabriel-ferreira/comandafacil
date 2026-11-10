"""Licença do cliente: limite de usuários ativos e validade, conferidos offline.

A licença é um texto assinado (Ed25519) emitido pelo vendedor com tools/gerar_licenca.py:
    CF1.<dados em base64url>.<assinatura em base64url>
Os dados dizem: cliente, e-mail do responsável, nº de usuários e data de validade.
O sistema só tem a chave pública, então não consegue (nem o cliente consegue) fabricar licenças.

Regras:
  - sem licença (ou licença inválida): modo de avaliação com EVAL_MAX_USERS usuários ativos;
  - licença válida: até `usuarios` usuários ativos (o gerente conta);
  - licença vencida: ninguém é bloqueado no meio do expediente, mas não dá para criar nem reativar usuários.
"""
import base64
import json
import os
import time
from datetime import date

from . import db as dbm
from .clock import today
from .license_key import PUBLIC_KEY_B64

EVAL_MAX_USERS = 3
PREFIX = "CF1"
RESET_PREFIX = "CR1"


class LicenseError(ValueError):
    """Licença recusada; a mensagem pode ser mostrada ao usuário."""


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _public_key():
    raw = (os.environ.get("COMANDAFACIL_LICENSE_PUBKEY") or PUBLIC_KEY_B64).strip()
    if not raw:
        raise LicenseError("Este sistema ainda não tem a chave de licenças configurada. Fale com o fornecedor.")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        return Ed25519PublicKey.from_public_bytes(_b64d(raw))
    except ImportError as exc:
        raise LicenseError("Falta instalar a biblioteca 'cryptography' (pip install cryptography).") from exc
    except ValueError as exc:
        raise LicenseError("A chave de licenças deste sistema está inválida.") from exc


def _open(token: str, prefix: str, what: str) -> bytes:
    """Confere o formato e a assinatura; devolve os dados (bytes). LicenseError se algo não bater."""
    token = "".join((token or "").split())
    try:
        got, payload_b64, sig_b64 = token.split(".")
        if got != prefix:
            raise ValueError
        payload, sig = _b64d(payload_b64), _b64d(sig_b64)
    except ValueError:
        raise LicenseError(f"{what} inválido: confira se copiou o texto inteiro.") from None
    try:
        _public_key().verify(sig, payload)
    except LicenseError:
        raise
    except Exception:
        raise LicenseError(f"{what} inválido: a assinatura não confere.") from None
    return payload


def parse(token: str) -> dict:
    """Confere a assinatura e devolve os dados. Levanta LicenseError se não for uma licença válida."""
    payload = _open(token, PREFIX, "Licença")
    try:
        data = json.loads(payload)
        info = {
            "customer": str(data["cliente"])[:80],
            "owner_email": str(data.get("email") or "").strip().lower(),
            "max_users": int(data["usuarios"]),
            "expires": date.fromisoformat(data["expira"]),
        }
        if info["max_users"] < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise LicenseError("Licença inválida: dados incompletos.") from None
    return info


def parse_reset_code(code: str) -> dict:
    """Código de redefinição de senha emitido pelo suporte: {email, exp}. LicenseError se inválido ou vencido."""
    payload = _open(code, RESET_PREFIX, "Código")
    try:
        data = json.loads(payload)
        info = {"email": str(data["email"]).strip().lower(), "exp": int(data["exp"])}
        if data.get("t") != "reset" or not info["email"]:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise LicenseError("Código inválido: dados incompletos.") from None
    if time.time() > info["exp"]:
        raise LicenseError("Esse código venceu. Peça um novo ao suporte.")
    return info


def active_users(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]


def status(conn) -> dict:
    """Situação atual: state = none | ok | expired | invalid."""
    st = {"state": "none", "customer": "", "owner_email": "", "expires": None, "days_left": None,
          "max_users": EVAL_MAX_USERS, "active": active_users(conn), "message": ""}
    token = dbm.get_setting(conn, "license_key", "")
    if token:
        try:
            info = parse(token)
        except LicenseError as exc:
            st.update(state="invalid", message=str(exc))
        else:
            st.update(info, days_left=(info["expires"] - today()).days)
            st["state"] = "ok" if st["days_left"] >= 0 else "expired"
    st["can_add"] = st["state"] != "expired" and st["active"] < st["max_users"]
    return st


def check_can_add_user(conn):
    """None se pode criar/ativar mais um usuário; senão, a mensagem do motivo."""
    st = status(conn)
    if st["state"] == "expired":
        return (f"A licença venceu em {st['expires']:%d/%m/%Y}. Renove para criar ou reativar usuários "
                "(os usuários atuais continuam funcionando).")
    if st["active"] >= st["max_users"]:
        if st["state"] == "ok":
            return (f"Sua licença permite {st['max_users']} usuários ativos e todos já estão em uso. "
                    "Desative um usuário ou contrate mais vagas.")
        return (f"No modo de avaliação o limite é de {EVAL_MAX_USERS} usuários ativos. "
                "Ative uma licença para liberar mais.")
    return None


def validate(token: str, manager_email: str = "") -> dict:
    """Confere a licença sem gravar. Se ela nomeia um responsável, só o gerente com esse e-mail pode ativá-la."""
    info = parse(token)
    if info["owner_email"] and (manager_email or "").strip().lower() != info["owner_email"]:
        raise LicenseError(f"Esta licença é do e-mail {info['owner_email']}. Cadastre esse e-mail no seu usuário "
                           "(em Usuários) e tente de novo.")
    if info["expires"] < today():
        raise LicenseError(f"Esta licença venceu em {info['expires']:%d/%m/%Y}.")
    return info


def save(conn, token: str, manager_email: str = "") -> dict:
    info = validate(token, manager_email)
    dbm.set_setting(conn, "license_key", "".join((token or "").split()))
    return info
