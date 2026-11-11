import re
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

BASE = "https://steambuy.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIMEOUT = 10


def search_steambuy(query):
    """Товары по запросу из JSON API."""
    r = requests.get(f"{BASE}/sb/api/catalog",
                     params={"q": query, "page": 1, "view": "detail"},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    items = []
    for item in r.json().get("items", []):
        url = item.get("url", "")
        items.append({
            "id": str(item.get("id_good") or item.get("id")),
            "title": item.get("title", ""),
            "category": item.get("category", ""),
            "price": item.get("price"),              # RUB
            "url": BASE + url if url.startswith("/") else url,
        })
    return items


def is_in_stock(page_html, item_id):
    """Есть ли на странице кнопка «Купить сейчас» для этого товара."""
    soup = BeautifulSoup(page_html, "html.parser")
    for a in soup.select("a.product-price__btn_buy, a.product-addition__buy-btn2"):
        if re.search(rf"/gcart/\?id={re.escape(item_id)}(?!\d)", a.get("href", "")):
            return True
    return False


def fetch_stock(item):
    try:
        html = requests.get(item["url"], headers=HEADERS, timeout=TIMEOUT).text
        item["in_stock"] = is_in_stock(html, item["id"])
    except requests.RequestException:
        item["in_stock"] = None
    return item


def search_with_stock(query):
    items = search_steambuy(query)
    with ThreadPoolExecutor(max_workers=8) as ex:
        return sorted(ex.map(fetch_stock, items), key=lambda x: x["price"] or 0)


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) or input("Название игры: ").strip()
    for r in search_with_stock(query):
        status = {True: "в наличии", False: "нет в наличии", None: "не проверено"}[r["in_stock"]]
        print(f'{r["price"]:>7} RUB  {r["category"]:<10} {r["title"]}  [{status}]')