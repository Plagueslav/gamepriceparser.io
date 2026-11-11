import difflib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "game-price-compare/1.0 (personal use)"}
TIMEOUT = 10
DELAY = 1.0          # пауза между запросами к steampay, сек
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
    """Товар в наличии, если на странице есть кнопка «Купить сейчас» для него.

    Два варианта разметки:
    - основная кнопка товара: a.product-price__btn_buy с data-id равным id товара
      (ссылка href="#", её обрабатывает скрипт);
    - кнопка в блоке дополнений: ссылка /gcart/?id=<id товара>.
    """
    soup = BeautifulSoup(page_html, "html.parser")
    gcart = re.compile(rf"/gcart/\?id={re.escape(item_id)}(?!\d)")
    for a in soup.select("a.product-price__btn_buy, a.product-addition__buy-btn2"):
        if a.get("data-id") == item_id or gcart.search(a.get("href", "")):
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


# ---------- steam ----------

STEAM = "https://store.steampowered.com"


def parse_steam_search(html):
    """Разбирает HTML страницы поиска Steam: цена в рублях и статус продажи."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for a in soup.select("a.search_result_row"):
        title_el = a.select_one(".title")
        title = title_el.get_text(strip=True) if title_el else ""
        pc = a.select_one(".search_price_discount_combined")
        final = int(pc.get("data-price-final") or 0) if pc else 0
        text = pc.get_text(" ", strip=True) if pc else ""

        if final > 0:
            price, status, tag = final // 100, "в продаже", "in"
        elif "Бесплатно" in text:
            price, status, tag = 0, "бесплатно", "in"
        else:
            price, status, tag = None, "нет цены", "unknown"

        rows.append({
            "site": "steam",
            "title": title,
            "category": "",
            "requires": "",
            "price": price,
            "url": a.get("href", ""),
            "in_stock": None,
            "status": status,
            "status_tag": tag,
        })
    return rows


def split_words(text):
    """Слова без апострофов: «Baldur's» и «baldurs» дают одно и то же."""
    text = text.lower().replace("'", "").replace("\u2019", "")
    return [w for w in re.split(r"[^a-z0-9а-яё]+", text) if w]


def word_matches(query_word, title_words):
    """Слово совпадает точно или с небольшой опечаткой (только для слов от 4 букв)."""
    if query_word in title_words:
        return True
    if len(query_word) < 4:
        return False
    return bool(difflib.get_close_matches(query_word, title_words, n=1, cutoff=0.75))


def title_matches(query, title):
    """Каждое слово запроса должно найтись в названии, возможно с опечаткой."""
    title_words = split_words(title)
    return all(word_matches(w, title_words) for w in split_words(query))


STEAM_CATEGORIES = [("998", "игра"), ("21", "DLC")]   # 998 — игры, 21 — доп. контент


