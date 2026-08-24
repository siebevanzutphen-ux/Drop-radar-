#!/usr/bin/env python3
"""
DropRadar v3 - Nederlandse meldingen over schaarse spullen.

Wat er anders is dan v2:
- Reddit eruit. Die feeds waren Engels en overstemden alle andere bronnen.
- Meldingen in gewone taal: wat is het, waar, hoeveel, waarom gemeld.
- Winkelnaam en oplagegrootte worden uit de tekst gehaald als ze er staan.

Wat het NIET kan: als het nieuwsbericht niet vermeldt waar iets te koop is
of wat het kost, weet het script dat ook niet. Dan staat er eerlijk
"niet vermeld" en moet je het artikel zelf openen.
"""

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone

import feedparser
import requests

# ---------------------------------------------------------------------------
# INSTELLINGEN
# ---------------------------------------------------------------------------

STATE_FILE = "seen.json"
MAX_STATE = 3000
MIN_SCORE = 2
MAX_ALERTS_PER_RUN = 10
FEED_TIMEOUT = 20

# Sterke signalen: harde feiten over schaarste.
STERKE_SIGNALEN = {
    "genummerd": "de stuks zijn genummerd",
    "oplage van": "er is een vaste oplage genoemd",
    "gelimiteerde oplage": "er worden er maar een beperkt aantal gemaakt",
    "eerste persing": "eerste persing (bij vinyl vaak het meest gewild)",
    "eerste druk": "eerste druk",
    "eenmalig": "komt eenmalig uit",
    "alleen verkrijgbaar bij": "maar bij een winkel te koop",
    "exclusief bij": "maar bij een winkel te koop",
    "collector's edition": "speciale verzamelaarsuitgave",
    "verzamelaarseditie": "speciale verzamelaarsuitgave",
    "jubileumeditie": "eenmalige jubileumuitgave",
    "gesigneerd": "gesigneerd door de maker",
}

# Zwakke signalen: wijst op iets, maar is vaak gewoon marketing.
ZWAKKE_SIGNALEN = {
    "limited edition": "aangekondigd als limited edition",
    "gelimiteerd": "aangekondigd als gelimiteerd",
    "exclusief": "aangekondigd als exclusief",
    "special edition": "speciale uitgave",
    "uitverkocht": "was snel uitverkocht",
    "sold out": "was snel uitverkocht",
    "samenwerking": "samenwerking tussen twee merken",
    "collab": "samenwerking tussen twee merken",
    "pre-order": "nu al te reserveren",
    "voorverkoop": "nu al te reserveren",
    "raffle": "je moet loten om te mogen kopen",
    "loting": "je moet loten om te mogen kopen",
}

CATEGORIES = {
    "Pokémon en verzamelkaarten": [
        "pokemon", "pokémon", "pikachu", "charizard", "verzamelkaart",
        "trading card", "booster", "yu-gi-oh", "magic the gathering",
    ],
    "Speelgoed en figuren": [
        "funko", "lego", "bearbrick", "amiibo", "actiefiguur", "playmobil",
    ],
    "Vinyl en muziek": [
        "vinyl", "elpee", "picture disc", "record store day",
        "persing", "pressing", "platenlabel", "single", "album",
    ],
    "Sneakers en kleding": [
        "sneaker", "sneakers", "nike", "adidas", "jordan", "new balance",
        "air max", "asics", "supreme",
    ],
    "Kunst en prints": [
        "zeefdruk", "art print", "kunstwerk", "galerie", "kunstenaar",
        "museum", "prent", "litho", "ets",
    ],
    "Munten en postzegels": [
        "munt", "postzegel", "penning", "koninklijke nederlandse munt",
    ],
    "Lokaal en streekproducten": [
        "brouwerij", "jubileumbier", "speciaalbier", "distilleerderij",
        "streekproduct", "ambachtelijk",
    ],
}

# Winkels en plekken. Als een van deze in de tekst staat, weten we waar.
WINKELS = [
    "Intertoys", "Bol.com", "Blokker", "Kruidvat", "HEMA", "Action",
    "Albert Heijn", "Jumbo", "Game Mania", "MediaMarkt", "Coolblue",
    "Bruna", "Primera", "Etos", "Praxis", "Gamma", "Lidl", "Aldi",
    "Rijksmuseum", "Van Gogh Museum", "Efteling", "Nike Store",
    "Snipes", "Foot Locker", "JD Sports", "Sneakerdistrict",
]

REGIO = [
    "Veghel", "Uden", "Oss", "Schijndel", "Sint-Oedenrode", "Erp",
    "Den Bosch", "'s-Hertogenbosch", "Eindhoven", "Helmond", "Tilburg",
    "Nijmegen", "Brabant",
]

