# -*- coding: utf-8 -*-
"""Ядро шлюза kompas_api: разбор операторов, проверка по индексу API ДО вызова, вызов,
преобразование результата в JSON. Без COM — объекты КОМПАС приходят снаружи (roots),
поэтому всё тестируется на фейковых объектах.

Оператор (JSON-объект):
  obj   — откуда: корень ("app7","app5","doc","doc5","top","top5") или "$имя" / "$имя.N"
          (сохранённый результат / его N-й элемент); по умолчанию "doc"
  cast  — привести к интерфейсу перед действием ("IFeature7")
  ровно одно действие:
    get: "Имя" [+ args — для индексированного свойства]      set: "Имя", value [+ args]
    call: "Метод", args                                       items: true — элементы коллекции
    help: "IИнтерфейс" | "IИнтерфейс.Член"   find: "подстрока" — без КОМПАС
  as    — сохранить результат под именем для следующих операторов
Аргументы: JSON-значения; "$имя" — сохранённый объект; {"const": "o3d_cutExtrusion"} —
значение перечисления; строка, начинающаяся с "$$", — литерал "$…".
"""
import json

import api_index

ROOTS = ("app7", "app5", "doc", "doc5", "top", "top5")
ACTIONS = ("get", "set", "call", "items", "help", "find")
MAX_OPS = 60
MAX_ITEMS = 200
MAX_STEP_CHARS = 2500
MAX_TOTAL_CHARS = 6000

# Сохранение/закрытие/открытие — только инструментами с защитами проекта.
BLOCKED = {
    "Save": "kompas_save_document (делает бэкап .a3d)",
    "SaveAs": "kompas_save_document(path=...)",
    "SaveAsEx": "kompas_save_document(path=...)",
    "Close": "kompas_close_document(document=...)",
    "close": "kompas_close_document(document=...)",
    "ksCloseDocument": "kompas_close_document(document=...)",
    "ksSaveDocument": "kompas_save_document",
    "ksSaveDocumentEx": "kompas_save_document",
    "ksOpenDocument": "kompas_open_document",
    "OpenDocument": "kompas_open_document",
    "SaveAsToUncompressedRasterFormat": "kompas_export_raster",
    "CloseAllDocuments": "kompas_close_document по одному",
    "Quit": "— закрывать КОМПАС агенту нельзя",
    "ksExit": "— закрывать КОМПАС агенту нельзя",
    "ExecuteKompasCommand": "— команды интерфейса открывают диалоги; ищи метод API",
}
BLOCKED_ON = {
    "IDocuments": {"Open": "kompas_open_document", "Add": "kompas_create_document",
                   "AddWithDefaultSettings": "kompas_create_document"},
}
# API5-документы: Open/Create у ksDocument3D/2D, ksSpcDocument, ksDocumentTxt открывают/создают
# документ в обход инструментов (у ksEntity.Create — это построение, его не трогаем)
API5_DOC_PREFIXES = ("ksDocument", "ksSpcDocument")
API5_DOC_BLOCKED = {"Open": "kompas_open_document", "Create": "kompas_create_document"}
# В графическом документе (чертёж/фрагмент) шлюз только читает: чертит конвейер
# kompas_build_drawing; эскиз детали — это 3D-документ, на него запрет не распространяется
MUTATING_2D_PREFIXES = ("Add", "Create", "Delete", "Set", "Update", "Insert", "Move", "Rotate", "Remove",
                        "Clear", "Rebuild", "Execute", "Save", "Make", "Destroy", "Transform", "Copy")
# Методы, не меняющие модель (для признака changed); остальные вызовы считаются правкой.
READ_PREFIXES = ("Get", "Is", "Check", "Find", "Exist", "Calc", "Choose", "Read", "Search")


class GatewayError(Exception):
    pass