def steam_unavailable_status(html):
    """Определяет, что Steam не может показать товар.

    Возвращает «недоступно в регионе», «недоступно» или None (товар доступен).
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if "недоступен в вашем регионе" in text:
        return "недоступно в регионе"
    title = soup.title.get_text(strip=True) if soup.title else ""
    if "Ошибка веб-страницы" in title or "Ой, извините" in text:
        return "недоступно"
    return None


def check_steam_row(row):
    """Проверяет страницу товара и ставит статус, если Steam его не показывает."""
    try:
        html = requests.get(row["url"], headers=HEADERS, timeout=TIMEOUT).text
        status = steam_unavailable_status(html)
        if status:
            row["status"], row["status_tag"] = status, "out"
    except requests.RequestException:
        pass
    time.sleep(0.5)
    return row


def search_steam(query):
    """Ищет игры и доп. контент отдельно: сначала игры, затем DLC."""
    rows = []
    for category_id, label in STEAM_CATEGORIES:
        r = requests.get(f"{STEAM}/search",
                         params={"term": query, "hwtype": 0, "supportedlang": "russian",
                                 "category1": category_id, "ndl": 1,
                                 "cc": "ru", "l": "russian"},
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for row in parse_steam_search(r.text):
            if title_matches(query, row["title"]):
                row["category"] = label
                rows.append(row)
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(check_steam_row, rows))


# ---------- ggsel ----------

GGSEL = "https://ggsel.net"
GGSEL_API = "https://api.ggsel.com/elastic/goods/query"
GGSEL_CONTENT_TYPES = [2, 48, 19]     # 2 — ключи, 48 — гифты, 19 — DLC
GGSEL_PLATFORMS = ["Steam"]
GGSEL_LIMIT = 60
GGSEL_MAX_PAGES = 10                  # до 600 товаров за один запуск


def ggsel_items_to_rows(items):
    rows = []
    for it in items:
        url = it.get("url", "")
        rows.append({
            "site": "ggsel",
            "title": it.get("name", ""),
            "category": "",
            "requires": "",
            "price": int(float(it.get("price_brl") or 0)),
            "sales": int(it.get("cnt_sell") or 0),
            "url": f"{GGSEL}/catalog/product/{url}",
            "in_stock": True,   # на ggsel показываются только товары в наличии
        })
    return rows


def search_ggsel(query):
    """Постраничный поиск по API ggsel с фильтрами: Steam, ключи, гифты, DLC."""
    rows, search_after = [], []
    headers = {**HEADERS, "Content-Type": "application/json", "Accept": "application/json"}
    for _ in range(GGSEL_MAX_PAGES):
        body = {
            "search_term": query,
            "limit": GGSEL_LIMIT,
            "search_after": search_after,
            "is_preorders": False,
            "with_filters": True,
            "with_categories": False,
            "sort": "sortByRec",
            "content_type_ids": GGSEL_CONTENT_TYPES,
            "with_forbidden": False,
            "min_price": "",
            "max_price": "",
            "currency": "wmr",
            "lang": "ru",
            "platforms": GGSEL_PLATFORMS,
        }
        r = requests.post(GGSEL_API, json=body, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        items = (r.json().get("data") or {}).get("items") or []
        if not items:
            break
        rows.extend(ggsel_items_to_rows(items))
        if len(items) < GGSEL_LIMIT:
            break
        search_after = items[-1].get("sort") or []
        if not search_after:
            break
        time.sleep(0.5)
    return [row for row in rows if title_matches(query, row["title"])]


# ---------- gabestore ----------

GABESTORE = "https://gabestore.ru"


def parse_gabestore_search(html):
    """Карточки товаров на странице поиска gabestore."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select("div.shop-item"):
        name_el = card.select_one(".shop-item__name")
        price_el = card.select_one(".shop-item__price-current")
        btn = card.select_one("a.btn")
        if not name_el or not price_el:
            continue
        price_digits = re.sub(r"\D", "", price_el.get_text())
        in_stock = bool(btn) and "btn--empty-item" not in btn.get("class", [])
        rows.append({
            "site": "gabestore",
            "title": name_el.get_text(strip=True),
            "category": "",
            "requires": "",
            "price": int(price_digits) if price_digits else None,
            "url": GABESTORE + name_el.get("href", ""),
            "in_stock": in_stock,
        })
    return rows


def search_gabestore(query):
    r = requests.get(f"{GABESTORE}/result",
                     params={"ProductFilter[search]": query},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [row for row in parse_gabestore_search(r.text) if title_matches(query, row["title"])]


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
    result = []
    for url in urls:
        slug_words = split_words(url.rsplit("/", 1)[-1].replace("-", " "))
        if all(word_matches(w, slug_words) for w in split_words(query)):
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
        return {"site": "steampay", "title": url, "category": "",
                "requires": "", "price": None, "url": url, "in_stock": None}
    finally:
        time.sleep(DELAY)


def search_steampay(query):
    return [fetch_steampay(url) for url in steampay_matches(query, load_steampay_urls())]


# ---------- общий вывод ----------

def search_all(query):
    rows = (search_steam(query) + search_steambuy(query) + search_ggsel(query)
            + search_gabestore(query) + search_steampay(query))
    return sorted(rows, key=lambda r: (r["price"] is None, r["price"] or 0))


def format_status(in_stock):
    return {True: "в наличии", False: "нет в наличии", None: "не проверено"}[in_stock]


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or input("Название игры: ").strip()
    for r in search_all(query):
        price = f'{r["price"]:>7} RUB' if r["price"] is not None else "   нет цены"
        print(f'{price}  {r["site"]:<9} {r["category"]:<13} {r["title"]}  [{format_status(r["in_stock"])}]')