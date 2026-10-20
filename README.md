# FC Gaia Basquetebol · Calendário

Site com os jogos, resultados e classificações de todas as equipas do FC Gaia.
Os dados ficam em `data/jogos.json` e são atualizados todos os dias por um script automático.
O site é publicado no GitHub Pages (grátis, sem limite de publicações).

## Configurar (uma vez)
1. Carregar todos os ficheiros deste pacote para um repositório **público** (incluindo a pasta `.github`).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Actions → Atualizar jogos e publicar site → Run workflow**.
4. O endereço do site aparece em **Settings → Pages** (ex.: `https://<utilizador>.github.io/fcgaia-basquetebol/`).

## Como funciona
Todos os dias às 00:05 (UTC) o GitHub corre `scripts/atualizar.py`, que lê o Centro de Resultados da FPB
e o calendário do clube no fpb.pt, grava as alterações em `data/jogos.json` e publica o site.

## Atualizar à mão
**Actions → Atualizar jogos e publicar site → Run workflow**.

## Nova época ou novas competições
Editar `scripts/config.json`: os números das competições (`id`) estão no endereço do Centro de Resultados
(`getCompetitionDetails?competitionId=...`). As fases novas dentro de cada competição são encontradas sozinhas.

## Se a atualização falhar
O GitHub envia um email. O site continua no ar com os últimos dados. O separador **Actions** mostra o erro.