def parse_ops(text):
    try:
        ops = json.loads(text) if isinstance(text, str) else text
    except Exception as e:
        raise GatewayError(f"ops — не JSON: {e}")
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not ops:
        raise GatewayError("ops — непустой JSON-список операторов")
    if len(ops) > MAX_OPS:
        raise GatewayError(f"{len(ops)} операторов, предел {MAX_OPS} — раздели")
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise GatewayError(f"[{i}] оператор должен быть объектом")
        acts = [a for a in ACTIONS if a in op]
        if len(acts) != 1:
            raise GatewayError(f"[{i}] нужно ровно одно действие из {ACTIONS}, есть: {acts or 'нет'}")
        if "set" in op and "value" not in op:
            raise GatewayError(f"[{i}] set без value")
        if "args" in op and not isinstance(op["args"], list):
            raise GatewayError(f"[{i}] args — список")
        obj = op.get("obj", "doc")
        if not (obj in ROOTS or (isinstance(obj, str) and obj.startswith("$"))):
            raise GatewayError(f"[{i}] obj: корень {ROOTS} или \"$имя\", а не {obj!r}")
    return ops


def needs_com(ops):
    return any(not ("help" in op or "find" in op) for op in ops)


def help_text(idx, what):
    cls, _, mem = what.partition(".")
    if cls not in idx["classes"]:
        raise GatewayError(f"нет интерфейса {cls!r}. Похожие: {', '.join(api_index.similar(list(idx['classes']), cls))}")
    if mem:
        m = api_index.member(idx, cls, mem)
        if m is None:
            raise GatewayError(_no_member(idx, cls, mem))
        return {"member": f"{m['owner']}.{mem}", "kind": m["kind"], "args": m.get("args", []),
                "rw": m.get("rw"), "type": m.get("type") or m.get("ret") or "",
                "hint": "текст справки — kompas_sdk_search(\"" + f"{cls} {mem}" + "\")"}
    ms = api_index.members(idx, cls)
    return {"interface": cls, "doc": idx["classes"][cls]["doc"], "bases": api_index.mro(idx, cls)[1:],
            "props": {k: ("rw" if v.get("rw") else "r") + (f"({', '.join(v['args'])})" if v.get("args") else "")
                      for k, v in sorted(ms["props"].items())},
            "methods": {k: v.get("args", []) for k, v in sorted(ms["methods"].items())}}


def find_text(idx, sub, limit=40):
    s = sub.lower()
    ifaces = [c for c in idx["classes"] if s in c.lower()][:limit]
    mems = []
    for c, d in idx["classes"].items():
        for m in list(d["props"]) + list(d["methods"]):
            if s in m.lower():
                mems.append(f"{c}.{m}")
    enums = [f"{e}.{v}" for e, d in idx["enums"].items() for v in d["values"] if s in v.lower()]
    return {"interfaces": ifaces, "members": mems[:limit], "constants": enums[:limit],
            "more": max(0, len(mems) - limit)}


def _no_member(idx, cls, name):
    ms = api_index.members(idx, cls)
    sim = api_index.similar(list(ms["props"]) + list(ms["methods"]), name)
    return f"{cls}: нет члена {name!r}." + (f" Похожие: {', '.join(sim)}." if sim else "") + \
        f" Весь список — {{\"help\": \"{cls}\"}}."


def check_member(idx, iface, op):
    """Проверка действия над объектом интерфейса iface ДО вызова. Ничего не возвращает."""
    name = op.get("get") or op.get("set") or op.get("call")
    if name in BLOCKED:
        raise GatewayError(f"{name} через шлюз запрещён — используй {BLOCKED[name]}")
    if name in BLOCKED_ON.get(iface, {}):
        raise GatewayError(f"{iface}.{name} через шлюз запрещён — используй {BLOCKED_ON[iface][name]}")
    if iface.startswith(API5_DOC_PREFIXES) and name in API5_DOC_BLOCKED:
        raise GatewayError(f"{iface}.{name} через шлюз запрещён — используй {API5_DOC_BLOCKED[name]}")
    if name == "Delete" and iface.startswith(("IKompasDocument", "ksDocument", "IPartDocument",
                                              "IAssemblyDocument", "IDrawingDocument")):
        raise GatewayError("удалять документ через шлюз нельзя")
    if iface not in idx["classes"]:
        return                      # неизвестный интерфейс — проверить нечем, вызов покажет
    if "items" in op:
        return
    m = api_index.member(idx, iface, name)
    if m is None:
        raise GatewayError(_no_member(idx, iface, name))
    args = op.get("args", [])
    if "call" in op and m["kind"] == "prop" and not m.get("args"):
        raise GatewayError(f"{iface}.{name} — свойство: {{\"get\": \"{name}\"}}")
    if "set" in op and m["kind"] == "prop" and not m.get("rw"):
        raise GatewayError(f"{iface}.{name} — свойство только для чтения")
    known = m.get("args") or []
    if m["kind"] == "method" and known and len(args) > len(known):
        raise GatewayError(f"{iface}.{name}({', '.join(known)}) — передано {len(args)} аргументов")
    if m["kind"] == "prop" and ("get" in op or "set" in op) and len(args) > len(known):
        raise GatewayError(f"{iface}.{name} — у свойства {len(known)} индексных аргументов"
                           f" ({', '.join(known) or 'нет'}), передано {len(args)}")


