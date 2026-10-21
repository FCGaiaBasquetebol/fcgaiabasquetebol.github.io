#!/usr/bin/env python3
"""Atletas inscritos na FPB por clube, escalão, ano de nascimento e género.

Não mexe no site. Escreve só contagens (sem nomes) em dados-atletas/.
Fontes (fpb.pt, admin-ajax.php):
  - get_clubs_dropdown / get_clubes  -> lista de clubes
  - search_player (idClube, género)  -> atletas de cada clube (a pesquisa corta em 500)
  - /atletas/ID/                     -> ano de nascimento (cache fora do repositório)
O escalão é calculado pelo ano de nascimento; Masters vem do filtro da FPB.
"""
import argparse, concurrent.futures, collections, datetime, json, pathlib, re, time, unicodedata
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "dados-atletas"
CACHE = ROOT / "ano-cache.json"
FPB = "https://www.fpb.pt"
AJAX = f"{FPB}/wp-admin/admin-ajax.php"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
EPOCA = "2026/2027"
GENEROS = ["masculino", "feminino"]
LIMITE = 500
S = requests.Session()
S.headers.update(UA)


def log(m):
    print(m, flush=True)


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "").replace("\xa0", " ")).strip()


def plain(s):
    return unicodedata.normalize("NFD", norm(s)).encode("ascii", "ignore").decode().lower()


