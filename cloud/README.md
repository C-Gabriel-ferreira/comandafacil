# Nuvem dos pedidos online

1. Rode `001_pedidos_online.sql` no SQL Editor do Supabase (só cria tabelas/funções `cf_*`; não mexe em nada existente).
2. `python cloud/criar_loja.py minha-loja "Minha Loja" https://PROJETO.supabase.co CHAVE_ANON` imprime o SQL da loja e o código de ativação.
3. No PDV: Pedidos online → Configurar → colar o código → Publicar cardápio → Receber pedidos.
4. Link para os clientes: `https://SEU-SITE/cardapio/?loja=minha-loja` (ajuste `site/cardapio/config.js` se mudar de projeto).

Segurança: as tabelas têm RLS ligado e nenhuma policy; clientes e PDV só chamam as funções. O PDV prova quem é com um token
(guardado só como hash). Preços e totais são recalculados no servidor a partir do cardápio publicado.
