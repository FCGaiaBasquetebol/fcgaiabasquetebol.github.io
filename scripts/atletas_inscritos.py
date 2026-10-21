#!/usr/bin/env python3
"""Conta os atletas inscritos na FPB por clube, escalão, género e ano de nascimento.

Não mexe no site: escreve só contagens (sem nomes) em dados-atletas/.
Fonte: a pesquisa de atletas do fpb.pt (/atletas/clube_ID/), que usa
admin-ajax.php com action=search_player e os filtros Época, Escalão e Género.

Uso:  python scripts/atletas_inscritos.py [--clubes 119,120] [--sem-idades]
"""
import argparse, concurrent.futures, csv, json, re, time, unicodedata, pathlib, datetime, collections
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
            r = requests.post(f"{FPB}/wp-admin/admin-ajax.php", data=data, headers=UA, timeout=180)
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
    ids = linhas_atletas(html)
    if re.search(r"pagina|paged|page-numbers|carregar mais|ver mais", plain(html)):
        log("   AVISO: a resposta parece ter paginação; o total pode estar incompleto.")
    return ids


def linhas_atletas(html):
    """{id_atleta: nome do clube} a partir das linhas 'team-row' da resposta."""
    html = html.replace("\\/", "/")
    out = {}
    blocos = re.split(r'class="team-row', html)
    for b in blocos[1:] if len(blocos) > 1 else [html]:
        m = re.search(r"/atletas/(\d+)", b)
        if not m:
            continue
        clube = ""
        bs = BeautifulSoup(b if b.startswith("<") else "<div " + b, "html.parser")
        logo = bs.find("img", alt=re.compile("logo equipa", re.I))
        if logo:
            c = logo.find_next(class_="athlete-char")
            clube = norm(c.get_text(" ")) if c else ""
        out.setdefault(int(m.group(1)), clube)
    if not out:
        for x in re.findall(r"/atletas/(\d+)", html):
            out.setdefault(int(x), "")
    return out


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


CACHE = ROOT / "ano-cache.json"   # guardado em cache das Actions, nunca no repositório (privacidade)


def anos_de(ids, threads=4):
    cache = {}
    try:
        cache = {int(k): v for k, v in json.loads(CACHE.read_text()).items()}
    except Exception:  # noqa: BLE001
        pass
    falta = [i for i in ids if i not in cache or cache[i] is None]
    log(f"Anos de nascimento: {len(ids) - len(falta)} já em cache, {len(falta)} por ler")
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(threads) as ex:
        for n, (aid, ano) in enumerate(zip(falta, ex.map(ano_nascimento, falta)), 1):
            cache[aid] = ano
            if n % 500 == 0:
                log(f"   {n}/{len(falta)} ({int(time.time() - t0)} s)")
                CACHE.write_text(json.dumps(cache))
    CACHE.write_text(json.dumps(cache))
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clubes", default="todos", help="'todos' ou IDs FPB separados por vírgula")
    ap.add_argument("--sem-idades", action="store_true")
    a = ap.parse_args()
    alvo = [""] if a.clubes.strip().lower() == "todos" else [c.strip() for c in a.clubes.split(",") if c.strip()]
    OUT.mkdir(exist_ok=True)
    hoje = datetime.date.today().isoformat()
    escaloes = escaloes_do_site(alvo[0] or 119)
    log(f"Escalões: {escaloes}")

    atleta = {}   # id -> [clube, escalão, género]
    primeiro = True
    for clube in alvo:
        log(f"== {'Todos os clubes' if not clube else 'Clube ' + clube} · época {EPOCA}")
        for g in GENEROS:
            try:
                total_g = pesquisa(clube, "", g)
                log(f"{g}: {len(total_g)} atletas (sem filtro de escalão)")
            except Exception as e:  # noqa: BLE001
                total_g = {}
                log(f"AVISO: total {g} falhou: {e}")
            vistos = {}
            for e in escaloes:
                try:
                    res = pesquisa(clube, e, g, mostrar=primeiro)
                except Exception as ex:  # noqa: BLE001
                    log(f"AVISO: {e}/{g} falhou: {ex}")
                    continue
                if res:
                    primeiro = False
                k = frozenset(res)
                if res and k in vistos:
                    log(f"AVISO: {e}/{g} devolveu a mesma lista que {vistos[k]}; ignorado.")
                    continue
                vistos[k] = e
                for aid, cl in res.items():
                    atleta.setdefault(aid, [cl or total_g.get(aid, "") or "?", e, g])
                log(f"   {e:22s} {g:10s} {len(res):6d}")
                time.sleep(0.5)
            for aid, cl in total_g.items():
                if aid not in atleta:
                    atleta[aid] = [cl or "?", "Sem escalão no filtro", g]
    log(f"Total: {len(atleta)} atletas em {len({v[0] for v in atleta.values()})} clubes")

    anos = {} if a.sem_idades else anos_de(sorted(atleta))
    ordem = {e: i for i, e in enumerate(escaloes + ["Sem escalão no filtro"])}

    # por clube e escalão
    t = collections.defaultdict(lambda: [0, 0])
    for cl, e, g in atleta.values():
        t[(cl, e)][GENEROS.index(g)] += 1
    rows = sorted(t.items(), key=lambda kv: (kv[0][0].lower(), ordem.get(kv[0][1], 99)))
    with open(OUT / "por-clube-escalao.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["data", "clube", "escalao", "masculino", "feminino", "total"])
        for (cl, e), (m, f) in rows:
            w.writerow([hoje, cl, e, m, f, m + f])

    # por clube, ano de nascimento, género e escalão
    if anos:
        c = collections.Counter()
        for aid, (cl, e, g) in atleta.items():
            c[(cl, anos.get(aid) or "", g, e)] += 1
        with open(OUT / "por-clube-ano.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["data", "clube", "ano_nascimento", "idade_em_31_dez", "genero", "escalao", "atletas"])
            ano_ref = int(EPOCA[:4])
            for (cl, ano, g, e), n in sorted(c.items(), key=lambda kv: (kv[0][0].lower(), str(kv[0][1]), kv[0][2])):
                w.writerow([hoje, cl, ano, (ano_ref - ano) if ano else "", g, e, n])

    # resumo nacional
    nat = collections.defaultdict(lambda: [0, 0])
    for (cl, e), mf in t.items():
        nat[e][0] += mf[0]
        nat[e][1] += mf[1]
    md = [f"# Atletas inscritos na FPB · época {EPOCA} · {hoje}\n",
          f"{len(atleta)} atletas em {len({v[0] for v in atleta.values()})} clubes\n",
          "| Escalão | Masculino | Feminino | Total |", "|---|---:|---:|---:|"]
    for e in sorted(nat, key=lambda x: ordem.get(x, 99)):
        m, f = nat[e]
        md.append(f"| {e} | {m} | {f} | {m + f} |")
    clubes_tot = collections.Counter(v[0] for v in atleta.values())
    md += ["", "## Clubes com mais atletas", "", "| Clube | Atletas |", "|---|---:|"]
    md += [f"| {cl} | {n} |" for cl, n in clubes_tot.most_common(30)]
    (OUT / "resumo.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log("\n".join(md))


if __name__ == "__main__":
    main()
