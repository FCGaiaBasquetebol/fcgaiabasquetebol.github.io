"""Guarda uma cópia dos logótipos dos clubes (data/logos/) e das fotos dos atletas que o
site mostra (data/fotos/), no mesmo endereço do site.

As imagens da FPB não podem ser usadas para gerar imagens no browser (a app de
estatísticas cria as publicações do Instagram num canvas), por isso ficam aqui.
Resultado: data/logos.json {clube: ficheiro} e data/fotos.json {id do atleta: ficheiro}.
"""
import hashlib, json, os, sys
import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "data")
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "image/svg+xml": ".svg"}


def main():
    with open(os.path.join(DADOS, "jogos.json"), encoding="utf-8") as f:
        dados = json.load(f)
    copiar(dados.get("logos") or {}, "logos")
    fotos = {}
    for equipa in (dados.get("plantel") or {}).values():
        for a in equipa or []:
            if a.get("photo") and a.get("id"):
                fotos[str(a["id"])] = a["photo"]
    copiar(fotos, "fotos")


def copiar(logos, nome_pasta):
    PASTA = os.path.join(DADOS, nome_pasta)
    os.makedirs(PASTA, exist_ok=True)
    mapa_path = os.path.join(DADOS, nome_pasta + ".json")
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
            mapa[nome] = f"data/{nome_pasta}/" + feito[0]
            continue
        try:
            r = s.get(url, timeout=20)
            tipo = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if r.status_code != 200 or tipo not in EXT or not (200 < len(r.content) < 2_000_000):
                raise ValueError(f"{r.status_code} {tipo} {len(r.content)}B")
            fich = base + EXT[tipo]
            with open(os.path.join(PASTA, fich), "wb") as f:
                f.write(r.content)
            mapa[nome] = f"data/{nome_pasta}/" + fich
            novos += 1
        except Exception as e:  # um logótipo em falta não impede o resto
            falhas += 1
            if nome in antigo:
                mapa[nome] = antigo[nome]
            print(f"{nome_pasta} {nome}: {e}", file=sys.stderr)
    usados = set(mapa.values())
    for x in os.listdir(PASTA):  # ficheiros que já não estão no site (ex.: atleta que saiu) são apagados
        if f"data/{nome_pasta}/{x}" not in usados:
            os.remove(os.path.join(PASTA, x))
    with open(mapa_path, "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"{nome_pasta}: {len(mapa)} no mapa, {novos} novos, {falhas} falhas")


if __name__ == "__main__":
    main()
