"""Dinheiro sempre em CENTAVOS (inteiros). Nunca use float para valores."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MAX_CENTS = 100_000_000  # R$ 1.000.000,00 por valor


def parse_money(text) -> int:
    """'12,50' / '1.234,56' / '12.5' / 'R$ 3' -> centavos. Levanta ValueError se inválido."""
    s = str(text or "").strip().replace("R$", "").replace(" ", "")
    if not s:
        raise ValueError("valor vazio")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError("valor inválido")
    if not d.is_finite() or d < 0:
        raise ValueError("valor inválido")
    cents = int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if cents > MAX_CENTS:
        raise ValueError("valor muito alto")
    return cents


def format_brl(cents) -> str:
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    reais, c = divmod(abs(cents), 100)
    return f"{sign}R$ {reais:,}".replace(",", ".") + f",{c:02d}"


def cents_to_input(cents) -> str:
    reais, c = divmod(int(cents or 0), 100)
    return f"{reais},{c:02d}"


def split_evenly(total_cents: int, people: int) -> list[int]:
    """Divide sem perder centavos: os primeiros recebem 1 centavo a mais."""
    people = max(1, int(people))
    base, rest = divmod(int(total_cents), people)
    return [base + (1 if i < rest else 0) for i in range(people)]
