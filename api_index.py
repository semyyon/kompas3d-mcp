# -*- coding: utf-8 -*-
"""Индекс API КОМПАС: stub-файлы (KompasAPI7.pyi, Kompas6API5.pyi, constants.py — описания, v22)
+ библиотеки типов установленного КОМПАС (kAPI7.tlb, kAPI5.tlb, ksConstants*.tlb — состав v23).
COM-сервер КОМПАС не нужен: .tlb читается как файл.

Зачем: шлюз kompas_api проверяет имя интерфейса и члена ДО вызова в КОМПАС (опечатка в
имени метода даёт понятную ошибку с подсказкой, а не «COM error: Unknown name»), а опись
возможностей (tools/capabilities.py) берёт отсюда соответствие типов объектов API5 ↔ API7.

    idx = load()                      # {"classes": {...}, "enums": {...}}
    member(idx, "IExtrusion", "Direction")  -> {"kind": "prop", "rw": True, ...} | None
"""
import json
import os
import re

# Optional SDK stub files (KompasAPI7.pyi, Kompas6API5.pyi, constants.py) with member docs.
# They come from the KOMPAS SDK and are NOT shipped in this repository; point KOMPAS_STUBS_DIR
# at your own copy. Without them the index is built from the installed type libraries only.
STUBS_DIR = os.environ.get("KOMPAS_STUBS_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stubs")
STUB_FILES = {"api7": "KompasAPI7.pyi", "api5": "Kompas6API5.pyi"}
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "api_index.json")

_CLASS_RE = re.compile(r"^class (\w+)\(([^)]*)\):(?:\s*#\s*(\S+))?", re.M)
_DEF_RE = re.compile(r"^    def (\w+)\(self(?:, )?([^)]*)\)(?: -> ([^:]+))?:", re.M)
_ENUM_RE = re.compile(r"^class (\w+):\s*#\s*(\S+)", re.M)
_CONST_RE = re.compile(r"^    (\w+)\s*=\s*(-?\d+)\s*\n(?:\s*\"\"\"(.*?)\"\"\")?", re.M | re.S)


def _first_doc_line(body: str) -> str:
    m = re.search(r'"""\s*\n\s*##\s*(.+)', body)
    return m.group(1).strip() if m else ""


def parse_stub(text: str, api: str) -> dict:
    """Классы stub-файла: базы, свойства (rw — есть setter), методы с аргументами."""
    out = {}
    heads = list(_CLASS_RE.finditer(text))
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        bases = [b.strip() for b in h.group(2).split(",") if b.strip() and b.strip() != "IDispatch"]
        cls = {"api": api, "bases": bases, "doc": _first_doc_line(body), "props": {}, "methods": {}}
        prev_prop = False
        for line_m in re.finditer(r"^    (@property|@\w+\.setter|def .*)$", body, re.M):
            line = line_m.group(1)
            if line == "@property":
                prev_prop = True
                continue
            sm = re.match(r"@(\w+)\.setter", line)
            if sm:
                if sm.group(1) in cls["props"]:
                    cls["props"][sm.group(1)]["rw"] = True
                prev_prop = False
                continue
            dm = _DEF_RE.match("    " + line)
            if not dm:
                continue
            name, args, ret = dm.group(1), dm.group(2), (dm.group(3) or "").strip()
            if prev_prop:
                if name not in cls["props"]:
                    cls["props"][name] = {"type": ret, "rw": False}
            elif not name.startswith("_") and name not in cls["props"]:
                arg_names = [a.split(":")[0].split("=")[0].strip() for a in args.split(",") if a.strip()]
                cls["methods"][name] = {"args": arg_names, "ret": ret}
            prev_prop = False
        out[h.group(1)] = cls
    return out


def parse_enums(text: str) -> dict:
    """Перечисления constants.py: {Enum: {"page": html, "values": {NAME: [int, doc]}}}."""
    out = {}
    heads = list(_ENUM_RE.finditer(text))
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        vals = {}
        for c in _CONST_RE.finditer(body):
            vals[c.group(1)] = [int(c.group(2)), (c.group(3) or "").strip()]
        out[h.group(1)] = {"page": h.group(2), "values": vals}
    return out


# Библиотеки типов установленного КОМПАС — источник истины по составу членов v23
# (stubs сгенерированы по справке v22: IModelsFamilyManager и др. в них нет).
KOMPAS_BIN = os.environ.get("KOMPAS_BIN", r"C:\Program Files\ASCON\Bin")
TLB_FILES = {"api7": "kAPI7.tlb", "api5": "kAPI5.tlb",
             "const": "ksConstants.tlb", "const3d": "ksConstants3D.tlb"}
_IDISPATCH = {"QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo",
              "GetIDsOfNames", "Invoke"}


