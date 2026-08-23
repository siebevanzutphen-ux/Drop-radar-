#!/usr/bin/env python3
"""
DropRadar v2 - schaarstesignalen spotten in domeinen die je nog niet kent.

Verschil met v1: het systeem beweert niet dat iets waardevol is. Het zegt
"dit vertoont de patronen die jij bij Pokemon zou herkennen" en geeft je
meteen de links om het zelf binnen 30 seconden te controleren.

Het systeem selecteert. Jij beslist.
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
# CONFIGURATIE
# ---------------------------------------------------------------------------

STATE_FILE = "seen.json"
MAX_STATE = 3000
MIN_SCORE = 2
MAX_ALERTS_PER_RUN = 12
FEED_TIMEOUT = 20

# Schaarstesignalen, gegroepeerd op sterkte.
# STERK = het soort signaal dat jij bij Pokemon meteen zou herkennen.
STERKE_SIGNALEN = [
    "genummerd", "numbered", "oplage van", "gelimiteerde oplage",
    "limited to", "eerste persing", "first pressing", "eerste druk",
    "one-off", "eenmalig", "nooit meer", "laatste kans",
    "alleen verkrijgbaar bij", "exclusief bij", "exclusive to",
    "collector's edition", "verzamelaarseditie", "jubileumeditie",
]

# ZWAK = wijst erop dat er iets speelt, maar zegt op zichzelf weinig.
ZWAKKE_SIGNALEN = [
    "limited edition", "limited-edition", "gelimiteerd", "gelimiteerde",
    "exclusief", "exclusieve", "exclusive", "special edition",
    "drop", "restock", "pre-order", "preorder", "voorverkoop",
    "release", "lanceert", "uitverkocht", "sold out", "raffle", "loting",
    "samenwerking", "collab", "collaboration", "onthult", "aankondiging",
]

CATEGORIES = {
    "Pokémon / TCG": [
        "pokemon", "pokémon", "pikachu", "charizard", "tcg", "trading card",
        "verzamelkaart", "booster", "elite trainer box", "yu-gi-oh",
        "magic the gathering", "one piece card",
    ],
    "Speelgoed / figures": [
        "funko", "lego", "bearbrick", "be@rbrick", "amiibo", "hot toys",
        "actiefiguur", "action figure", "playmobil", "nendoroid",
    ],
    "Vinyl / muziek": [
        "vinyl", "lp", "plaat", "picture disc", "record store day", "rsd",
        "coloured vinyl", "gekleurd vinyl", "boxset", "box set", "reissue",
        "heruitgave", "persing", "pressing", "platenlabel", "album",
    ],
    "Sneakers / kleding": [
        "sneaker", "sneakers", "nike", "adidas", "jordan", "yeezy",
        "new balance", "dunk", "air max", "asics", "salomon", "supreme",
    ],
    "Kunst / prints / overig": [
        "zeefdruk", "screenprint", "art print", "artprint", "kunstwerk",
        "art toy", "editie", "museum", "galerie", "kunstenaar",
        "munt", "postzegel", "penning",
    ],
    "Lokaal / streekproduct": [
        "brouwerij", "jubileumbier", "speciaalbier", "distilleerderij",
        "streekproduct", "ambachtelijk", "lokale kunstenaar",
    ],
}

# Waar jij fysiek kunt zijn. Deze woorden geven een punt extra.
NL_MARKERS = [
    "nederland", "nederlandse", "dutch",
    "veghel", "uden", "oss", "schijndel", "sint-oedenrode", "erp",
    "den bosch", "'s-hertogenbosch", "eindhoven", "helmond", "tilburg",
    "nijmegen", "noord-brabant", "brabant",
    "intertoys", "bol.com", "blokker", "kruidvat", "hema", "game mania",
    "mediamarkt", "media markt", "coolblue",
]

GOOGLE_NEWS_QUERIES = [
    '"gelimiteerde oplage" Nederland',
    '"genummerde oplage" kunst OR print',
    'eerste persing vinyl Nederland',
    '"limited edition" Brabant OR Eindhoven OR "Den Bosch"',
    'platenzaak eigen persing Nederland',
    'lokale brouwerij gelimiteerd jubileum',
    'museum gelimiteerde editie prent',
    'Pokemon TCG exclusief Nederland',
    'sneaker release exclusief Nederland',
    'zeefdruk oplage kunstenaar Nederland',
]

# Vul aan met bronnen uit jouw eigen wereld: platenzaken, galeries,
# brouwerijen, lokale winkels. Veel sites hebben /feed of /rss.
EXTRA_FEEDS = [
    ("Reddit r/PokemonTCG", "https://www.reddit.com/r/PokemonTCG/new/.rss"),
    ("Reddit r/vinyl", "https://www.reddit.com/r/vinyl/new/.rss"),
    ("Reddit r/Sneakers", "https://www.reddit.com/r/Sneakers/new/.rss"),
    # ("Platenzaak X", "https://voorbeeld.nl/feed"),
]

# Waar je per categorie controleert wat iets echt waard is.
# {q} wordt vervangen door de zoekterm uit de titel.
LOOKUP = {
    "Vinyl / muziek": [
        ("Discogs", "https://www.discogs.com/search/?q={q}&type=release"),
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
    ],
    "Sneakers / kleding": [
        ("StockX", "https://stockx.com/search?s={q}"),
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
    ],
    "Pokémon / TCG": [
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
        ("Cardmarket", "https://www.cardmarket.com/en/Pokemon/Products/Search?searchString={q}"),
    ],
    "_default": [
        ("eBay verkocht", "https://www.ebay.nl/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1"),
        ("Catawiki", "https://www.catawiki.com/nl/q/?q={q}"),
    ],
}

# Woorden die uit de zoekterm gefilterd worden - die vervuilen je lookup.
STOPWOORDEN = {
    "de", "het", "een", "en", "van", "voor", "met", "bij", "op", "in", "te",
    "dit", "die", "deze", "is", "zijn", "wordt", "worden", "komt", "krijgt",
    "nieuwe", "nieuw", "limited", "edition", "gelimiteerde", "gelimiteerd",
    "exclusieve", "exclusief", "the", "and", "for", "with", "new", "now",
    "release", "aankondiging", "onthult", "lanceert", "nederland",
}

USER_AGENT = "Mozilla/5.0 (compatible; DropRadar/2.0; persoonlijke hobbyprojectbot)"

# ---------------------------------------------------------------------------
# HULPFUNCTIES
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


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
        json.dump(
            {"updated": datetime.now(timezone.utc).isoformat(),
             "seen": list(seen)[-MAX_STATE:]},
            f,
            indent=1,
        )


def item_id(entry) -> str:
    raw = entry.get("id") or entry.get("link") or entry.get("title") or ""
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


def google_news_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        f"{urllib.parse.quote(query)}&hl=nl&gl=NL&ceid=NL:nl"
    )


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


def score_item(title: str, summary: str):
    """
    Geeft terug: (score, categorieen, is_nl, gevonden_signalen).

    Een sterk signaal telt dubbel: 'genummerde oplage van 200' zegt veel
    meer dan het marketingwoord 'exclusief'.
    """
    text = normalize(f"{title} {summary}")

    sterk = [w for w in STERKE_SIGNALEN if normalize(w) in text]
    zwak = [w for w in ZWAKKE_SIGNALEN if normalize(w) in text]
    if not sterk and not zwak:
        return 0, [], False, []

    cats, cat_hits = [], 0
    for label, words in CATEGORIES.items():
        hits = sum(1 for w in words if normalize(w) in text)
        if hits:
            cats.append(label)
            cat_hits += hits
    if cat_hits == 0:
        return 0, [], False, []

    is_nl = any(normalize(m) in text for m in NL_MARKERS)
    score = min(len(sterk), 3) * 2 + min(len(zwak), 2) + (1 if is_nl else 0)
    return score, cats, is_nl, (sterk + zwak)[:3]


def zoekterm(title: str) -> str:
    """Haalt de kern uit een titel voor de opzoeklinks."""
    clean = re.sub(r"[^\w\s-]", " ", title)
    clean = re.split(r"\s+-\s+", clean)[0]  # bronnaam achter de streep eraf
    woorden = [w for w in clean.split()
               if normalize(w) not in STOPWOORDEN and len(w) > 2]
    return urllib.parse.quote(" ".join(woorden[:5]))


def build_message(hit) -> str:
    vlag = "🇳🇱 " if hit["is_nl"] else ""
    vuur = "🔥" * min(max(hit["score"] - MIN_SCORE + 1, 1), 3)
    hoofdcat = hit["cats"][0]
    links = LOOKUP.get(hoofdcat, LOOKUP["_default"])
    q = zoekterm(hit["title"])
    check = " · ".join(f'<a href="{u.format(q=q)}">{naam}</a>'
                       for naam, u in links)

    return (
        f"{vlag}{vuur} <b>{hit['title'][:180]}</b>\n"
        f"{' · '.join(hit['cats'])}\n"
        f"<i>Signaal: {', '.join(hit['signalen'])}</i>\n\n"
        f"🔎 Check zelf: {check}\n"
        f"{hit['link']}"
    )


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
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
# MAIN
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    dry_run = "--dry-run" in sys.argv

    if not dry_run and (not token or not chat_id):
        print("FOUT: TELEGRAM_TOKEN of TELEGRAM_CHAT_ID ontbreekt.")
        return 1

    seen = load_state()
    first_run = len(seen) == 0
    if first_run:
        print("[info] eerste run: basislijn opbouwen, geen meldingen.")

    sources = [(f"Google News: {q}", google_news_url(q))
               for q in GOOGLE_NEWS_QUERIES] + EXTRA_FEEDS

    hits, checked = [], 0
    for name, url in sources:
        entries = fetch_feed(name, url)
        checked += len(entries)
        for entry in entries:
            uid = item_id(entry)
            if uid in seen:
                continue
            seen.add(uid)

            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            score, cats, is_nl, signalen = score_item(title, summary)
            if score >= MIN_SCORE:
                hits.append({
                    "title": title, "link": entry.get("link", ""),
                    "score": score, "cats": cats, "is_nl": is_nl,
                    "signalen": signalen,
                })
        time.sleep(1)

    hits.sort(key=lambda h: h["score"], reverse=True)
    print(f"[info] {checked} items bekeken, {len(hits)} treffers.")

    if first_run:
        save_state(seen)
        return 0

    for hit in hits[:MAX_ALERTS_PER_RUN]:
        msg = build_message(hit)
        if dry_run:
            print("---\n" + msg)
        else:
            send_telegram(token, chat_id, msg)
            time.sleep(0.5)

    if len(hits) > MAX_ALERTS_PER_RUN and not dry_run:
        send_telegram(token, chat_id,
                      f"… en nog {len(hits) - MAX_ALERTS_PER_RUN} treffers. "
                      "Overweeg MIN_SCORE te verhogen.")

    save_state(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