def mutates(op):
    """Оператор может менять документ: set или вызов «изменяющего» метода / API5-функции ks*."""
    if "set" in op:
        return True
    name = op.get("call") or ""
    if name.startswith("ks"):
        return not name.startswith(("ksGet", "ksExist", "ksIs", "ksCalc", "ksChoice", "ksRead"))
    return name.startswith(MUTATING_2D_PREFIXES)


def default_iface(obj):
    return type(obj).__name__


def is_com(v):
    return hasattr(v, "_oleobj_")


def to_json(v, iface_of=default_iface, depth=2, max_items=MAX_ITEMS):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if is_com(v):
        out = {"_iface": iface_of(v)}
        if depth > 0:
            for p in ("Name", "Count"):
                try:
                    val = getattr(v, p)
                    if isinstance(val, (int, float, str)):
                        out[p] = val
                except Exception:
                    pass
        return out
    if isinstance(v, (tuple, list)):
        seq = list(v)
        if (len(seq) == 2 and isinstance(seq[0], (bool, int)) and isinstance(seq[1], (tuple, list))):
            return {"ok": seq[0], "value": to_json(seq[1], iface_of, depth - 1, max_items)}
        res = [to_json(x, iface_of, depth - 1, max_items) for x in seq[:max_items]]
        if len(seq) > max_items:
            res.append({"_truncated": len(seq) - max_items})
        return res
    try:
        return float(v)            # pywintypes и т.п.
    except Exception:
        return str(v)


def _resolve(arg, vars_, idx):
    if isinstance(arg, str) and arg.startswith("$$"):
        return arg[1:]
    if isinstance(arg, str) and arg.startswith("$"):
        return _ref(arg, vars_)
    if isinstance(arg, dict) and set(arg) == {"const"}:
        name = arg["const"]
        for e in idx["enums"].values():
            if name in e["values"]:
                return e["values"][name][0]
        raise GatewayError(f"нет константы {name!r}")
    if isinstance(arg, list):
        return [_resolve(a, vars_, idx) for a in arg]
    return arg


def _ref(ref, vars_):
    name, _, n = ref[1:].partition(".")
    if name not in vars_:
        raise GatewayError(f"{ref}: нет сохранённого результата {name!r} (оператор с \"as\")")
    v = vars_[name]
    if (n and isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], (bool, int))
            and isinstance(v[1], (tuple, list))):
        v = v[1]                      # API5 (ok, (…)): индексируем значение, как его показывает ответ
    if n:
        try:
            return v[int(n)]
        except Exception:
            pass
        for getter in ("GetByIndex", "Item"):          # COM-коллекции (ksEntityCollection, IParts7…)
            try:
                el = getattr(v, getter)(int(n))
                if el is not None:
                    return el
            except Exception:
                continue
        raise GatewayError(f"{ref}: у {name} нет элемента {n}")
    return v


def _collection_items(obj, iface, idx, limit):
    count = obj.Count
    getter = None
    ms = api_index.members(idx, iface)["methods"] if iface in idx["classes"] else {}
    for cand in ("Item", iface[1:].rstrip("0123456789").rstrip("s"), iface[1:].rstrip("s")):
        if cand in ms or (not ms and hasattr(obj, cand)):
            getter = getattr(obj, cand)
            break
    if getter is None:
        one_arg = [k for k, v in ms.items() if len(v.get("args") or []) == 1]
        if len(one_arg) == 1:
            getter = getattr(obj, one_arg[0])
    if getter is None:
        raise GatewayError(f"{iface}: не понял, как брать элементы — вызови метод сам (help)")
    return [getter(i) for i in range(min(count, limit))], count


