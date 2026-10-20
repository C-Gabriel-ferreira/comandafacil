"""Gera o SQL para cadastrar uma loja na nuvem e o CÓDIGO DE ATIVAÇÃO para colar no PDV.

    python cloud/criar_loja.py minha-loja "Nome da Loja" https://SEU-PROJETO.supabase.co CHAVE_ANON

O token é aleatório e só aparece aqui (dentro do código de ativação). A nuvem guarda apenas o hash.
"""
import hashlib
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from comandafacil.online import make_activation_code  # noqa: E402


def main(argv):
    if len(argv) != 5:
        print(__doc__)
        return 2
    slug, name, url, key = argv[1:]
    token = secrets.token_urlsafe(24)
    digest = hashlib.sha256(token.encode()).hexdigest()
    esc = name.replace("'", "''")
    print("-- 1) Rode no SQL Editor do Supabase:")
    print(f"insert into public.cf_stores(slug, name, token_hash) values ('{slug}', '{esc}', '{digest}');\n")
    print("-- 2) Cole no PDV (Pedidos online > Configurar > Código de ativação):")
    print(make_activation_code(url, key, slug, token))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
