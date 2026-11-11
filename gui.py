import queue
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import ttk

import price_compare as pc
from urllib.parse import quote

HOME_URLS = {
    "steam": "https://store.steampowered.com/",
    "steambuy": "https://steambuy.com/",
    "ggsel": "https://ggsel.net/",
    "gabestore": "https://gabestore.ru/",
    "steampay": "https://steampay.com/",
}


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


SITES = [
    ("steam", "Steam"),
    ("steambuy", "steambuy"),
    ("ggsel", "ggsel"),
    ("gabestore", "gabestore"),
    ("steampay", "steampay"),
]

CATEGORY_ORDER = {"игра": 0, "коллекция": 1, "DLC": 2}   # неизвестные в конце


class App:
    def __init__(self, root):
        self.root = root
        root.title("Сравнение цен на игры")
        root.geometry("1500x800")
        self.events = queue.Queue()
        self.gen = 0
        self.rows = {key: [] for key, _ in SITES}
        self.texts = {}
        self.status_labels = {}
        self.ggsel_by_sales = tk.BooleanVar(value=False)

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Поиск").pack(side="left")
        self.query_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query_var, font=("Segoe UI", 12))
        entry.pack(side="left", fill="x", expand=True, padx=10)
        entry.bind("<Return>", lambda e: self.start())
        entry.focus_set()
        ttk.Button(top, text="Найти", command=self.start).pack(side="left")

        columns = ttk.Frame(root, padding=(10, 0, 10, 10))
        columns.pack(fill="both", expand=True)
        for i, (key, name) in enumerate(SITES):
            columns.columnconfigure(i, weight=1, uniform="col")
            col = ttk.Frame(columns, padding=5)
            col.grid(row=0, column=i, sticky="nsew")
            columns.rowconfigure(0, weight=1)
            header = tk.Label(col, text=name, font=("Segoe UI", 12, "bold underline"),
                              fg="#1a5fb4", cursor="hand2")
            header.pack()
            header.bind("<Button-1>", lambda e, k=key: self.open_store(k))
            status = ttk.Label(col, text="", foreground="gray")
            status.pack()
            self.status_labels[key] = status

            if key == "ggsel":
                ttk.Checkbutton(col, text="сортировать по продажам",
                                variable=self.ggsel_by_sales,
                                command=lambda: self.render("ggsel")).pack()

            frame = ttk.Frame(col)
            frame.pack(fill="both", expand=True)
            text = tk.Text(frame, wrap="word", state="disabled", width=28,
                           font=("Segoe UI", 10), relief="solid", borderwidth=1)
            scroll = ttk.Scrollbar(frame, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            text.tag_configure("in", foreground="#1a7f37")
            text.tag_configure("out", foreground="#b42318")
            text.tag_configure("unknown", foreground="gray")
            text.tag_configure("sales", foreground="#6b7280")
            text.tag_configure("title", font=("Segoe UI", 10, "bold"), foreground="#1a5fb4")
            self.texts[key] = text

        root.after(100, self.poll)

    # ---------- запуск поиска ----------

    def start(self):
        query = self.query_var.get().strip()
        if not query:
            return
        self.gen += 1
        gen = self.gen
        for key, _ in SITES:
            self.rows[key] = []
            self.render(key)
            self.set_status(key, "ищем…")
            threading.Thread(target=self.worker, args=(key, query, gen),
                             daemon=True).start()

    def worker(self, key, query, gen):
        try:
            if key == "steam":
                self.run_steam(query, gen)
            elif key == "steambuy":
                self.run_steambuy(query, gen)
            elif key == "ggsel":
                self.run_ggsel(query, gen)
            elif key == "gabestore":
                self.run_gabestore(query, gen)
            elif key == "steampay":
                self.run_steampay(query, gen)
            self.events.put((gen, key, "done", None))
        except Exception as e:
            self.events.put((gen, key, "error", str(e)))

    def run_steam(self, query, gen):
        for row in pc.search_steam(query):
            self.events.put((gen, "steam", "row", row))

    def run_steambuy(self, query, gen):
        items = pc.steambuy_search(query)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(pc.steambuy_stock, item) for item in items]
            for f in as_completed(futures):
                self.events.put((gen, "steambuy", "row", f.result()))

    def run_ggsel(self, query, gen):
        for row in pc.search_ggsel(query):
            self.events.put((gen, "ggsel", "row", row))

    def run_gabestore(self, query, gen):
        for row in pc.search_gabestore(query):
            self.events.put((gen, "gabestore", "row", row))

    def run_steampay(self, query, gen):
        urls = pc.steampay_matches(query, pc.load_steampay_urls())
        for url in urls:
            self.events.put((gen, "steampay", "row", pc.fetch_steampay(url)))

    # ---------- обновление интерфейса ----------

    def poll(self):
        try:
            while True:
                gen, key, kind, payload = self.events.get_nowait()
                if gen != self.gen:
                    continue
                if kind == "row":
                    self.rows[key].append(payload)
                    self.render(key)
                    self.set_status(key, f"найдено: {len(self.rows[key])}")
                elif kind == "done":
                    self.set_status(key, f"готово: {len(self.rows[key])}")
                elif kind == "error":
                    self.set_status(key, f"ошибка: {payload}")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def open_store(self, key):
        webbrowser.open(store_url(key, self.query_var.get()))

    def set_status(self, key, text):
        self.status_labels[key].config(text=text)

    def sort_key(self, key, r):
        if key == "ggsel" and self.ggsel_by_sales.get():
            return (r.get("sales") is None, -(r.get("sales") or 0), r["price"] is None, r["price"] or 0)
        return (
            CATEGORY_ORDER.get(r["category"], 3),
            r["price"] is None,
            r["price"] or 0,
        )

    @staticmethod
    def stock_label(r):
        """Текст статуса и цвет: у Steam свои статусы, у остальных магазинов по наличию."""
        if "status" in r:
            return r["status"], r["status_tag"]
        if r["in_stock"] is True:
            return "в наличии", "in"
        if r["in_stock"] is False:
            return "нет в наличии", "out"
        return "не проверено", "unknown"

    def render(self, key):
        text = self.texts[key]
        rows = sorted(self.rows[key], key=lambda r: self.sort_key(key, r))
        text.config(state="normal")
        text.delete("1.0", "end")
        for name in text.tag_names():
            if name.startswith("row"):
                text.tag_delete(name)

        for idx, r in enumerate(rows):
            row_tag = f"row{idx}"
            if r["price"] is None:
                price = "нет цены"
            elif r["price"] == 0:
                price = "бесплатно"
            else:
                price = f'{r["price"]} ₽'
            status, tag = self.stock_label(r)

            label = f'{price}   {r["category"]}' if r["category"] else price
            text.insert("end", f"{label}\n", ())
            if r.get("sales") is not None:
                text.insert("end", f'продано: {r["sales"]}\n', ("sales",))
            text.insert("end", f'{r["title"]}\n', ("title", row_tag))
            text.insert("end", f"{status}\n\n", (tag,))

            url = r.get("url", "")
            if url.startswith("http"):
                text.tag_bind(row_tag, "<Button-1>",
                              lambda e, u=url: webbrowser.open(u))
                text.tag_bind(row_tag, "<Enter>",
                              lambda e, t=text: t.config(cursor="hand2"))
                text.tag_bind(row_tag, "<Leave>",
                              lambda e, t=text: t.config(cursor=""))
        text.config(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()