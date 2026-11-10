"""Envio de e-mail por SMTP (usado na recuperação de senha).

Configuração (variáveis de ambiente ou arquivo .env na pasta de dados):
  COMANDAFACIL_SMTP_HOST      servidor SMTP (ex.: smtp.gmail.com). Sem ele, o envio fica desativado.
  COMANDAFACIL_SMTP_PORT      porta (587 = STARTTLS, 465 = SSL). Padrão 587
  COMANDAFACIL_SMTP_USER      usuário de login (normalmente o próprio e-mail)
  COMANDAFACIL_SMTP_PASSWORD  senha ou "senha de app"
  COMANDAFACIL_SMTP_FROM      remetente (padrão: o usuário)
  COMANDAFACIL_SMTP_SECURITY  starttls (padrão) | ssl | none
"""
import os
import smtplib
import ssl
import sys
import threading
from email.message import EmailMessage
from email.utils import formataddr


def _cfg():
    user = os.environ.get("COMANDAFACIL_SMTP_USER", "").strip()
    return {
        "host": os.environ.get("COMANDAFACIL_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("COMANDAFACIL_SMTP_PORT") or 587),
        "user": user,
        "password": os.environ.get("COMANDAFACIL_SMTP_PASSWORD", ""),
        "sender": os.environ.get("COMANDAFACIL_SMTP_FROM", "").strip() or user,
        "security": os.environ.get("COMANDAFACIL_SMTP_SECURITY", "starttls").strip().lower(),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["sender"])


def send(to: str, subject: str, body: str, sender_name: str = "ComandaFácil") -> None:
    """Envia um e-mail de texto simples. Levanta exceção se falhar."""
    c = _cfg()
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, c["sender"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if c["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(c["host"], c["port"], timeout=15, context=ctx)
    else:
        smtp = smtplib.SMTP(c["host"], c["port"], timeout=15)
    with smtp:
        if c["security"] == "starttls":
            smtp.starttls(context=ctx)
        if c["user"]:
            smtp.login(c["user"], c["password"])
        smtp.send_message(msg)


def send_async(to: str, subject: str, body: str, sender_name: str = "ComandaFácil") -> None:
    """Envia em segundo plano: a resposta da página não depende do SMTP (nem revela se o e-mail existe)."""
    def run():
        try:
            send(to, subject, body, sender_name)
        except Exception as exc:  # nunca derrubar o sistema por falha de e-mail
            print("Falha ao enviar e-mail:", exc, file=sys.stderr)
    threading.Thread(target=run, daemon=True, name="mail").start()