def parse_typelib(path: str, api: str):
    """Интерфейсы и перечисления из .tlb: члены уже «развёрнуты» (наследованные включены).
    Свойство с аргументами (IExtrusion.Depth(Normal)) — индексированное: args непусты."""
    import pythoncom
    tl = pythoncom.LoadTypeLib(path)
    classes, enums = {}, {}
    for k in range(tl.GetTypeInfoCount()):
        ti = tl.GetTypeInfo(k)
        ta = ti.GetTypeAttr()
        name = tl.GetDocumentation(k)[0]
        if ta.typekind == pythoncom.TKIND_ENUM:
            vals = {}
            for v in range(ta.cVars):
                vd = ti.GetVarDesc(v)
                vals[ti.GetNames(vd.memid)[0]] = [int(vd.value), ""]
            enums[name] = {"page": "", "values": vals}
            continue
        if ta.typekind not in (pythoncom.TKIND_DISPATCH, pythoncom.TKIND_INTERFACE):
            continue
        props, meths = {}, {}
        for f in range(ta.cFuncs):
            fd = ti.GetFuncDesc(f)
            names = ti.GetNames(fd.memid)
            m = names[0]
            if m in _IDISPATCH:
                continue
            if fd.invkind == pythoncom.INVOKE_FUNC:
                meths[m] = {"args": list(names[1:]), "ret": ""}
            else:
                p = props.setdefault(m, {"type": "", "rw": False, "args": []})
                if fd.invkind == pythoncom.INVOKE_PROPERTYGET:
                    p["args"] = list(names[1:])
                else:
                    p["rw"] = True
                    if not p["args"]:
                        p["args"] = list(names[1:])
        classes[name] = {"api": api, "bases": [], "doc": "", "props": props, "methods": meths,
                         "src": "tlb"}
    return classes, enums


def _sources():
    files = [os.path.join(STUBS_DIR, f) for f in list(STUB_FILES.values()) + ["constants.py"]]
    files = [f for f in files if os.path.exists(f)]
    files += [p for p in (os.path.join(KOMPAS_BIN, t) for t in TLB_FILES.values()) if os.path.exists(p)]
    return files, [round(os.path.getmtime(f)) for f in files]


def _merge_class(stub: dict, tlb: dict) -> dict:
    """Члены — объединение (typelib v23 полнее), описание и базы — из stubs."""
    out = dict(stub)
    props = {k: dict(v) for k, v in stub["props"].items()}
    for k, v in tlb["props"].items():
        if k in props:
            props[k]["rw"] = props[k]["rw"] or v["rw"]
            props[k]["args"] = v["args"]
        else:
            props[k] = dict(v, inherited_or_new=True)
    meths = dict(stub["methods"])
    for k, v in tlb["methods"].items():
        meths.setdefault(k, v)
        if v["args"]:                      # имена аргументов typelib — как их ждёт COM
            meths[k] = dict(meths[k], args=v["args"])
    out.update(props=props, methods=meths, src="stub+tlb")
    return out


def build() -> dict:
    files, mt = _sources()
    classes = {}
    enums = {}
    for api, fn in STUB_FILES.items():
        sp = os.path.join(STUBS_DIR, fn)
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                classes.update(parse_stub(f.read(), api))
    cp = os.path.join(STUBS_DIR, "constants.py")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            enums = parse_enums(f.read())
    for api, fn in TLB_FILES.items():
        p = os.path.join(KOMPAS_BIN, fn)
        if not os.path.exists(p):
            continue
        t_classes, t_enums = parse_typelib(p, api)
        for name, c in t_classes.items():
            classes[name] = _merge_class(classes[name], c) if name in classes else c
        for name, e in t_enums.items():
            if name in enums:
                for v, val in e["values"].items():
                    enums[name]["values"].setdefault(v, val)
            else:
                enums[name] = e
    return {"key": mt, "classes": classes, "enums": enums}


_MEM = {"idx": None}


def load() -> dict:
    """Индекс из кэша logs/api_index.json; пересборка при изменении stub-файлов (~1 с)."""
    _, mt = _sources()
    if _MEM["idx"] is not None and _MEM["idx"]["key"] == mt:
        return _MEM["idx"]
    idx = None
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            idx = json.load(f)
        if idx.get("key") != mt:
            idx = None
    except Exception:
        idx = None
    if idx is None:
        idx = build()
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    _MEM["idx"] = idx
    return idx


def mro(idx: dict, cls: str) -> list:
    """Класс и все его базы (в ширину), без повторов."""
    seen, queue = [], [cls]
    while queue:
        c = queue.pop(0)
        if c in seen or c not in idx["classes"]:
            continue
        seen.append(c)
        queue.extend(idx["classes"][c]["bases"])
    return seen


def member(idx: dict, cls: str, name: str):
    """Описание члена интерфейса с учётом наследования; None — такого нет."""
    for c in mro(idx, cls):
        d = idx["classes"][c]
        if name in d["props"]:
            return dict(d["props"][name], kind="prop", owner=c)
        if name in d["methods"]:
            return dict(d["methods"][name], kind="method", owner=c)
    return None


def members(idx: dict, cls: str) -> dict:
    """Все свойства и методы интерфейса с базами: {"props": {...}, "methods": {...}}."""
    props, meths = {}, {}
    for c in reversed(mro(idx, cls)):
        props.update(idx["classes"][c]["props"])
        meths.update(idx["classes"][c]["methods"])
    return {"props": props, "methods": meths}


def similar(names, name: str, n: int = 5) -> list:
    """Похожие имена — для подсказки при опечатке."""
    import difflib
    low = {x.lower(): x for x in names}
    hits = difflib.get_close_matches(name.lower(), list(low), n=n, cutoff=0.6)
    sub = [x for x in names if name.lower() in x.lower() and low.get(x.lower()) not in hits]
    return [low[h] for h in hits] + sub[: max(0, n - len(hits))]


def o3d_interfaces(idx: dict) -> dict:
    """{значение ksObj3dTypeEnum: API7-интерфейс} — из описаний типов (справка SDK)."""
    out = {}
    for name, (val, doc) in idx["enums"].get("ksObj3dTypeEnum", {}).get("values", {}).items():
        m = re.search(r"API 7:\s*(I\w+)", doc)
        if m and m.group(1) in idx["classes"]:
            out[val] = m.group(1)
    return out