# Nederlandse zoekopdrachten. Elke regel is een aparte bron.
GOOGLE_NEWS_QUERIES = [
    '"gelimiteerde oplage" Nederland',
    '"limited edition" Nederland winkel',
    'genummerde oplage kunstenaar',
    'eerste persing vinyl Nederland',
    'platenzaak gelimiteerde plaat',
    'museum gelimiteerde editie',
    'Pokemon kaarten exclusief Nederland',
    'sneaker release Nederland exclusief',
    'brouwerij jubileumbier gelimiteerd',
    'zeefdruk oplage gesigneerd',
    'Koninklijke Nederlandse Munt speciale munt',
    'verzamelaars zeldzaam Nederland uitverkocht',
]

# Eigen bronnen. Reddit staat er bewust uit: te veel, te Engels.
# Zet hier platenzaken, galeries en winkels neer die jij volgt.
EXTRA_FEEDS = [
    # ("Platenzaak X", "https://voorbeeld.nl/feed"),
]

LOOKUP = {
    "Vinyl en muziek": [
        ("Discogs", "https://www.discogs.com/search/?q={q}&type=release"),
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
    ],
    "Sneakers en kleding": [
        ("StockX", "https://stockx.com/search?s={q}"),
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
    ],
    "Pokémon en verzamelkaarten": [
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
        ("Cardmarket", "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={q}"),
    ],
    "_default": [
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
        ("Catawiki", "https://www.catawiki.com/nl/q/?q={q}"),
    ],
}

STOPWOORDEN = {
    "de", "het", "een", "en", "van", "voor", "met", "bij", "op", "in", "te",
    "dit", "die", "deze", "is", "zijn", "wordt", "worden", "komt", "krijgt",
    "nieuwe", "nieuw", "limited", "edition", "gelimiteerde", "gelimiteerd",
    "exclusieve", "exclusief", "the", "and", "for", "with", "new", "now",
    "release", "aankondiging", "onthult", "lanceert", "nederland", "brengt",
    "uit", "naar", "over", "aan",
}

USER_AGENT = "Mozilla/5.0 (compatible; DropRadar/3.0; persoonlijke hobbyprojectbot)"

# ---------------------------------------------------------------------------
# HULPFUNCTIES
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").replace("&nbsp;", " ")


def load_state() -> set:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen", []))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[waarschuwing] kon {STATE_FILE} niet lezen: {exc}")
        return set()


def save_state(seen: set) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(),
                   "seen": list(seen)[-MAX_STATE:]}, f, indent=1)


def item_id(entry) -> str:
    raw = entry.get("id") or entry.get("link") or entry.get("title") or ""
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def google_news_url(query: str) -> str:
    return ("https://news.google.com/rss/search?q="
            f"{urllib.parse.quote(query)}&hl=nl&gl=NL&ceid=NL:nl")


def fetch_feed(name: str, url: str):
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=FEED_TIMEOUT)
        if resp.status_code != 200:
            print(f"[skip] {name}: HTTP {resp.status_code}")
            return []
        return feedparser.parse(resp.content).entries or []
    except Exception as exc:  # noqa: BLE001
        print(f"[skip] {name}: {type(exc).__name__}: {exc}")
        return []


def vind_winkel(tekst: str):
    """Zoekt naar een bekende winkel of plaats in de tekst."""
    laag = normalize(tekst)
    winkels = [w for w in WINKELS if normalize(w) in laag]
    plaatsen = [p for p in REGIO if normalize(p) in laag]
    return winkels[:2], plaatsen[:2]


