import json, re, requests
from bs4 import BeautifulSoup
U = "https://www.fpb.pt/wp-admin/admin-ajax.php"
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/128.0"}
def q(**kw):
    d = {"action": "search_player", "epoca": "2026/2027", "escalao": "Sub 14", "genero": "masculino", "nome": "", "idAssociacao": "", "idClube": ""}
    d.update(kw)
    r = requests.post(U, data=d, headers=H, timeout=180)
    try: j = r.json()
    except ValueError: j = r.text
    n = len(j) if isinstance(j, list) else -1
    t = json.dumps(j) if not isinstance(j, str) else j
    ids = set(re.findall(r"/atletas/(\d+)", t.replace("\\/", "/")))
    print(kw, "| tipo", type(j).__name__, "| elementos", n, "| bytes", len(t), "| ids", len(ids), "| tail:", re.sub(r"[A-Za-zÀ-ÿ]{3,}", "x", t[-300:]).replace("\\n", " ")[:300])
for kw in [{}, {"idClube": "119"}, {"page": "2"}, {"pagina": "2"}, {"offset": "1"}, {"nome": "a"}, {"idAssociacao": "1"}, {"limit": "5000"}]:
    try: q(**kw)
    except Exception as e: print(kw, "ERRO", e)
for url in ["https://www.fpb.pt/atletas/", "https://www.fpb.pt/clubes/"]:
    s = BeautifulSoup(requests.get(url, headers=H, timeout=60).text, "html.parser")
    for sel in s.find_all("select"):
        print(url, sel.get("name"), sel.get("id"), [(o.get("value"), o.get_text(strip=True)) for o in sel.find_all("option")][:80])
    js = [x for x in re.findall(r'<script[^>]+src="([^"]+)"', str(s)) if "fpbasquetebol" in x]
    print(url, "scripts:", js)
    for x in js:
        t = requests.get(x, headers=H, timeout=60).text
        for m in re.finditer(r"search_player|search_club|action\s*:\s*['\"]\w+", t):
            print("   ", x.split('/')[-1], t[max(0, m.start()-200):m.end()+400].replace("\n", " "))
