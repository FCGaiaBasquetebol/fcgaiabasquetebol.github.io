#!/usr/bin/env python3
"""Atualiza planteis, fotos e carreiras dos treinadores do FC Gaia em data/jogos.json.

Fontes (fpb.pt):
  - /equipas/clube_119/ e /treinadores/clube_119/  -> listas (carregadas por JavaScript; lidas com Playwright)
  - /equipa/equipa_ID/   -> atletas de cada equipa
  - /atletas/ID/         -> número, posição, altura, ano de nascimento (sem foto nem data completa)
  - /treinadores/ID/     -> grau, foto e carreira

Regras:
  - Se uma equipa ou treinador falhar, mantém o que já estava guardado.
  - IDs fixos em config.json ("equipas_fpb", "treinadores_fpb") têm prioridade sobre a descoberta automática.
  - Equipa técnica e coordenação vêm de config.json ("equipa_tecnica", "coordenacao").
"""
import json, re, sys, time, unicodedata, datetime, pathlib
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "jogos.json"
DEBUG = ROOT / "debug"
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
FPB = "https://www.fpb.pt"
CLUBE = CONFIG.get("clube_fpb", 119)
UA = {"User-Agent": "Mozilla/5.0 (FC Gaia site; atualizacao mensal do plantel)"}
TEAMS = [t["id"] for t in CONFIG["equipas"]]


def log(msg):
    print(msg, flush=True)


