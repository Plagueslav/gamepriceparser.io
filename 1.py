import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "game-price-compare/1.0 (personal use)"}
TIMEOUT = 10
DELAY = 1.0          # пауза между запросами к steampay, сек1
MAX_STEAMPAY = 60    # максимум страниц steampay за один запуск

STEAMBUY = "https://steambuy.com"
STEAMPAY = "https://steampay.com"
BASE_DIR = Path(__file__).parent
SITEMAP_CACHE = BASE_DIR / "steampay_sitemap.xml"
EXTRA_URLS_FILE = BASE_DIR / "steampay_extra.txt"   # по одной ссылке в строке, необязательно

CATEGORY_LABELS = {"games": "игра", "dlc": "DLC", "collection": "коллекция"}


# ---------- steambuy ----------

def steambuy_search(query):
    r = requests.get(f"{STEAMBUY}/sb/api/catalog",
                     params={"q": query, "page": 1, "view": "detail"},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    items = []
    for item in r.json().get("items", []):
        url = item.get("url", "")
        items.append({
            "site": "steambuy",
            "id": str(item.get("id_good") or item.get("id")),
            "title": item.get("title", ""),
            "category": CATEGORY_LABELS.get(item.get("category", ""), "не определено"),
            "requires": "",
            "price": item.get("price"),
            "url": STEAMBUY + url if url.startswith("/") else url,
        })
    return items


def steambuy_in_stock(page_html, item_id):
    soup = BeautifulSoup(page_html, "html.parser")
    for a in soup.select("a.product-price__btn_buy, a.product-addition__buy-btn2"):
        if re.search(rf"/gcart/\?id={re.escape(item_id)}(?!\d)", a.get("href", "")):
            return True
    return False


def steambuy_stock(item):
    try:
        html = requests.get(item["url"], headers=HEADERS, timeout=TIMEOUT).text
        item["in_stock"] = steambuy_in_stock(html, item["id"])
    except requests.RequestException:
        item["in_stock"] = None
    return item


def search_steambuy(query):
    items = steambuy_search(query)
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(steambuy_stock, items))


# ---------- steampay ----------

def load_steampay_urls():
    if not SITEMAP_CACHE.exists():
        r = requests.get(f"{STEAMPAY}/sitemap.xml", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        SITEMAP_CACHE.write_bytes(r.content)
    xml = SITEMAP_CACHE.read_text(encoding="utf-8")
    urls = re.findall(r"<loc>(https://steampay\.com/game/[^<]+)</loc>", xml)
    if EXTRA_URLS_FILE.exists():
        for line in EXTRA_URLS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("https://steampay.com/game/") and line not in urls:
                urls.append(line)
    return urls


def steampay_matches(query, urls):
    tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]
    result = []
    for url in urls:
        slug_tokens = set(re.split(r"[^a-z0-9]+", url.rsplit("/", 1)[-1].lower()))
        if all(t in slug_tokens for t in tokens):
            result.append(url)
    return result[:MAX_STEAMPAY]


def parse_steampay_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else ""

    price_meta = soup.select_one('meta[itemprop="price"]')
    price = int(float(price_meta["content"])) if price_meta and price_meta.get("content") else None

    not_available = soup.select_one(".product__advantages-item--not-available") is not None
    in_stock = (not not_available) and bool(price)

    category, requires = "игра", ""
    addon = soup.select_one("p.product-addon")
    if addon and "Дополнение" in addon.get_text():
        category = "DLC"
        base = addon.select_one("a[href*='/game/']")
        if base:
            requires = base.get_text(strip=True)

    return {"site": "steampay", "title": title, "category": category,
            "requires": requires, "price": price, "url": url, "in_stock": in_stock}


def fetch_steampay(url):
    try:
        html = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
        return parse_steampay_page(html, url)
    except requests.RequestException:
        return {"site": "steampay", "title": url, "category": "не определено",
                "requires": "", "price": None, "url": url, "in_stock": None}
    finally:
        time.sleep(DELAY)


def search_steampay(query):
    return [fetch_steampay(url) for url in steampay_matches(query, load_steampay_urls())]


# ---------- общий вывод ----------

def search_all(query):
    rows = search_steambuy(query) + search_steampay(query)
    return sorted(rows, key=lambda r: (r["price"] is None, r["price"] or 0))


def format_status(in_stock):
    return {True: "в наличии", False: "нет в наличии", None: "не проверено"}[in_stock]


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or input("Название игры: ").strip()
    for r in search_all(query):
        price = f'{r["price"]:>7} RUB' if r["price"] is not None else "   нет цены"
        print(f'{price}  {r["site"]:<9} {r["category"]:<13} {r["title"]}{req}  [{format_status(r["in_stock"])}]')