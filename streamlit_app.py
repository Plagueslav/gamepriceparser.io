import concurrent.futures as cf
import html
import json
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components

import price_compare as pc

st.set_page_config(page_title="Сравнение цен на игры", layout="wide")

# скрываем значки ссылок у заголовков Streamlit (#steam и т.п.)
st.markdown(
    """
    <style>
    [data-testid="stHeaderActionElements"] { display: none; }
    </style>
    """,
    unsafe_allow_html=True,
)

SITES = [
    ("steam", "Steam"),
    ("steambuy", "steambuy"),
    ("ggsel", "ggsel"),
    ("gabestore", "gabestore"),
    ("steampay", "steampay"),
]

HOME_URLS = {
    "steam": "https://store.steampowered.com/",
    "steambuy": "https://steambuy.com/",
    "ggsel": "https://ggsel.net/en",
    "gabestore": "https://gabestore.ru/",
    "steampay": "https://steampay.com/",
}

CATEGORY_ORDER = {"игра": 0, "коллекция": 1, "DLC": 2}
COLUMN_HEIGHT = 650   # высота колонки в пикселях, дальше прокрутка внутри неё


def store_url(key, query):
    """Страница поиска магазина по запросу; если запрос пустой, — домашняя страница."""
    query = query.strip()
    if not query:
        return HOME_URLS[key]
    q = quote(query)
    return {
        "steam": f"https://store.steampowered.com/search/?term={q}",
        "steambuy": f"https://steambuy.com/catalog/?q={q}",
        "ggsel": f"https://ggsel.net/search/{q}",
        "gabestore": f"https://gabestore.ru/result?ProductFilter%5Bsearch%5D={q}",
        "steampay": f"https://steampay.com/search?q={q}",
    }[key]


def copy_button(text):
    """Кнопка «копировать ссылку». Адрес не выводится на экран, только копируется."""
    components.html(
        f"""
        <button id="copy-btn" style="cursor:pointer;padding:5px 12px;border-radius:6px;
            border:1px solid #666;background:#262730;color:#fafafa;font-size:13px;
            font-family:sans-serif;">📋 копировать ссылку</button>
        <script>
        const text = {json.dumps(text)};
        const btn = document.getElementById("copy-btn");
        btn.addEventListener("click", function () {{
            const ta = document.createElement("textarea");
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            let ok = false;
            try {{ ok = document.execCommand("copy"); }} catch (e) {{}}
            document.body.removeChild(ta);
            btn.textContent = ok ? "✅ скопировано" : "✖ не удалось";
        }});
        </script>
        """,
        height=44,
    )


def search_site(key, query):
    if key == "steam":
        return pc.search_steam(query)
    if key == "steambuy":
        items = pc.steambuy_search(query)
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            return list(ex.map(pc.steambuy_stock, items))
    if key == "ggsel":
        return pc.search_ggsel(query)
    if key == "gabestore":
        return pc.search_gabestore(query)
    if key == "steampay":
        return pc.search_steampay(query)
    return []


def sort_rows(key, rows, by_sales):
    if key == "ggsel" and by_sales:
        return sorted(rows, key=lambda r: (r.get("sales") is None, -(r.get("sales") or 0),
                                           r["price"] is None, r["price"] or 0))
    return sorted(rows, key=lambda r: (CATEGORY_ORDER.get(r["category"], 3),
                                       r["price"] is None, r["price"] or 0))


def price_text(r):
    if r["price"] is None:
        return "нет цены"
    if r["price"] == 0:
        return "бесплатно"
    return f'{r["price"]} ₽'


def stock_text(r):
    if "status" in r:
        icon = {"in": "✅", "out": "❌"}.get(r.get("status_tag"), "❔")
        return f'{icon} {r["status"]}'
    if r["in_stock"] is True:
        return "✅ в наличии"
    if r["in_stock"] is False:
        return "❌ нет в наличии"
    return "❔ не проверено"


