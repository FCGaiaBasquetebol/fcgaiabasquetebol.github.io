#!/usr/bin/env python3
"""Gera os calendários para subscrever: _site/ics/<equipa>.ics e _site/ics/todos.ics."""
import json, re, sys, unicodedata, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "_site") / "ics"
NOMES = {"Seniores Masc": "Seniores", "Esperanças": "Esperanças", "Sub-18 Masc A": "Sub 18 A", "Sub-18 Masc B": "Sub 18 B",
         "Sub-16 Masc A": "Sub 16 A", "Sub-16 Masc B": "Sub 16 B", "Sub-14 Masc A": "Sub 14 A", "Sub-14 Masc B": "Sub 14 B", "Masters": "Masters"}
TZ = ["BEGIN:VTIMEZONE", "TZID:Europe/Lisbon", "BEGIN:STANDARD", "DTSTART:19701025T020000", "TZOFFSETFROM:+0100",
      "TZOFFSETTO:+0000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD", "BEGIN:DAYLIGHT",
      "DTSTART:19700329T010000", "TZOFFSETFROM:+0000", "TZOFFSETTO:+0100", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
      "END:DAYLIGHT", "END:VTIMEZONE"]


def slug(s):
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def esc(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    b = line.encode("utf-8")
    if len(b) <= 74:
        return line
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 73:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    return "\r\n".join(out + [cur])


def cal(games, name):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FC Gaia Basquetebol//Jogos//PT", "CALSCALE:GREGORIAN",
         "METHOD:PUBLISH", "X-WR-CALNAME:" + esc(name), "X-WR-TIMEZONE:Europe/Lisbon",
         "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"] + TZ
    for g in games:
        team, comp, _src, num, jor, date, hora, home, away, venue, status, res, _ph = g
        if not date:
            continue
        pend = not hora and not res
        venue = "Pavilhão por definir" if pend or not venue else venue
        d = datetime.date.fromisoformat(date)
        L += ["BEGIN:VEVENT", f"UID:fcgaia-{slug(team)}-{num}@fcgaia-basquetebol", "DTSTAMP:" + now]
        if hora:
            s = datetime.datetime.fromisoformat(f"{date}T{hora[:5]}")
            e = s + datetime.timedelta(minutes=120)
            L += ["DTSTART;TZID=Europe/Lisbon:" + s.strftime("%Y%m%dT%H%M00"), "DTEND;TZID=Europe/Lisbon:" + e.strftime("%Y%m%dT%H%M00")]
        else:
            L += ["DTSTART;VALUE=DATE:" + d.strftime("%Y%m%d"), "DTEND;VALUE=DATE:" + (d + datetime.timedelta(days=1)).strftime("%Y%m%d")]
        title = f"{NOMES.get(team, team)} · {home} vs {away}" + (f" ({res})" if res else "")
        desc = f"{comp}" + (f" · Jornada {jor}" if jor else "") + f"\nJogo n.º {num}" + ("" if hora else "\nHora ainda por marcar (data de referência da jornada).")
        loc = [ "LOCATION:" + esc(venue)]
        if re.search(r"(?i)futebol clube gaia|pav\.? fc ?gaia", venue):
            loc = ["LOCATION:" + esc("Pavilhão Futebol Clube Gaia, Rua Fialho de Almeida 34, 4400-150 Vila Nova de Gaia"),
                   "GEO:41.1280245;-8.6085688"]
        L += ["SUMMARY:" + esc(title)] + loc + ["DESCRIPTION:" + esc(desc), "END:VEVENT"]
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in L) + "\r\n"


def main():
    data = json.loads((ROOT / "data" / "jogos.json").read_text(encoding="utf-8"))
    games = data["games"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "todos.ics").write_text(cal(games, "FC Gaia Basquetebol"), encoding="utf-8")
    for team in sorted({g[0] for g in games}):
        (OUT / f"{slug(team)}.ics").write_text(cal([g for g in games if g[0] == team], "FC Gaia · " + NOMES.get(team, team)), encoding="utf-8")
    print(f"{len(list(OUT.glob('*.ics')))} calendários gerados em {OUT}")


if __name__ == "__main__":
    main()
