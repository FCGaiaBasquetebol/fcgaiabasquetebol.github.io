#!/usr/bin/env python3
"""Conta os atletas inscritos na FPB por clube, escalão, género e ano de nascimento.

Não mexe no site: escreve só contagens (sem nomes) em dados-atletas/.
Fonte: a pesquisa de atletas do fpb.pt (/atletas/clube_ID/), que usa
admin-ajax.php com action=search_player e os filtros Época, Escalão e Género.

Uso:  python scripts/atletas_inscritos.py [--clubes 119,120] [--sem-idades]
"""
import argparse, csv, json, re, time, unicodedata, pathlib, datetime, collections
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "dados-atletas"
FPB = "https://www.fpb.pt"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
EPOCA = "2026/2027"
GENEROS = ["masculino", "feminino"]
# usada só se a página não mostrar a lista de escalões
ESCALOES_RESERVA = ["Baby-Basket", "Mini 8", "Mini 10", "Mini 12", "Minis 12 Mistos", "Sub 14", "Sub 16",
                    "Sub 18", "Sub 20", "Esperanças", "Sénior", "Masters / Veteranos"]


def log(m):
    print(m, flush=True)


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "").replace("\xa0", " ")).strip()


def plain(s):
    return unicodedata.normalize("NFD", norm(s)).encode("ascii", "ignore").decode().lower()


def redact(html, n=1500):
    """Estrutura da resposta sem nomes (para o log público)."""
    s = re.sub(r">([^<]*[A-Za-zÀ-ÿ][^<]*)<", lambda m: ">" + re.sub(r"[A-Za-zÀ-ÿ]", "x", m.group(1)) + "<", html)
    return s[:n]


def escaloes_do_site(clube):
    try:
        r = requests.get(f"{FPB}/atletas/clube_{clube}/", headers=UA, timeout=30)
        s = BeautifulSoup(r.text, "html.parser")
        for sel in s.find_all("select"):
            ident = plain(" ".join([sel.get("name", ""), sel.get("id", "")]))
            opts = [norm(o.get("value") or o.get_text()) for o in sel.find_all("option")]
            log(f"   select '{ident}': {opts[:40]}")
            if "escal" in ident:
                vals = [o for o in opts if o and "todos" not in plain(o) and "selecion" not in plain(o)]
                if vals:
                    return vals
    except Exception as e:  # noqa: BLE001
        log(f"AVISO: não li a página de atletas: {e}")
    log("AVISO: lista de escalões não encontrada na página; uso a lista de reserva.")
    return ESCALOES_RESERVA


def pesquisa(clube, escalao, genero, mostrar=False):
    data = {"action": "search_player", "epoca": EPOCA, "escalao": escalao, "genero": genero,
            "nome": "", "idAssociacao": "", "idClube": str(clube)}
    for i in range(3):
        try:
            r = requests.post(f"{FPB}/wp-admin/admin-ajax.php", data=data, headers=UA, timeout=40)
            r.raise_for_status()
            break
        except Exception as e:  # noqa: BLE001
            if i == 2:
                raise
            log(f"   nova tentativa ({e})")
            time.sleep(4 + 4 * i)
    try:
        html = r.json()
    except ValueError:
        html = r.text
    html = html if isinstance(html, str) else json.dumps(html, ensure_ascii=False)
    if mostrar:
        log("   ---- exemplo de resposta (nomes ocultados) ----")
        log(redact(html))
        log("   ----")
    ids = set(int(x) for x in re.findall(r"/atletas/(\d+)", html.replace("\\/", "/")))
    if re.search(r"pagina|paged|page-numbers|carregar mais|ver mais", plain(html)):
        log("   AVISO: a resposta parece ter paginação; o total pode estar incompleto.")
    return ids