def pedido(method, data, tries=4, timeout=120):
    for i in range(tries):
        try:
            r = S.request(method, AJAX, **({"data": data} if method == "POST" else {"params": data}), timeout=timeout)
            r.raise_for_status()
            try:
                j = r.json()
            except ValueError:
                return r.text
            if isinstance(j, list):
                return "\n".join(x if isinstance(x, str) else json.dumps(x) for x in j)
            return j if isinstance(j, str) else json.dumps(j, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def escalao(ano, masters=False):
    """Escalão pela idade na época (ano de início da época - ano de nascimento)."""
    if masters:
        return "Masters / Veteranos"
    if not ano:
        return "Sem ano"
    i = int(EPOCA[:4]) - ano          # 2026/27: 2013 -> 13 -> Sub 14
    for lim, nome in ((5, "Baby-Basket"), (7, "Mini 8"), (9, "Mini 10"), (11, "Mini 12"),
                      (13, "Sub 14"), (15, "Sub 16"), (17, "Sub 18")):
        if i <= lim:
            return nome
    return "Sénior"


ORDEM = ["Baby-Basket", "Mini 8", "Mini 10", "Mini 12", "Sub 14", "Sub 16", "Sub 18", "Sénior",
         "Masters / Veteranos", "Sem ano"]


def clubes():
    """{id: nome} de todos os clubes."""
    out = {}
    for method, data in (("POST", {"action": "get_clubs_dropdown", "associacao": "", "epoca": EPOCA}),
                         ("GET", {"action": "get_clubes", "associacao": "0"}),
                         ("GET", {"action": "get_clubes", "associacao": ""})):
        try:
            html = pedido(method, data)
        except Exception as e:  # noqa: BLE001
            log(f"AVISO: {data['action']}: {e}")
            continue
        s = BeautifulSoup(html.replace('\\"', '"').replace("\\/", "/"), "html.parser")
        n0 = len(out)
        for el in s.find_all(True):
            cid = None
            for att in ("data-value", "value", "data-id", "data-idclube"):
                v = el.get(att)
                if v and str(v).isdigit():
                    cid = int(v)
                    break
            if cid is None and el.name == "a":
                m = re.search(r"clube_(\d+)", el.get("href", ""))
                cid = int(m.group(1)) if m else None
            if cid:
                nome = norm(el.get_text(" ")) or norm(el.get("title", ""))
                if nome and (cid not in out or len(nome) < len(out[cid])):
                    out[cid] = nome
        log(f"{data['action']} ({data.get('associacao')!r}): {len(out) - n0} clubes novos · amostra: {html[:300]!r}")
        if len(out) > 50:
            break
    return out


def atletas(cid, genero, esc=""):
    html = pedido("POST", {"action": "search_player", "epoca": EPOCA, "escalao": esc, "genero": genero,
                           "nome": "", "idAssociacao": "", "idClube": str(cid)})
    return set(int(x) for x in re.findall(r"/atletas/(\d+)", html.replace("\\/", "/")))


ESC_FILTRO = ["Baby-Basket", "Mini 8", "Mini 10", "Mini 12", "Minis 12 Mistos", "Sub 14", "Sub 16", "Sub 18",
              "Sub 20", "Esperanças", "Sénior", "Masters / Veteranos"]


def ano_nascimento(aid):
    for i in range(3):
        try:
            r = S.get(f"{FPB}/atletas/{aid}/", timeout=40)
            r.raise_for_status()
            lines = [norm(x) for x in BeautifulSoup(r.text, "html.parser").get_text("\n").split("\n") if norm(x)]
            for j, l in enumerate(lines):
                if plain(l).rstrip(":") == "data de nascimento" and j + 1 < len(lines):
                    m = re.search(r"(19|20)\d{2}", lines[j + 1])
                    return int(m.group(0)) if m else 0
            return 0
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clubes", default="todos")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    hoje = datetime.date.today().isoformat()

    lista = clubes()
    if a.clubes.strip().lower() != "todos":
        ids = [int(x) for x in a.clubes.split(",") if x.strip()]
        lista = {i: lista.get(i, f"Clube {i}") for i in ids}
    log(f"{len(lista)} clubes a ler")
    if not lista:
        raise SystemExit("ERRO: lista de clubes vazia")

    reg = {}                       # id atleta -> [id clube, género, masters?]
    t0 = time.time()
    for n, (cid, nome) in enumerate(sorted(lista.items()), 1):
        for g in GENEROS:
            try:
                ids = atletas(cid, g)
                if len(ids) >= LIMITE:      # corte da FPB: junta por escalão
                    for e in ESC_FILTRO:
                        ids |= atletas(cid, g, e)
                        time.sleep(0.3)
                mast = atletas(cid, g, "Masters / Veteranos") if ids else set()
            except Exception as e:  # noqa: BLE001
                log(f"AVISO: {nome} ({cid}) {g}: {e}")
                continue
            for aid in ids:
                reg.setdefault(aid, [cid, g, aid in mast])
            time.sleep(0.3)
        if n % 25 == 0:
            log(f"   {n}/{len(lista)} clubes · {len(reg)} atletas · {int(time.time() - t0)} s")
    log(f"Total: {len(reg)} atletas")

    cache = {}
    try:
        cache = {int(k): v for k, v in json.loads(CACHE.read_text()).items()}
    except Exception:  # noqa: BLE001
        pass
    falta = [i for i in reg if cache.get(i) is None]
    log(f"Anos de nascimento: {len(reg) - len(falta)} em cache, {len(falta)} por ler")
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(a.threads) as ex:
        for n, (aid, ano) in enumerate(zip(falta, ex.map(ano_nascimento, falta)), 1):
            cache[aid] = ano
            if n % 1000 == 0:
                log(f"   {n}/{len(falta)} ({int(time.time() - t0)} s)")
                CACHE.write_text(json.dumps(cache))
    CACHE.write_text(json.dumps(cache))

    cont = collections.Counter()
    for aid, (cid, g, mast) in reg.items():
        ano = cache.get(aid) or 0
        cont[(cid, escalao(ano, mast), ano, g)] += 1
    dados = {"epoca": EPOCA, "data": hoje, "clubes": {str(k): v for k, v in lista.items()},
             "linhas": [[cid, e, ano, g[0].upper(), n] for (cid, e, ano, g), n in
                        sorted(cont.items(), key=lambda kv: (kv[0][0], ORDEM.index(kv[0][1]), kv[0][2], kv[0][3]))]}
    (OUT / "atletas.json").write_text(json.dumps(dados, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    nat = collections.defaultdict(lambda: [0, 0])
    for (cid, e, ano, g), n in cont.items():
        nat[e][GENEROS.index(g)] += n
    md = [f"# Atletas inscritos na FPB · época {EPOCA} · {hoje}", "",
          f"{len(reg)} atletas em {len({v[0] for v in reg.values()})} clubes", "",
          "| Escalão | Masculino | Feminino | Total |", "|---|---:|---:|---:|"]
    md += [f"| {e} | {nat[e][0]} | {nat[e][1]} | {sum(nat[e])} |" for e in ORDEM if e in nat]
    (OUT / "resumo.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log("\n".join(md))


if __name__ == "__main__":
    main()