def md_escape(text):
    for ch in "\\`*_[]<>":
        text = text.replace(ch, "\\" + ch)
    return text


def head_markdown(r):
    """Первая строка товара: цена, категория, продажи."""
    label = price_text(r)
    if r["category"]:
        label += f' · {r["category"]}'
    lines = [f"**{label}**"]
    if r.get("sales") is not None:
        lines.append(f'продано: {r["sales"]}')
    return "  \n".join(lines)


def render_item(r):
    url = r.get("url", "")
    title = md_escape(r["title"])
    if r["site"] == "ggsel" and url.startswith("http"):
        # ggsel: название без ссылки, адрес товара только для кнопки копирования
        st.markdown(f"{head_markdown(r)}  \n{title}  \n{stock_text(r)}")
        copy_button(url)
    else:
        link = f"[{title}]({url})" if url.startswith("http") else title
        st.markdown(f"{head_markdown(r)}  \n{link}  \n{stock_text(r)}")
    st.write("")   # небольшой отступ между товарами


def render_items(rows, key, by_sales):
    if not rows:
        st.caption("ничего не найдено")
        return
    for r in sort_rows(key, rows, by_sales):
        render_item(r)


@st.fragment
def ggsel_block():
    """Колонка ggsel отдельным фрагментом: галочка перерисовывает только её."""
    st.checkbox("сортировать по продажам", key="ggsel_by_sales")
    with st.container(height=COLUMN_HEIGHT):
        render_items(st.session_state.results.get("ggsel", []), "ggsel",
                     st.session_state.get("ggsel_by_sales", False))


if "results" not in st.session_state:
    st.session_state.results = {}
    st.session_state.query = ""

st.title("Сравнение цен на игры")

with st.form("search"):
    query = st.text_input("Название игры", placeholder="например, metal gear")
    submitted = st.form_submit_button("Найти")

if submitted and query.strip():
    st.session_state.query = query.strip()

cols = st.columns(len(SITES))
for i, (key, name) in enumerate(SITES):
    with cols[i]:
        if key == "ggsel":
            # заголовок ggsel и рядом кнопка копирования адреса поиска
            head_col, copy_col = st.columns([3, 2])
            with head_col:
                st.markdown(f"### [{name}]({store_url(key, st.session_state.query)})")
            with copy_col:
                copy_button(store_url(key, st.session_state.query))
        else:
            st.markdown(f"### [{name}]({store_url(key, st.session_state.query)})")

by_sales = st.session_state.get("ggsel_by_sales", False)

if submitted and query.strip():
    query = query.strip()
    st.session_state.query = query
    st.session_state.results = {}

    boxes, status = {}, {}
    for i, (key, _) in enumerate(SITES):
        with cols[i]:
            if key == "ggsel":
                status[key] = st.empty()
                status[key].caption("ищем…")
            else:
                boxes[key] = st.container(height=COLUMN_HEIGHT)
                with boxes[key]:
                    status[key] = st.empty()
                    status[key].caption("ищем…")

    with cf.ThreadPoolExecutor(max_workers=len(SITES)) as ex:
        futures = {ex.submit(search_site, key, query): key for key, _ in SITES}
        for fut in cf.as_completed(futures):
            key = futures[fut]
            try:
                rows = fut.result()
                error = None
            except Exception as e:
                rows, error = [], str(e)
            st.session_state.results[key] = rows
            status[key].empty()
            if key != "ggsel":
                with boxes[key]:
                    if error:
                        st.error(f"ошибка: {error}")
                    else:
                        render_items(rows, key, by_sales)

    # ggsel выводим после загрузки, чтобы фрагмент с галочкой создавался один раз
    with cols[2]:
        ggsel_block()
else:
    # обычная перерисовка: например, после переключения галочки ggsel
    for i, (key, _) in enumerate(SITES):
        with cols[i]:
            if key == "ggsel":
                ggsel_block()
            else:
                with st.container(height=COLUMN_HEIGHT):
                    render_items(st.session_state.results.get(key, []), key, by_sales)