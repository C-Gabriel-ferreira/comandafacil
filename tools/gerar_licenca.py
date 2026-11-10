"""Ferramenta DO VENDEDOR para emitir licenças do ComandaFácil. Não distribua a chave privada.

  python tools/gerar_licenca.py init
      Cria a chave privada (chave_privada_licenca.pem) e grava a chave pública em comandafacil/license_key.py.
      Faça UMA vez, guarde a chave privada em local seguro (com cópia) e gere o sistema dos clientes depois disso.

  python tools/gerar_licenca.py emitir --cliente "Restaurante do Zé" --email dono@gmail.com --usuarios 5 --dias 365
      Imprime a licença para enviar ao cliente (ele cola no primeiro acesso ou em Usuários > Licença).

  python tools/gerar_licenca.py reset --email dono@gmail.com --horas 24
      Código para o cliente que esqueceu a senha (tela "Esqueci minha senha" > "Tenho um código").
      Vale uma vez só, até o prazo, e só para o usuário com esse e-mail.
"""
import argparse
import base64
import json
import os
import secrets
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "chave_privada_licenca.pem"
PUB_PY = ROOT / "comandafacil" / "license_key.py"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def public_b64(private_key) -> str:
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def sign_license(private_key, cliente, email, usuarios, expira: date) -> str:
    payload = json.dumps({"v": 1, "cliente": cliente, "email": email.strip().lower(), "usuarios": int(usuarios),
                          "expira": expira.isoformat(), "emitida": date.today().isoformat()},
                         separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"CF1.{b64(payload)}.{b64(private_key.sign(payload))}"


def sign_reset(private_key, email, hours: float) -> str:
    payload = json.dumps({"v": 1, "t": "reset", "email": email.strip().lower(),
                          "exp": int(time.time() + hours * 3600), "n": secrets.token_hex(6)},
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"CR1.{b64(payload)}.{b64(private_key.sign(payload))}"


def load_private(path: Path):
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def cmd_init(_):
    if KEY_FILE.exists():
        sys.exit(f"Já existe {KEY_FILE.name}. Apagá-la invalida todas as licenças já emitidas; nada foi alterado.")
    key = Ed25519PrivateKey.generate()
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    PUB_PY.write_text(
        '"""Chave PÚBLICA usada para conferir as licenças (não é segredo).\n'
        'É preenchida por:  python tools/gerar_licenca.py init\n'
        'A chave privada fica só com o vendedor (arquivo chave_privada_licenca.pem) e nunca vai para o sistema do cliente."""\n'
        f'PUBLIC_KEY_B64 = "{public_b64(key)}"\n', encoding="utf-8")
    print(f"Chave privada criada: {KEY_FILE}\n  -> GUARDE com cópia de segurança e NUNCA envie ao cliente.")
    print(f"Chave pública gravada em {PUB_PY.relative_to(ROOT)}")


def cmd_emitir(a):
    key_path = Path(a.chave) if a.chave else KEY_FILE
    if not key_path.exists():
        sys.exit(f"Chave privada não encontrada ({key_path}). Rode 'init' primeiro.")
    expira = date.today() + timedelta(days=a.dias)
    print(sign_license(load_private(key_path), a.cliente, a.email, a.usuarios, expira))
    print(f"\n# {a.cliente} | responsável {a.email} | {a.usuarios} usuários | vale até {expira:%d/%m/%Y}", file=sys.stderr)


def cmd_reset(a):
    key_path = Path(a.chave) if a.chave else KEY_FILE
    if not key_path.exists():
        sys.exit(f"Chave privada não encontrada ({key_path}). Rode 'init' primeiro.")
    print(sign_reset(load_private(key_path), a.email, a.horas))
    print(f"\n# código de redefinição para {a.email}, vale por {a.horas:g} h e só pode ser usado uma vez", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    e = sub.add_parser("emitir")
    e.add_argument("--cliente", required=True)
    e.add_argument("--email", required=True, help="e-mail (Gmail) do responsável/gerente principal")
    e.add_argument("--usuarios", type=int, required=True, help="usuários ativos permitidos (o gerente conta)")
    e.add_argument("--dias", type=int, default=365, help="validade a partir de hoje (padrão 365)")
    e.add_argument("--chave", help="caminho da chave privada (padrão: chave_privada_licenca.pem)")
    e.set_defaults(fn=cmd_emitir)
    r = sub.add_parser("reset")
    r.add_argument("--email", required=True, help="e-mail do usuário que vai redefinir a senha")
    r.add_argument("--horas", type=float, default=24, help="validade em horas (padrão 24)")
    r.add_argument("--chave", help="caminho da chave privada (padrão: chave_privada_licenca.pem)")
    r.set_defaults(fn=cmd_reset)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
