#!/usr/bin/env python3
"""Atualiza data/jogos.json com os jogos, resultados e classificações do FC Gaia.

Fontes:
  - Centro de Resultados FPB (resultados.tugabasket.com): calendários e classificações
  - Calendário do clube em fpb.pt: horas e pavilhões já marcados (sobretudo Esperanças)

Regras:
  - Nunca apaga jogos nem fases já guardados (só acrescenta ou atualiza).
  - Marcações confirmadas das Esperanças mantêm-se se a fonte deixar de as mostrar.
  - Se uma fonte falhar, mantém os dados anteriores dessa parte.
"""
import json, re, sys, time, unicodedata, datetime, pathlib
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "jogos.json"
CONFIG = json.loads((ROOT / "scripts" / "config.json").read_text(encoding="utf-8"))
BASE = "https://resultados.tugabasket.com"
CLUB_URL = CONFIG["calendario_clube"]
UA = {"User-Agent": "Mozilla/5.0 (FC Gaia calendario; atualizacao diaria)"}
LOG = []


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


def get(url, tries=3):
    """Pede uma página com sessão nova (o site guarda estado em sessão)."""
    for i in range(tries):
        try:
            r = requests.Session().get(url, headers=UA, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(3 + 3 * i)


def norm(s):
    s = unicodedata.normalize("NFC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def is_gaia(name):
    return norm(name).lower().startswith("fc gaia")


def nome_equipa(name):
    """A fonte às vezes escreve o clube em maiúsculas ('FC GAIA'): normaliza para 'FC Gaia'
    (mantendo o resto, ex.: 'FC GAIA A' -> 'FC Gaia A'). Os outros nomes ficam como vêm."""
    n = norm(name)
    return re.sub(r"^fc gaia\b", "FC Gaia", n, flags=re.I) if is_gaia(n) else n


def phase_and_group(title, cup=False):
    """'1ª Fase - Série B' -> ('1.ª Fase', 'Série B'); Taça -> ('Taça de Portugal', ...)."""
    t = norm(title)
    if cup:
        g = re.sub(r"(?i)^\d+\.?ª\s*fase\s*-\s*", "", t)
        g = re.sub(r"(?i)eliminat[oó]ria", "Elim.", g).replace(" - ", " ")
        return "Taça de Portugal", g.replace("Zona ", "")
    m = re.match(r"(?i)^(\d+)\.?\s*ª\s*fase\s*(?:-\s*(.*))?$", t)
    if m:
        grp = (m.group(2) or "").strip()
        grp = grp.title() if grp.isupper() else grp
        return f"{m.group(1)}.ª Fase", grp
    return t.title() if t.isupper() else t, ""


def cells(tr):
    return [norm(td.get_text(" ")) for td in tr.find_all(["td", "th"])]


# ---------- Centro de Resultados ----------

def competition_phases(comp_id):
    """Devolve {phaseId: titulo} e as tabelas de classificação do resumo."""
    soup = get(f"{BASE}/getCompetitionDetails?competitionId={comp_id}")
    phases, tables = {}, {}
    for a in soup.find_all("a", href=re.compile(r"getSchedule\?competitionId=%s&phaseId=(\d+)" % comp_id)):
        pid = re.search(r"phaseId=(\d+)", a["href"]).group(1)
        head = a.find_previous(string=re.compile(r"(?i)fase|elimina"))
        if head and pid not in phases:
            phases[pid] = norm(head)
    for table in soup.find_all("table"):
        link = table.find_previous("a", href=re.compile(r"phaseId=(\d+)"))
        if not link:
            continue
        pid = re.search(r"phaseId=(\d+)", link["href"]).group(1)
        rows = []
        for tr in table.find_all("tr"):
            c = [x for x in cells(tr)]
            if not c or not c[0].isdigit():
                continue
            name = next((x for x in c[1:] if x and not re.fullmatch(r"-?\d+", x)), None)
            if not name:
                continue
            nums = [int(x) for x in c[c.index(name) + 1:] if re.fullmatch(r"-?\d+", x)]
            if any("EXCLU" in x.upper() for x in c):
                rows.append([name, "exc"])
            elif len(nums) >= 4:
                rows.append([name] + nums[:4])
        if rows:
            tables[pid] = rows
    return phases, tables


def schedule_games(comp_id, phase_id):
    soup = get(f"{BASE}/getSchedule?competitionId={comp_id}&phaseId={phase_id}")
    if not soup.find("tr"):
        log(f"AVISO: página sem tabela em {comp_id}/{phase_id}: {norm(soup.get_text(' '))[:200]}")
    out, jor = [], None
    for tr in soup.find_all("tr"):
        c = cells(tr)
        txt = " ".join(c)
        m = re.search(r"(\d+)\s*ª\s*Jornada", txt)
        if m and not (c and c[0].isdigit()):
            jor = int(m.group(1))
            continue
        if len(c) < 7 or not c[0].isdigit():
            continue
        num, d, h, home, res, away, venue = c[:7]
        if "ANULAD" in d.upper():
            continue
        date = d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else ""
        hora = h if re.fullmatch(r"\d{1,2}:\d{2}", h) else ""
        r = re.fullmatch(r"(\d+)\s*[:\-]\s*(\d+)", res or "")
        out.append(dict(num=int(num), jor=jor, date=date, time=hora, home=nome_equipa(home), away=nome_equipa(away),
                        venue=venue, res=f"{r.group(1)}-{r.group(2)}" if r else ""))
    return out


def standings_full(comp_id, phase_id, group):
    """Classificação detalhada (J, V, D, PTS, PM, PS) e logos.
    Só é aceite se a página for mesmo do grupo pedido (o site às vezes devolve outro grupo)."""
    soup = get(f"{BASE}/standings?competitionId={comp_id}&phaseId={phase_id}")
    text = soup.get_text(" ").lower()
    if group and group.lower() not in text:
        return None, {}
    rows, logos = [], {}
    for table in soup.find_all("table"):
        head = None
        for tr in table.find_all("tr"):
            c = cells(tr)
            up = [x.upper() for x in c]
            if "J" in up and "PTS" in up:
                head = up
                continue
            if not head or not c:
                continue
            img = tr.find("img", src=re.compile(r"sav2\.fpb\.pt"))
            # nome: primeira célula com letras (pode vir "1 FC Gaia A" ou pos e nome separados)
            name = next((x for x in c if re.search(r"[A-Za-zÀ-ÿ]", x)), None)
            if not name:
                continue
            name = nome_equipa(re.sub(r"^\d+\s+", "", name))
            if img and not is_gaia(name):
                logos[name] = img["src"]
            if any("EXCLU" in x.upper() for x in c):
                rows.append([name, "exc"])
                continue
            off = len(c) - len(head)   # células a mais/menos antes das colunas numéricas
            def col(lbl):
                i = head.index(lbl) + off
                return int(c[i]) if 0 <= i < len(c) and re.fullmatch(r"-?\d+", c[i]) else None
            vals = [col(k) for k in ("J", "V", "D", "PTS")]
            if None in vals:
                continue
            pm, ps = (col("PM") if "PM" in head else None), (col("PS") if "PS" in head else None)
            rows.append([name] + vals + ([pm, ps] if pm is not None and ps is not None else []))
        if rows:
            break
    return (rows or None), logos


# ---------- Calendário do clube (fpb.pt) ----------

MESES = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}


def club_calendar():
    """Lista de jogos marcados [{date,time,home,away,cat,venue}] (leitura por linhas de texto)."""
    soup = get(CLUB_URL)
    lines = [norm(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    out, date = [], None
    i = 0
    while i < len(lines):
        m = re.fullmatch(r"(\d{1,2}) (JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ) (\d{4})", lines[i].upper())
        if m:
            date = f"{m.group(3)}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"
            i += 1
            continue
        t = re.fullmatch(r"(\d{1,2})H(\d{2})", lines[i].upper())
        if t and date and i >= 1:
            block = lines[max(0, i - 2): i + 8]
            cat = next((x for x in block if re.search(r"(?i)campeonato|ta[cç]a|divis", x)), "")
            teams = [x for x in lines[max(0, i - 2): i + 4]
                     if x not in (lines[i], cat) and not re.search(r"(?i)masculino|feminino|campeonato|ta[cç]a|divis|^\d", x)]
            venue = ""
            if cat:
                k = lines.index(cat, max(0, i - 2))
                rest = [x for x in lines[k + 1:k + 3] if not re.search(r"(?i)masculino|feminino", x)]
                venue = re.sub(r"\s*,.*$", "", rest[0]) if rest else ""
            if len(teams) >= 2:
                out.append(dict(date=date, time=f"{int(t.group(1)):02d}:{t.group(2)}",
                                home=teams[0], away=teams[1], cat=cat, venue=venue))
        i += 1
    return out


def club_results(games):
    """Resultados do clube em fpb.pt/resultados/clube_119/ (fonte de reserva).
    Usa os nomes de equipas já conhecidos para encontrar cada jogo e os dois números entre eles."""
    soup = get(CONFIG["resultados_clube"])
    try:
        dbg = ROOT / "debug"
        dbg.mkdir(exist_ok=True)
        (dbg / "resultados-clube.html").write_text(str(soup), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    lines = [norm(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    blocks, cur = {}, None
    for x in lines:
        m = re.fullmatch(r"(\d{1,2}) (JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ) (\d{4})", x.upper())
        if m:
            cur = f"{m.group(3)}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"
            blocks.setdefault(cur, [])
            continue
        if cur:
            blocks[cur].append(x)
    found = []
    for g in games:
        if g["res"] or g["date"] not in blocks:
            continue
        bl = [x.lower() for x in blocks[g["date"]]]
        h, a = norm(g["home"]).lower(), norm(g["away"]).lower()
        if h not in bl or a not in bl:
            continue
        # procura o par casa/fora do MESMO jogo (casa seguida de fora, poucas linhas depois),
        # para não misturar dois jogos do FC Gaia no mesmo dia
        pair = next(((i, j) for i, x in enumerate(bl) if x == h
                     for j in range(i + 1, min(i + 9, len(bl))) if bl[j] == a), None)
        if not pair:
            continue
        ih, ia = pair
        seg = blocks[g["date"]][ih + 1:ia]
        if any(x.lower() in (h, a) for x in seg if x.lower() not in (h, a)) or len([x for x in seg if x.lower() not in (h, a)]) > 4:
            continue
        nums = [int(x) for x in seg if re.fullmatch(r"\d{1,3}", x)]
        if len(nums) != 2:
            continue
        sh, sa = nums[0], nums[1]
        found.append((g, f"{sh}-{sa}"))
    return found, len(blocks)


# ---------- Masters: competição descoberta automaticamente ----------

def masters_competitions():
    """Procura no Centro de Resultados (FPB e AB Porto) competições de Masters/Veteranos masculinas.
    Assim que o calendário for publicado, passa a ser lido sem mexer no config.json."""
    if "Masters" not in {t["id"] for t in CONFIG["equipas"]}:
        return []
    season = CONFIG.get("epoca_resultados", 65)
    known = {c["id"] for c in CONFIG["competicoes"]}  # as já guardadas voltam a ser vistas (não faz mal)
    out = []
    for assoc, fonte in ((50, "FPB"), (4, "ABP")):
        try:
            soup = get(f"{BASE}/competitions?associationId={assoc}&seasonId={season}")
        except Exception as e:  # noqa: BLE001
            log(f"AVISO: lista de competições {fonte}: {e}")
            continue
        for a in soup.find_all("a", href=re.compile(r"competitionId=(\d+)")):
            name = norm(a.get_text(" ")) or norm(a.get("title", ""))
            if not re.search(r"(?i)master|veteran|maxi", name) or re.search(r"(?i)femin", name):
                continue
            cid = int(re.search(r"competitionId=(\d+)", a["href"]).group(1))
            if cid in known or any(c["id"] == cid for c in out):
                continue
            log(f"Masters: competição encontrada {cid} ({name}, {fonte})")
            out.append({"id": cid, "nome": re.sub(r"(?i)\s*s[ée]nior$", "", name), "equipa": "Masters", "fonte": fonte})
    if not out:
        log("Masters: calendário ainda não publicado no Centro de Resultados.")
    return out


# ---------- Atualização ----------

def team_id(base, gaia_name, ab):
    if not ab:
        return base
    m = re.search(r"\b([AB])$", norm(gaia_name))
    return f"{base} {m.group(1)}" if m else None


def pending_results(data, now=None):
    """Jogos que começaram há 1h30–30h e ainda não têm resultado. Sem hora conta como 21h00."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Lisbon")
    now = now or datetime.datetime.now(tz)
    out = []
    for r in data["games"]:
        team, date, hora, res = r[0], r[5], r[6], r[11]
        if res or not date:
            continue
        h, m = (hora or "21:00").split(":")[:2]
        start = datetime.datetime.fromisoformat(f"{date}T{int(h):02d}:{int(m):02d}").replace(tzinfo=tz)
        if datetime.timedelta(hours=1, minutes=30) <= now - start <= datetime.timedelta(hours=30):
            out.append(r)
    return out


def main(mode="calendario"):
    data = json.loads(DATA.read_text(encoding="utf-8"))
    comps = list(CONFIG["competicoes"])
    # competições descobertas (Masters) ficam guardadas para os modos seguintes (ex.: resultados)
    extra = {c["id"]: c for c in data.get("comps_extra", [])}
    if mode == "calendario":
        for c in masters_competitions():
            extra[c["id"]] = c
        data["comps_extra"] = list(extra.values())
    comps += [c for c in extra.values() if c["id"] not in {x["id"] for x in comps}]
    if mode == "resultados":
        pend = pending_results(data)
        if not pend:
            log("Nenhum jogo à espera de resultado (1h30 a 30h depois do início). Nada a fazer.")
            return
        for r in pend:
            log(f"À espera de resultado: {r[0]} {r[7]} - {r[8]} {r[5]} {r[6]}")
        teams = {r[0] for r in pend}
        comps = [c for c in comps if any(t.startswith(c["equipa"]) for t in teams)]
    F = ["team", "comp", "src", "num", "jor", "date", "time", "home", "away", "venue", "status", "res", "phase"]
    games = {(r[0], r[3]): dict(zip(F, r)) for r in data["games"]}
    stand, logos = data["stand"], data.get("logos", {})
    groups = data.setdefault("groups", {})
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    known_teams = set(t["id"] for t in CONFIG["equipas"])
    total_mine = 0

    for ci, comp in enumerate(comps):
        cid = comp["id"]
        log(f"[{round(100 * ci / max(len(comps), 1)):3d}%] {comp['nome']}")
        try:
            phases, tables = competition_phases(cid)
        except Exception as e:  # noqa: BLE001
            log(f"AVISO: falhou a competição {cid} ({comp['nome']}): {e}")
            continue
        log(f"{comp['nome']} ({cid}): {len(phases)} fases/grupos encontrados")
        if not phases:
            log(f"AVISO: nenhuma fase lida em {comp['nome']}; o site pode ter mudado de formato.")
        for pid, title in phases.items():
            try:
                rows = schedule_games(cid, pid)
            except Exception as e:  # noqa: BLE001
                log(f"AVISO: falhou o calendário {cid}/{pid}: {e}")
                continue
            mine = [g for g in rows if is_gaia(g["home"]) or is_gaia(g["away"])]
            if not mine:
                continue
            total_mine += len(mine)
            log(f"  {title}: {len(rows)} jogos no grupo, {len(mine)} do FC Gaia, {sum(1 for g in mine if g['res'])} com resultado")
            phase, group = phase_and_group(title, comp.get("taca"))
            label = f"{comp['nome']} · {group}" if group else comp["nome"]
            ids = set()
            for g in mine:
                gaia = g["home"] if is_gaia(g["home"]) else g["away"]
                tid = team_id(comp["equipa"], gaia, comp.get("ab"))
                if tid not in known_teams:
                    log(f"AVISO: equipa nova do FC Gaia não configurada: {gaia} ({comp['nome']})")
                    continue
                ids.add(tid)
                old = games.get((tid, g["num"]))
                new = dict(team=tid, comp=label, src=comp["fonte"], num=g["num"], jor=g["jor"], date=g["date"],
                           time=g["time"], home=g["home"], away=g["away"], venue=g["venue"], res=g["res"], phase=phase)
                if old and not g["time"] and old.get("time") and old.get("status") == "marcado" and not g["res"]:
                    # marcação confirmada (fpb.pt) que o Centro de Resultados ainda não mostra
                    new.update(date=old["date"], time=old["time"], venue=old["venue"])
                if old and not new["date"] and old.get("date") and not new["res"]:
                    # a fonte deixou de mostrar a data de referência da jornada: mantém a anterior
                    new["date"] = old["date"]
                new["status"] = "realizado" if new["res"] else ("marcado" if new["date"] and new["time"] else "por marcar")
                if old != new:
                    log(("Atualizado" if old else "Novo") + f": {tid} #{g['num']} {g['home']} - {g['away']} {new['date']} {new['time']} {new['res']}")
                games[(tid, g["num"])] = new
            # todos os jogos do grupo (para a vista "Jornadas do grupo")
            if not comp.get("taca"):
                grows = [[g["num"], g["jor"], g["date"], g["time"], g["home"], g["away"], g["res"], g["venue"]] for g in rows]
                for tid in ids:
                    lst = groups.setdefault(tid, [])
                    for k, e in enumerate(lst):
                        if e["phase"] == phase:
                            lst[k] = {"phase": phase, "rows": grows}
                            break
                    else:
                        lst.append({"phase": phase, "rows": grows})
            # classificação: detalhada (com pontos marcados/sofridos); se falhar, o resumo
            if not comp.get("taca"):
                rows_st = None
                try:
                    rows_st, found = standings_full(cid, pid, group)
                    for name, url in found.items():
                        logos.setdefault(name, url)
                except Exception as e:  # noqa: BLE001
                    log(f"AVISO: classificação detalhada {cid}/{pid} falhou: {e}")
                rows_st = rows_st or tables.get(pid)
                if rows_st:
                    for tid in ids:
                        lst = stand.setdefault(tid, [])
                        entry = {"phase": phase, "name": label.replace("1ª Divisão", "").replace("  ", " ").strip(), "rows": rows_st}
                        for k, e in enumerate(lst):
                            if e["phase"] == phase:
                                entry["name"] = e["name"]
                                lst[k] = entry
                                break
                        else:
                            lst.append(entry)
                            log(f"Nova fase na classificação: {tid} {phase}")

    # marcações do calendário do clube (Esperanças sem hora no Centro de Resultados)
    try:
        for c in club_calendar():
            if "esperan" not in c["cat"].lower():
                continue
            for g in games.values():
                if g["team"] == "Esperanças" and not g["res"] and norm(g["home"]).lower() == c["home"].lower() and norm(g["away"]).lower() == c["away"].lower():
                    upd = dict(date=c["date"], time=c["time"], venue=c["venue"] or g["venue"], status="marcado")
                    if any(g[k] != v for k, v in upd.items()):
                        log(f"Marcação: Esperanças {g['home']} - {g['away']} {c['date']} {c['time']}")
                        g.update(upd)
    except Exception as e:  # noqa: BLE001
        log(f"AVISO: falhou o calendário do clube: {e}")

    # resultados do clube no fpb.pt (reserva, caso o Centro de Resultados ainda não os tenha)
    try:
        found, nblocks = club_results(list(games.values()))
        log(f"fpb.pt resultados: {nblocks} datas lidas")
        for g, res in found:
            log(f"Resultado (fpb.pt): {g['team']} {g['home']} {res} {g['away']} {g['date']}")
            g.update(res=res, status="realizado")
            for ph in groups.get(g["team"], []):
                for r in ph["rows"]:
                    if r[0] == g["num"] and not r[6]:
                        r[6] = res
    except Exception as e:  # noqa: BLE001
        log(f"AVISO: falhou a página de resultados do clube: {e}")

    data["games"] = [[g[k] for k in F] for g in sorted(games.values(), key=lambda x: (x["team"], x["date"] or "9", x["num"]))]
    data["stand"], data["logos"], data["groups"] = stand, logos, groups
    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if after != before:
        data["updated"] = datetime.date.today().isoformat()
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        log("Dados atualizados.")
    else:
        log("Sem alterações.")
    log(f"[100%] Concluído. {total_mine} jogos do FC Gaia lidos no Centro de Resultados.")
    if total_mine == 0:
        sys.exit("ERRO: não foi lido nenhum jogo do FC Gaia no Centro de Resultados. O site pode estar a bloquear o acesso ou mudou de formato.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", default="calendario", choices=["calendario", "resultados"])
    main(ap.parse_args().modo)
