# Como lançar uma atualização

1. Em `CHANGELOG.md`, escreva uma seção nova no topo: `## 2.4.0 — dd/mm/aaaa` com as novidades em linguagem simples.
2. Em `VERSION`, coloque o mesmo número (`2.4.0`).
3. Faça merge na `main`. O GitHub Actions roda os testes, gera o instalador, calcula o SHA-256 e publica a Release
   (o texto da Release é a seção do CHANGELOG). Se a seção da versão não existir, o lançamento falha de propósito.
4. Os PDVs instalados detectam a versão nova (ao abrir e a cada 6 horas), mostram as novidades e o gerente atualiza com um clique.

Regras: sempre aumentar o número; mudanças no banco entram como migração em `comandafacil/db.py` (rodam sozinhas, com backup antes).