def norm(s):
    s = unicodedata.normalize("NFC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def plain(s):
    s = unicodedata.normalize("NFD", norm(s)).encode("ascii", "ignore").decode()
    return s.lower()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", plain(s)).strip("-")


def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return BeautifulSoup(r.text, "html.parser")
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(3 + 3 * i)


# ---------- descoberta (páginas com JavaScript) ----------

def rendered_links(url, pattern, name):
    """Abre a página num browser, espera pela lista e devolve {id: texto do link}."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("AVISO: Playwright não instalado; uso só os IDs de config.json.")
        return {}
    out = {}
    DEBUG.mkdir(exist_ok=True)
    xhr, bodies, links = [], [], []

    def on_resp(r):
        try:
            if r.request.resource_type in ("xhr", "fetch", "document"):
                xhr.append(f"{r.status} {r.request.method} {r.url} {r.request.post_data or ''}"[:600])
                t = r.text()
                if pattern.strip("/").split("/")[0] in t or "atleta" in t or "treinador" in t:
                    bodies.append(t)
        except Exception:  # noqa: BLE001
            pass

    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
                            viewport={"width": 1280, "height": 2000})
            pg.on("response", on_resp)
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:  # noqa: BLE001
                log(f"AVISO: {url} demorou a abrir: {e}")
            try:
                pg.wait_for_selector(f"a[href*='{pattern}']", timeout=45000)
            except Exception:  # noqa: BLE001
                log(f"AVISO: a lista em {url} não apareceu em 45 s; tento carregar em 'Aplicar filtros'.")
                try:
                    pg.get_by_text(re.compile("aplicar filtros", re.I)).first.click(timeout=5000)
                    pg.wait_for_selector(f"a[href*='{pattern}']", timeout=30000)
                except Exception:  # noqa: BLE001
                    pass
            for _ in range(4):
                pg.mouse.wheel(0, 3000)
                pg.wait_for_timeout(800)
            try:
                links = pg.eval_on_selector_all(
                    "a[href]", "els=>els.map(e=>[e.href,(e.innerText||'').trim(),((e.closest('li,tr,article')||e.parentElement||e).innerText||'')])")
            except Exception as e:  # noqa: BLE001
                log(f"AVISO: {e}")
            try:
                (DEBUG / f"{name}.html").write_text(pg.content(), encoding="utf-8")
                pg.screenshot(path=str(DEBUG / f"{name}.png"), full_page=True)
            except Exception:  # noqa: BLE001
                pass
            b.close()
    except Exception as e:  # noqa: BLE001
        log(f"AVISO: falhou a leitura de {url} com o browser: {e}")
    (DEBUG / f"{name}-pedidos.txt").write_text("\n".join(xhr), encoding="utf-8")
    for i, t in enumerate(bodies[:20]):
        (DEBUG / f"{name}-resposta{i}.txt").write_text(t[:300000], encoding="utf-8")
        try:
            for a in BeautifulSoup(t, "html.parser").find_all("a", href=True):
                links.append([a["href"], a.get_text(" "), (a.parent.get_text(" ") if a.parent else "")])
        except Exception:  # noqa: BLE001
            pass
    for href, txt, ctx in links:
        m = re.search(re.escape(pattern) + r"(\d+)", href.replace("\\/", "/"))
        if m:
            t, c = norm(txt), norm(ctx)[:200]
            out.setdefault(int(m.group(1)), t if not c or c == t else f"{t} | {c}")
    log(f"{url}: {len(out)} links encontrados")
    for k, v in out.items():
        log(f"   {k}: {v[:120]}")
    return out


def team_from_text(t):
    """'FC Gaia B · Sub 18 Masculino' -> 'Sub-18 Masc B'. Devolve None se não for equipa configurada."""
    t = plain(t)
    # minibasquete: junta masculino, feminino e misto no mesmo escalão
    if re.search(r"\bbaby\b", t):
        return "Baby" if "Baby" in TEAMS else None
    for n in ("12", "10", "8"):
        if re.search(rf"(mini|sub)[\s\-_]*(basquete\s*)?{n}\b", t):
            tid = f"Sub-{n}"
            return tid if tid in TEAMS else None
    if "femin" in t:
        return None
    if re.search(r"master|veteran|maxi", t):
        return "Masters" if "Masters" in TEAMS else None
    if "esperanc" in t:
        return "Esperanças"
    for n in ("18", "16", "14"):
        if re.search(rf"sub[\s\-_]*{n}\b", t):
            b = re.search(r"gaia\s*\"?b\"?\b|\"b\"|\bequipa\s*b\b|\sb\s*$|\bb\s*[\|,·-]", t)
            tid = f"Sub-{n} Masc {'B' if b else 'A'}"
            return tid if tid in TEAMS else None
    if "senior" in t and "sub" not in t:
        return "Seniores Masc"
    return None


MINI = {"Sub-12", "Sub-10", "Sub-8", "Baby"}


def discover_teams():
    """Devolve {equipa: [ids FPB]} (no minibasquete pode haver várias equipas por escalão)."""
    ids = {}
    for k, v in CONFIG.get("equipas_fpb", {}).items():
        if v:
            ids[k] = [int(x) for x in (v if isinstance(v, list) else [v])]
    found = rendered_links(f"{FPB}/equipas/clube_{CLUBE}/", "/equipa/equipa_", "equipas")
    for eid, txt in found.items():
        if any(eid in v for v in ids.values()):
            continue
        tid = team_from_text(txt)
        if not tid:
            # o texto do link pode não dizer o escalão; vê o título da página da equipa
            try:
                s = get(f"{FPB}/equipa/equipa_{eid}/")
                head = " ".join(norm(h.get_text(" ")) for h in s.select("title,h1,h2,h3")[:6])
                tid = team_from_text(head)
                txt = head
            except Exception as e:  # noqa: BLE001
                log(f"AVISO: equipa {eid}: {e}")
        if tid and tid in ids and tid.endswith(" A") and tid[:-1] + "B" not in ids:
            log(f"   {tid} já atribuída; equipa_{eid} fica como {tid[:-1]}B (confirma no site)")
            tid = tid[:-1] + "B"
        if tid in MINI:
            ids.setdefault(tid, []).append(eid)
            log(f"Equipa {tid} -> equipa_{eid}  ({txt[:80]})")
        elif tid and tid not in ids:
            ids[tid] = [eid]
            log(f"Equipa {tid} -> equipa_{eid}  ({txt[:80]})")
        elif not tid:
            log(f"   ignorada equipa_{eid}: {txt[:80]}")
    for t in TEAMS:
        if t not in ids:
            log(f"AVISO: não encontrei a página FPB da equipa {t}. Junta o ID em config.json -> equipas_fpb.")
    return ids


# escalões lidos pela pesquisa de atletas do clube (/atletas/clube_119/): equipa -> (opções do filtro, géneros)
FILTRO_ATLETAS = {
    "Baby": (["Baby-Basket"], ["masculino", "feminino"]),
    "Sub-8": (["Mini 8"], ["masculino", "feminino"]),
    "Sub-10": (["Mini 10"], ["masculino", "feminino"]),
    "Sub-12": (["Mini 12", "Minis 12 Mistos"], ["masculino", "feminino"]),
    "Masters": (["Masters / Veteranos"], ["masculino"]),
}


def club_athletes_mini():
    """Atletas do clube por escalão, pelo mesmo pedido que a página /atletas/clube_119/ faz ao escolher
    Género e Escalão (admin-ajax.php, action=search_player). Devolve {equipa: {id: nome}}.
    Proteção: se dois escalões devolverem exatamente a mesma lista, o filtro não funcionou e nada é usado."""
    out = {t: {} for t in FILTRO_ATLETAS if t in TEAMS}
    epoca = CONFIG.get("epoca_fpb", "2026/2027")
    DEBUG.mkdir(exist_ok=True)
    seen = {}
    for tid, (opts, gens) in FILTRO_ATLETAS.items():
        if tid not in out:
            continue
        for opt in opts:
            for g in gens:
                data = {"action": "search_player", "epoca": epoca, "escalao": opt, "genero": g,
                        "nome": "", "idAssociacao": "", "idClube": str(CLUBE)}
                try:
                    r = requests.post(f"{FPB}/wp-admin/admin-ajax.php", data=data, headers=UA, timeout=30)
                    r.raise_for_status()
                    try:
                        html = r.json()
                    except ValueError:
                        html = r.text
                    html = html if isinstance(html, str) else json.dumps(html)
                    (DEBUG / f"atletas-{slug(tid)}-{slug(opt)}-{g}.html").write_text(html, encoding="utf-8")
                    soup = BeautifulSoup(html, "html.parser")
                    got = {}
                    for a in soup.find_all("a", href=re.compile(r"/atletas/(\d+)")):
                        aid = int(re.search(r"/atletas/(\d+)", a["href"]).group(1))
                        nm = norm(a.get_text(" "))
                        if nm:
                            got.setdefault(aid, nm)
                    key = frozenset(got)
                    if got and key in seen:
                        log(f"AVISO: {tid} ({opt} · {g}) devolveu a mesma lista que {seen[key][1]}; filtro ignorado nos dois.")
                        out[tid] = {}
                        out.setdefault("_invalido", set()).update({tid, seen[key][0]})
                        continue
                    if got:
                        seen[key] = (tid, f"{tid} ({opt} · {g})")
                    if tid not in out.get("_invalido", set()):
                        for k, v in got.items():
                            out[tid].setdefault(k, v)
                    log(f"   {tid} ({opt} · {g}): {len(got)} atletas")
                except Exception as e:  # noqa: BLE001
                    log(f"AVISO: filtro {tid}/{opt}/{g}: {e}")
                time.sleep(0.5)
    bad = out.pop("_invalido", set())
    for t in bad:
        out[t] = {}
    return out


def discover_coaches(names):
    ids = {k: int(v) for k, v in CONFIG.get("treinadores_fpb", {}).items() if v}
    found = rendered_links(f"{FPB}/treinadores/clube_{CLUBE}/", "/treinadores/", "treinadores")
    for n in names:
        if n in ids:
            continue
        want = set(plain(n).split())
        for cid, txt in found.items():
            if want <= set(re.split(r"[^a-z]+", plain(txt))):
                ids[n] = cid
                log(f"Treinador {n} -> {cid}  ({txt[:60]})")
                break
        else:
            log(f"AVISO: não encontrei a ficha FPB de {n}. Junta o ID em config.json -> treinadores_fpb.")
    return ids


# ---------- páginas de equipa, atleta e treinador (HTML normal) ----------

def short(name):
    name = norm(name)
    if name.isupper():
        name = " ".join(w if w.lower() in ("da", "de", "do", "das", "dos", "e") else w.capitalize() for w in name.lower().split())
    p = [w for w in name.split() if w.lower() not in ("da", "de", "do", "das", "dos", "e")]
    return f"{p[0]} {p[-1]}" if len(p) > 1 else norm(name)


def team_athletes(eid):
    """{id_atleta: (nome, número na equipa)} a partir da página da equipa na FPB."""
    s = get(f"{FPB}/equipa/equipa_{eid}/")
    try:
        DEBUG.mkdir(exist_ok=True)
        (DEBUG / f"equipa_{eid}.html").write_text(str(s), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    out = {}
    for a in s.select("a[href*='/atletas/']"):
        m = re.search(r"/atletas/(\d+)", a["href"])
        if not m:
            continue
        aid = int(m.group(1))
        name = re.sub(r"^\d{1,3}\s+|\s+\d{1,3}$", "", norm(norm(a.get_text(" ")).split("•")[-1]))
        # cartão do atleta: sobe até ao elemento que só tem este atleta
        card = a
        for _ in range(4):
            up = card.parent
            if not up or len({x.get("href") for x in up.select("a[href*='/atletas/']")}) > 1:
                break
            card = up
        num = None
        for t in card.stripped_strings:
            t = norm(t).lstrip("#Nº").strip()
            if re.fullmatch(r"\d{1,3}", t):
                num = int(t)
                break
        if aid not in out or (out[aid][1] is None and num is not None):
            out[aid] = (name, num)
    return out


def label_value(lines, label):
    for i, l in enumerate(lines):
        if plain(l).rstrip(":") == plain(label) and i + 1 < len(lines):
            return lines[i + 1]
    return ""


def athlete(aid, fallback):
    s = get(f"{FPB}/atletas/{aid}/")
    lines = [norm(x) for x in s.get_text("\n").split("\n") if norm(x)]
    img = s.find("img", alt=re.compile("perfil", re.I))
    num, name = None, fallback
    if img:
        h = img.find_next(["h1", "h2", "h3"])
        if h and norm(h.get_text()):
            name = norm(h.get_text(" "))
        for st in img.find_all_next(string=True, limit=40):
            t = norm(st)
            if re.fullmatch(r"\d{1,3}", t):
                num = int(t)
                break
            if plain(t) in ("pontos", "nr. licenca") or (h and t == name):
                break
    pos = label_value(lines, "Posição").title()
    alt = re.sub(r"\D", "", label_value(lines, "Altura"))
    nac = label_value(lines, "Nacionalidade")
    flag = s.find("img", alt=re.compile(r"^[A-Za-z]{2}$"))
    nac_cc = flag.get("alt", "").lower() if flag else ""
    nac_flag = flag.get("src", "") if flag else ""
    if nac_flag.startswith("/"):
        nac_flag = FPB + nac_flag
    # só o ano: a data completa e a foto dos atletas não são guardadas (privacidade)
    nasc_txt = label_value(lines, "Data de Nascimento")
    m = re.search(r"(19|20)\d{2}", nasc_txt)
    ano = int(m.group(0)) if m else None
    # histórico de inscrições: tabela com Época / Clube
    insc = []
    for tb in s.find_all("table"):
        hd = [plain(x.get_text(" ")) for x in tb.find_all("th")]
        if not any("epoca" in h for h in hd) or not any("clube" in h for h in hd):
            continue
        for tr in tb.find_all("tr"):
            td = [norm(x.get_text(" ")) for x in tr.find_all("td")]
            if len(td) < 2 or not re.match(r"\d{4}", td[0]):
                continue
            extra = [c for i, c in enumerate(td[2:], 2) if c and c != "-" and (i >= len(hd) or "jogos" not in hd[i])]
            insc.append([td[0], td[1], " · ".join(extra)])
        break
    return {"name": short(name), "num": num, "pos": pos, "h": int(alt) if alt and 140 <= int(alt) <= 240 else None,
            "id": aid, "ano": ano, "insc": insc, "nac": nac, "nac_cc": nac_cc, "nac_flag": nac_flag}


ROLE = re.compile(r"^\(?(principal|adjunto|treinador.*|estagi.*)\)?$", re.I)


def coach(cid, name):
    s = get(f"{FPB}/treinadores/{cid}/")
    lines = [norm(x) for x in s.get_text("\n").split("\n") if norm(x)]
    img = s.find("img", alt=re.compile("perfil", re.I))
    photo = img.get("src", "") if img else ""
    if "uploads/utilizadores" not in photo:
        photo = ""
    grau = label_value(lines, "Nível Formação") or label_value(lines, "Nivel Formacao")
    car = []
    for tb in s.find_all("table"):
        hd = plain(tb.get_text(" ")[:200])
        if "epoca" not in hd or "fun" not in hd:
            continue
        for tr in tb.find_all("tr"):
            td = tr.find_all("td")
            if len(td) < 3:
                continue
            parts = []
            for l in td[2].get_text("\n").split("\n"):
                l = norm(l).strip(";,")
                if not l:
                    continue
                if parts and ROLE.match(l):
                    parts[-1] += " - " + l.strip("()").capitalize()
                else:
                    parts.append(l)
            funcs = "; ".join(parts)
            funcs = re.sub(r"\s*\((Principal|Adjunto)\)", r" - \1", funcs)
            car.append([norm(td[0].get_text()), norm(td[1].get_text(" ")), funcs])
        break
    return {"name": name, "id": cid, "grau": grau, "photo": photo, "carreira": car}


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    staff = CONFIG.get("equipa_tecnica", data.get("staff", {}))
    coord = CONFIG.get("coordenacao", data.get("coord", []))
    plantel = data.get("plantel", {})
    coaches = data.get("coaches", {})
    before = json.dumps([plantel, coaches, staff, coord], ensure_ascii=False, sort_keys=True)

    teams = discover_teams()
    mini_extra = club_athletes_mini()
    for t, d in mini_extra.items():
        if d:
            teams.setdefault(t, [])
    names = []
    for lst in staff.values():
        names += [n for n in lst if n not in names]
    names += [c["name"] for c in coord if c["name"] not in names]
    total_steps, done = len(teams) + len(names), 0

    def pct(label):
        log(f"[{round(100 * done / max(total_steps, 1)):3d}%] {label}")

    for tid, eids in teams.items():
        eid = eids
        pct(f"Plantel {tid}")
        done += 1
        try:
            ath = {}
            for e in eids:
                for k, v in team_athletes(e).items():
                    ath.setdefault(k, v)
            for k, nm in mini_extra.get(tid, {}).items():
                ath.setdefault(k, (nm, None))
            rows = []
            for aid, (nm, tnum) in ath.items():
                try:
                    a = athlete(aid, nm)
                    if tnum is not None:
                        a["num"] = tnum  # o número da equipa tem prioridade sobre o da ficha
                    rows.append(a)
                except Exception as e:  # noqa: BLE001
                    log(f"AVISO: atleta {aid} ({nm}): {e}")
                time.sleep(0.4)
            if rows:
                plantel[tid] = rows
                log(f"{tid}: {len(rows)} atletas")
            else:
                log(f"AVISO: {tid}: nenhum atleta lido; mantenho o anterior.")
        except Exception as e:  # noqa: BLE001
            log(f"AVISO: equipa {tid} ({eid}): {e}")

    found_c = discover_coaches(names)
    done += len(names) - len(found_c)
    for n, cid in found_c.items():
        pct(f"Treinador {n}")
        done += 1
        try:
            c = coach(cid, n)
            old = coaches.get(slug(n), {})
            if not c["carreira"] and old.get("carreira"):
                c["carreira"] = old["carreira"]
            c["grau"] = c["grau"] or old.get("grau", "")
            c["photo"] = c["photo"] or old.get("photo", "")
            coaches[slug(n)] = c
            log(f"Treinador {n}: {c['grau'] or 'sem grau'}, {len(c['carreira'])} épocas, foto {'sim' if c['photo'] else 'não'}")
        except Exception as e:  # noqa: BLE001
            log(f"AVISO: treinador {n} ({cid}): {e}")
        time.sleep(0.4)

    log(f"[100%] Concluído: {sum(len(v) for v in plantel.values())} atletas em {len(plantel)} escalões, {len(coaches)} treinadores.")
    data.update(plantel=plantel, coaches=coaches, staff=staff, coord=coord)
    if json.dumps([plantel, coaches, staff, coord], ensure_ascii=False, sort_keys=True) != before:
        data["updated"] = datetime.date.today().isoformat()
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        log("Plantel e treinadores atualizados.")
    else:
        log("Plantel e treinadores sem alterações.")
    if not plantel:
        sys.exit("ERRO: nenhum plantel lido.")


if __name__ == "__main__":
    main()