def ano_nascimento(aid):
    try:
        r = requests.get(f"{FPB}/atletas/{aid}/", headers=UA, timeout=30)
        lines = [norm(x) for x in BeautifulSoup(r.text, "html.parser").get_text("\n").split("\n") if norm(x)]
        for i, l in enumerate(lines):
            if plain(l).rstrip(":") == "data de nascimento" and i + 1 < len(lines):
                m = re.search(r"(19|20)\d{2}", lines[i + 1])
                return int(m.group(0)) if m else None
    except Exception as e:  # noqa: BLE001
        log(f"   AVISO: atleta {aid}: {e}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clubes", default="119")
    ap.add_argument("--sem-idades", action="store_true")
    a = ap.parse_args()
    clubes = [int(c) for c in a.clubes.split(",") if c.strip()]
    OUT.mkdir(exist_ok=True)
    linhas, anos_linhas, md = [], [], []
    hoje = datetime.date.today().isoformat()

    for clube in clubes:
        log(f"== Clube {clube} · época {EPOCA}")
        escaloes = escaloes_do_site(clube)
        log(f"Escalões: {escaloes}")
        por = {}            # (escalão, género) -> ids
        vistos = {}         # frozenset -> chave (deteta filtro ignorado)
        primeiro = True
        for g in GENEROS:
            total_g = pesquisa(clube, "", g)
            log(f"{g}: {len(total_g)} atletas (sem filtro de escalão)")
            for e in escaloes:
                ids = pesquisa(clube, e, g, mostrar=primeiro)
                if ids:
                    primeiro = False
                k = frozenset(ids)
                if ids and k in vistos and k != frozenset():
                    log(f"AVISO: {e}/{g} devolveu a mesma lista que {vistos[k]} (filtro pode ter sido ignorado).")
                vistos.setdefault(k, f"{e}/{g}")
                por[(e, g)] = ids
                log(f"   {e:22s} {g:10s} {len(ids):4d}")
                time.sleep(0.4)
            soma = set().union(*[v for (e, gg), v in por.items() if gg == g])
            fora = total_g - soma
            if fora:
                por[("Sem escalão no filtro", g)] = fora
                log(f"   {len(fora)} atletas {g} não apareceram em nenhum escalão")

        anos = {}
        if not a.sem_idades:
            todos = set().union(*por.values())
            log(f"A ler o ano de nascimento de {len(todos)} atletas...")
            for n, aid in enumerate(sorted(todos), 1):
                anos[aid] = ano_nascimento(aid)
                if n % 25 == 0:
                    log(f"   {n}/{len(todos)}")
                time.sleep(0.3)

        md.append(f"## Clube {clube} · época {EPOCA} · {hoje}\n")
        md.append("| Escalão | Masculino | Feminino | Total |\n|---|---:|---:|---:|")
        tm = tf = 0
        for e in escaloes + ["Sem escalão no filtro"]:
            m, f = len(por.get((e, "masculino"), ())), len(por.get((e, "feminino"), ()))
            if m or f:
                md.append(f"| {e} | {m} | {f} | {m + f} |")
                linhas.append([hoje, clube, e, m, f, m + f])
                tm, tf = tm + m, tf + f
        md.append(f"| **Total** | **{tm}** | **{tf}** | **{tm + tf}** |\n")

        if anos:
            cont = collections.Counter()
            for (e, g), ids in por.items():
                for aid in ids:
                    cont[(anos.get(aid) or 0, g, e)] += 1
            md.append("| Ano nasc. | Género | Escalão | Atletas |\n|---|---|---|---:|")
            for (ano, g, e), n in sorted(cont.items()):
                md.append(f"| {ano or '?'} | {g} | {e} | {n} |")
                anos_linhas.append([hoje, clube, ano or "", g, e, n])
            md.append("")

    with open(OUT / "por-escalao.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["data", "clube", "escalao", "masculino", "feminino", "total"]] + linhas)
    if anos_linhas:
        with open(OUT / "por-ano.csv", "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([["data", "clube", "ano_nascimento", "genero", "escalao", "atletas"]] + anos_linhas)
    (OUT / "resumo.md").write_text("# Atletas inscritos na FPB\n\n" + "\n".join(md) + "\n", encoding="utf-8")
    log("\n".join(md))


if __name__ == "__main__":
    main()
