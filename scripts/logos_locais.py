"""Guarda uma cópia dos logótipos dos clubes em data/logos/ (mesmo endereço do site).

As imagens da FPB não podem ser usadas para gerar imagens no browser (a app de
estatísticas cria as publicações do Instagram num canvas), por isso ficam aqui.
Resultado: data/logos.json com {nome do clube: "data/logos/<ficheiro>"}.
"""
import hashlib, json, os, sys
import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "data")
PASTA = os.path.join(DADOS, "logos")
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "image/svg+xml": ".svg"}


def main():
    with open(os.path.join(DADOS, "jogos.json"), encoding="utf-8") as f:
        logos = json.load(f).get("logos") or {}
    os.makedirs(PASTA, exist_ok=True)
    mapa_path = os.path.join(DADOS, "logos.json")
    try:
        with open(mapa_path, encoding="utf-8") as f:
            antigo = json.load(f)
    except (OSError, ValueError):
        antigo = {}
    mapa, novos, falhas = {}, 0, 0
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (fcgaia-calendario)"
    for nome, url in sorted(logos.items()):
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        base = hashlib.sha1(url.encode()).hexdigest()[:12]
        feito = [x for x in os.listdir(PASTA) if x.startswith(base + ".")]
        if feito:
            mapa[nome] = "data/logos/" + feito[0]
            continue
        try:
            r = s.get(url, timeout=20)
            tipo = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if r.status_code != 200 or tipo not in EXT or not (200 < len(r.content) < 2_000_000):
                raise ValueError(f"{r.status_code} {tipo} {len(r.content)}B")
            fich = base + EXT[tipo]
            with open(os.path.join(PASTA, fich), "wb") as f:
                f.write(r.content)
            mapa[nome] = "data/logos/" + fich
            novos += 1
        except Exception as e:  # um logótipo em falta não impede o resto
            falhas += 1
            if nome in antigo:
                mapa[nome] = antigo[nome]
            print(f"logo {nome}: {e}", file=sys.stderr)
    with open(mapa_path, "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"logos: {len(mapa)} no mapa, {novos} novos, {falhas} falhas")


if __name__ == "__main__":
    main()
