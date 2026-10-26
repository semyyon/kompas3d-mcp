"""Офлайн-поиск по справке SDK КОМПАС (KOMPAS_SDK_ru-RU.zip). Без COM.

Чистые части: parse_page (html -> заголовок + текст), search (ранжирование),
snippet/format_results (вывод). Построение индекса и кеш: build_index, load_or_build.
Идея — hymaxo/kompas3d-mcp (MIT); код свой.
"""
import html as _html
import json
import os
import re
import time
import zipfile

CACHE_VERSION = 1
MAX_TEXT_CHARS = 8000          # потолок текста одной страницы в индексе

_CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([\w.-]+)", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_JUNK_RE = re.compile(r"<(script|style|svg)\b.*?</\1\s*>|<!--.*?-->", re.I | re.S)
_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|br|tr|td|th|li|ul|ol|table|tbody|h[1-6]|pre)\b[^>]*>", re.I)
_ANY_TAG_RE = re.compile(r"<[^>]*>")
_HEAD_RE = re.compile(r"<head\b.*?</head>", re.I | re.S)
_BODY_RE = re.compile(r"<body\b[^>]*>", re.I)


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    m = _CHARSET_RE.search(raw[:3000])
    if m:
        try:
            return raw.decode(m.group(1).decode("ascii", "ignore"), "replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", "replace")


def _clean(s: str) -> str:
    return " ".join(_html.unescape(s).split())


def parse_page(raw: bytes, max_chars: int = MAX_TEXT_CHARS):
    """(заголовок, простой текст) страницы справки. Берётся фрагмент между
    <!--ZOOMRESTART--> и подвалом topicfooter; без маркера — всё тело."""
    doc = _decode(raw)
    m = _TITLE_RE.search(doc)
    title = _clean(re.sub(r"<[^>]*>", "", m.group(1))) if m else ""
    start = doc.find("<!--ZOOMRESTART")
    if start >= 0:
        region = doc[start:]
        cut = region.find('class="topicfooter"')
        if cut >= 0:
            region = region[:max(region.rfind("<", 0, cut), 0)]
    else:
        b = _BODY_RE.search(doc)
        region = doc[b.end():] if b else _HEAD_RE.sub(" ", doc)
    region = _JUNK_RE.sub(" ", region)
    region = _BLOCK_TAG_RE.sub(" ", region)
    text = _clean(_ANY_TAG_RE.sub("", region))
    return title, text[:max_chars]


def words_of(query: str):
    return [w for w in query.lower().split() if w]


def _score(page, words):
    """(сколько слов найдено, баллы). Заголовок > имя файла > текст."""
    tl, pl, xl = page["t"].lower(), page["p"].lower(), page["x"].lower()
    stem = os.path.splitext(os.path.basename(pl))[0]
    tokens = set(re.findall(r"\w+", tl))
    found = pts = 0
    for w in words:
        s = 0
        if w in tl:
            s += 100 + (50 if w in tokens else 0)
        if w == stem:
            s += 80
        elif w in stem:
            s += 30
        c = xl.count(w)
        if c:
            s += min(c, 10)
        if s:
            found += 1
            pts += s
    return found, pts


def search(pages, query: str, limit: int = 5):
    """Топ-limit страниц. Требуются все слова запроса; если таких нет — любое."""
    words = words_of(query)
    if not words or limit <= 0:
        return []
    scored = []
    for p in pages:
        found, pts = _score(p, words)
        if found:
            scored.append((found, pts, p))
    full = [s for s in scored if s[0] == len(words)]
    pool = full or scored
    pool.sort(key=lambda s: (-s[0], -s[1], len(s[2]["t"]), s[2]["p"]))
    return [s[2] for s in pool[:limit]]


def snippet(text: str, words, width: int = 300) -> str:
    low = text.lower()
    hits = [i for i in (low.find(w) for w in words) if i >= 0]
    pos = min(hits) if hits else 0
    start = max(0, pos - width // 3)
    end = start + width
    out = text[start:end]
    return ("…" if start > 0 else "") + out + ("…" if end < len(text) else "")


def format_results(results, words, width: int = 300) -> str:
    if not results:
        return "Ничего не найдено."
    return "\n".join(f"{i}. {p['t']}  [{p['p']}]\n   {snippet(p['x'], words, width)}"
                     for i, p in enumerate(results, 1))


def find_zip(candidates):
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def build_index(zip_path: str):
    pages = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            n = info.filename
            base = os.path.basename(n)
            if not n.lower().endswith((".htm", ".html")) or base.startswith("_hm"):
                continue
            title, text = parse_page(z.read(n))
            if title or text:
                pages.append({"p": n, "t": title, "x": text})
    return pages


def zip_key(zip_path: str):
    st = os.stat(zip_path)
    return {"v": CACHE_VERSION, "mtime": int(st.st_mtime), "size": st.st_size}


def load_or_build(zip_path: str, cache_path: str):
    """Индекс из JSON-кеша, если zip не менялся (mtime/size), иначе пересборка."""
    key = zip_key(zip_path)
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("key") == key and isinstance(data.get("pages"), list):
            return data["pages"]
    except (OSError, ValueError):
        pass
    pages = build_index(zip_path)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, "pages": pages}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, cache_path)
    except OSError:
        pass       # без кеша работаем, просто каждый раз медленнее
    return pages
