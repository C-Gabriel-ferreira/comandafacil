"""Relógio do estabelecimento. Servidores costumam estar em UTC; aqui tudo usa o fuso local
(padrão America/Sao_Paulo, configurável por COMANDAFACIL_TZ)."""
import os
from datetime import date, datetime

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(os.environ.get("COMANDAFACIL_TZ", "America/Sao_Paulo"))
except Exception:  # sem banco de fusos: usa o horário do computador
    _TZ = None


def now_dt() -> datetime:
    return datetime.now(_TZ).replace(tzinfo=None) if _TZ else datetime.now()


def today() -> date:
    return now_dt().date()


def minutes_since(iso) -> int:
    try:
        return max(0, int((now_dt() - datetime.fromisoformat(iso)).total_seconds() // 60))
    except (TypeError, ValueError):
        return 0