def vind_oplage(tekst: str):
    """Haalt het aantal stuks uit de tekst, als dat genoemd wordt."""
    patronen = [
        r"oplage van (?:slechts )?(\d[\d.]*)",
        r"(\d[\d.]*)\s*(?:genummerde )?exemplaren",
        r"(\d[\d.]*)\s*stuks",
        r"limited to (\d[\d.]*)",
        r"slechts (\d[\d.]*)",
    ]
    for p in patronen:
        m = re.search(p, tekst, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def score_item(titel: str, samenvatting: str):
    """Geeft (score, categorieen, redenen) terug."""
    tekst = normalize(f"{titel} {samenvatting}")

    sterk = [uitleg for woord, uitleg in STERKE_SIGNALEN.items()
             if normalize(woord) in tekst]
    zwak = [uitleg for woord, uitleg in ZWAKKE_SIGNALEN.items()
            if normalize(woord) in tekst]
    if not sterk and not zwak:
        return 0, [], []

    cats, treffers = [], 0
    for label, woorden in CATEGORIES.items():
        n = sum(1 for w in woorden if normalize(w) in tekst)
        if n:
            cats.append(label)
            treffers += n
    if treffers == 0:
        return 0, [], []

    score = min(len(sterk), 3) * 2 + min(len(zwak), 2)
    # dubbele uitleg eruit, sterke redenen eerst
    redenen = list(dict.fromkeys(sterk + zwak))[:3]
    return score, cats, redenen


def zoekterm(titel: str) -> str:
    schoon = re.sub(r"[^\w\s-]", " ", titel)
    schoon = re.split(r"\s+-\s+", schoon)[0]
    woorden = [w for w in schoon.split()
               if normalize(w) not in STOPWOORDEN and len(w) > 2]
    return urllib.parse.quote(" ".join(woorden[:5]))


def bouw_bericht(hit) -> str:
    """Bericht in gewone taal: wat, waar, hoeveel, waarom, wat nu."""
    cat = hit["cats"][0]
    links = LOOKUP.get(cat, LOOKUP["_default"])
    q = zoekterm(hit["titel"])
    check = " · ".join(f'<a href="{u.format(q=q)}">{naam}</a>'
                       for naam, u in links)

    regels = [f"📦 <b>{hit['titel'][:200]}</b>", ""]
    regels.append(f"<b>Categorie:</b> {cat}")

    if hit["winkels"]:
        regels.append(f"<b>Te koop bij:</b> {', '.join(hit['winkels'])}")
    else:
        regels.append("<b>Te koop bij:</b> staat niet in het bericht — "
                      "open het artikel")

    if hit["plaatsen"]:
        regels.append(f"<b>Regio:</b> {', '.join(hit['plaatsen'])} (bij jou in de buurt)")

    if hit["oplage"]:
        regels.append(f"<b>Hoeveel er zijn:</b> {hit['oplage']} stuks")

    regels.append("")
    regels.append("<b>Waarom je dit ziet:</b>")
    for reden in hit["redenen"]:
        regels.append(f"• {reden}")

    regels.append("")
    regels.append(f"📰 <a href=\"{hit['link']}\">Lees het hele bericht</a>")
    regels.append(f"💰 Wat is zoiets waard? {check}")
    regels.append("")
    regels.append("<i>Check eerst de verkoopprijzen voordat je iets koopt. "
                  "Zeldzaam betekent niet automatisch waardevol.</i>")

    return "\n".join(regels)


def stuur_telegram(token: str, chat_id: str, tekst: str) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": tekst, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[telegram] mislukt: {resp.status_code} {resp.text[:200]}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] fout: {exc}")
        return False


# ---------------------------------------------------------------------------
# HOOFDPROGRAMMA
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    dry_run = "--dry-run" in sys.argv

    if not dry_run and (not token or not chat_id):
        print("FOUT: TELEGRAM_TOKEN of TELEGRAM_CHAT_ID ontbreekt.")
        return 1

    seen = load_state()
    eerste_run = len(seen) == 0

    bronnen = [(f"Nieuws: {q}", google_news_url(q))
               for q in GOOGLE_NEWS_QUERIES] + EXTRA_FEEDS

    treffers, bekeken = [], 0
    for naam, url in bronnen:
        items = fetch_feed(naam, url)
        bekeken += len(items)
        for item in items:
            uid = item_id(item)
            if uid in seen:
                continue
            seen.add(uid)

            titel = strip_html(item.get("title", ""))
            samenvatting = strip_html(item.get("summary", "")
                                      or item.get("description", ""))
            score, cats, redenen = score_item(titel, samenvatting)
            if score < MIN_SCORE:
                continue

            volle_tekst = f"{titel} {samenvatting}"
            winkels, plaatsen = vind_winkel(volle_tekst)
            treffers.append({
                "titel": titel,
                "link": item.get("link", ""),
                "score": score,
                "cats": cats,
                "redenen": redenen,
                "winkels": winkels,
                "plaatsen": plaatsen,
                "oplage": vind_oplage(volle_tekst),
            })
        time.sleep(1)

    treffers.sort(key=lambda h: h["score"], reverse=True)
    print(f"[info] {bekeken} items bekeken, {len(treffers)} treffers.")

    if eerste_run:
        print("[info] eerste run: alleen geheugen opbouwen, geen meldingen.")
        save_state(seen)
        return 0

    for hit in treffers[:MAX_ALERTS_PER_RUN]:
        bericht = bouw_bericht(hit)
        if dry_run:
            print("=" * 50)
            print(bericht)
        else:
            stuur_telegram(token, chat_id, bericht)
            time.sleep(0.5)

    save_state(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