def run(ops, roots, idx, iface_of=default_iface, cast=None, wrap=None, read_only=""):
    """Выполнить операторы. roots — dict корней или функция name -> объект (ленивые корни).
    cast(obj, iface) -> obj — приведение (в сервере CastTo); wrap(obj) -> obj — типизировать
    динамический объект перед действием; read_only — непустая причина запрета изменений
    (графический документ). Возврат — dict для JSON."""
    get_root = roots if callable(roots) else roots.__getitem__
    vars_, steps, changed = {}, [], False
    for i, op in enumerate(ops):
        try:
            if "help" in op:
                res = help_text(idx, op["help"])
            elif "find" in op:
                res = find_text(idx, op["find"])
            else:
                o = op.get("obj", "doc")
                obj = _ref(o, vars_) if o.startswith("$") else get_root(o)
                if obj is None:
                    raise GatewayError(f"корень {o!r} недоступен для этого документа")
                if wrap is not None:
                    obj = wrap(obj)
                if op.get("cast"):
                    if cast is None:
                        raise GatewayError("cast недоступен")
                    obj = cast(obj, op["cast"])
                iface = op.get("cast") or iface_of(obj)
                check_member(idx, iface, op)
                if read_only and mutates(op):
                    raise GatewayError(read_only)
                args = _resolve(op.get("args", []), vars_, idx)
                if "items" in op:
                    items, count = _collection_items(obj, iface, idx, MAX_ITEMS)
                    res, raw = {"count": count, "items": [to_json(x, iface_of) for x in items]}, items
                elif "get" in op:
                    name = op["get"]
                    if args:            # индексированное: makepy даёт метод Name(args), иногда GetName
                        g = getattr(obj, name, None)
                        raw = g(*args) if callable(g) else getattr(obj, "Get" + name)(*args)
                    else:
                        raw = getattr(obj, name)
                    res = to_json(raw, iface_of)
                elif "set" in op:
                    name, val = op["set"], _resolve(op["value"], vars_, idx)
                    if args:
                        getattr(obj, "Set" + name)(*args, val)
                    else:
                        setattr(obj, name, val)
                    raw, res, changed = None, "OK", True
                else:
                    name = op["call"]
                    raw = getattr(obj, name)(*args)
                    res = to_json(raw, iface_of)
                    if not name.startswith(READ_PREFIXES):
                        changed = True
                if op.get("as"):
                    vars_[op["as"]] = raw
            steps.append({"i": i, "result": res})
        except GatewayError as e:
            steps.append({"i": i, "error": str(e)})
            break
        except Exception as e:      # ошибка КОМПАС/COM — текст даёт вызывающий (format_error)
            steps.append({"i": i, "error": f"{type(e).__name__}: {e}", "com": True})
            break
    ok = all("error" not in s for s in steps) and len(steps) == len(ops)
    return {"ok": ok, "steps": steps, "changed": changed}


def dumps_clipped(result):
    """ВАЛИДНЫЙ JSON ответа ≤ ~MAX_TOTAL_CHARS: ok/changed/document — первыми, длинные результаты
    и ошибки шагов урезаются всё сильнее, пока ответ не поместится."""
    head = {k: result[k] for k in ("ok", "changed", "document") if k in result}
    steps = result["steps"]
    for limit in (MAX_STEP_CHARS, 1000, 400, 150, 60):
        out_steps = []
        for s in steps:
            s2 = dict(s)
            for key in ("result", "error"):
                if key in s2:
                    t = s2[key] if isinstance(s2[key], str) else json.dumps(s2[key], ensure_ascii=False)
                    if len(t) > limit:
                        s2[key] = {"_clipped": t[:limit], "_chars": len(t)}
            out_steps.append(s2)
        text = json.dumps(dict(head, steps=out_steps), ensure_ascii=False, separators=(",", ":"))
        if len(text) <= MAX_TOTAL_CHARS:
            break
    return text
