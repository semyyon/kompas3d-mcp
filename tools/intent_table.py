# -*- coding: utf-8 -*-
"""Таблица замысла детали (intent.json) — решения конструктора ДО листа: элементы и их функции,
базы, размеры (от какой базы, номинал, допуск/посадка, в каком виде), допуски формы и
расположения, шероховатость, виды, ТТ, вопросы пользователю. Пишет агент drawing-intent,
утверждает пользователь, по утверждённой таблице строится лист (этап 2в,
docs/drawings-rework-2026-10-19.md). Здесь — проверка формата и md-версия для пользователя.

    python -X utf8 tools/intent_table.py <intent.json> [--md <out.md>]

Формат (поля помимо перечисленных допускаются):
{
 "part":     {"marking", "name", "material", "model", "class"},
 "summary":  "назначение детали в 1–3 фразах (что из модели, что допущение)",
 "features": [{"id": "F1", "kind": "шейка|ступень|торец|буртик|паз|фаска|канавка|галтель|
               расточка|резьба|отверстие|лыска|прочее", "geom": {...мм модели...},
               "function": "посадочная под подшипник|свободная|упорная|…",
               "source": "модель|допущение|вопрос Q1"}],
 "bases":    [{"id": "А", "feature": "F3", "role": "конструкторская|технологическая|измерительная",
               "why": "…"}],
 "dims":     [{"id": "D1", "features": ["F1"], "type": "lin|dia|rad|ang|chamfer|thread",
               "value": 80, "model_value": 80.0, "from": "F0|база А|ось",
               "scheme": "цепь|от базы|габарит|координата|справочный",
               "tolerance": "j6|h14|+0,1|±0,2|null", "reference": false,
               "view": "V1", "why": "…"}],
 "gdt":      [{"id": "G1", "feature": "F1", "kind": "радиальное биение|соосность|
               перпендикулярность|параллельность|симметричность|цилиндричность|…",
               "value": "0,02", "base": "А", "why": "…",
               "source": "ГОСТ 3325-85 табл. 4|расчёт: … = …|вопрос Q3"}],
 "roughness":[{"feature": "F1", "ra": "0,8", "why": "…"}],
 "ra_general": "3,2",
 "views":    [{"id": "V1", "type": "главный|разрез|сечение|выносной|местный|вид",
               "shows": ["F5"], "scale": "1:2", "why": "…"}],
 "tt":       ["…"],
 "questions":[{"id": "Q1", "text": "…", "options": ["…", "…"], "default": "…",
               "affects": ["D3", "G1"]}]
}
Правила проверки (validate): ссылки на элементы, базы, виды и вопросы существуют; у каждого
решения есть why; размер одного элемента одного типа — ровно в одном виде; посадочная
поверхность имеет допуск или посадку и шероховатость; номинал не дальше 0,05 мм от модели,
если это не справочный/округлённый размер с пояснением в why; у вопроса ≥ 2 варианта.
Источник (решение пользователя 2026-10-19): у каждого допуска формы/расположения — gdt[].source,
у каждого размера с допуском/посадкой — dims[].tol_source. Допустимы только: «ГОСТ <номер> …»
(с таблицей или пунктом), «расчёт: <формула и числа>», «вопрос Qn» (вопрос есть в таблице).
«Типовое», «по опыту», «≈IT…» без таблицы — не источник: значение выносится в вопрос.
"""
import json
import os
import re
import sys

TYPES = {"lin", "dia", "rad", "ang", "chamfer", "thread"}
VIEW_TYPES = {"главный", "разрез", "сечение", "выносной", "местный", "вид"}
FIT_WORDS = ("посадоч", "подшипник", "сопряг", "под ступиц", "под муфт", "под уплотн")


def _ids(items):
    return [x.get("id") for x in items or []]


SOURCE_RE = re.compile(r"^(ГОСТ(\s+Р)?\s+[\d.]+.*(табл|п\.|пункт|разд).*|расч[её]т:\s*\S.*=.*\d.*|вопрос\s+Q\d+)$",
                       re.IGNORECASE)
BAD_SOURCE = ("типов", "по опыту", "обычно", "принято", "по умолчанию", "≈")


def source_error(where, src, quests):
    """Ошибка источника значения или None. Вопрос должен существовать в таблице."""
    s = str(src or "").strip()
    if not s:
        return f"{where}: нет источника (ГОСТ с таблицей/пунктом, расчёт или вопрос Qn)"
    low = s.lower()
    if any(b in low for b in BAD_SOURCE) and not low.startswith("вопрос"):
        return f"{where}: источник «{s}» — не источник (типовое/оценка); вынести в вопрос"
    if not SOURCE_RE.match(s):
        return f"{where}: источник «{s}» не в форме «ГОСТ … табл./п. …», «расчёт: … = …» или «вопрос Qn»"
    m = re.match(r"вопрос\s+(Q\d+)", s, re.IGNORECASE)
    if m and m.group(1).upper() not in {q.upper() for q in quests}:
        return f"{where}: источник ссылается на несуществующий {m.group(1)}"
    return None


def validate(t):
    """Список ошибок (строки); пусто — таблица корректна."""
    err = []
    feats = {f.get("id"): f for f in t.get("features") or []}
    bases = set(_ids(t.get("bases")))
    views = set(_ids(t.get("views")))
    quests = {q.get("id"): q for q in t.get("questions") or []}
    for key in ("part", "features", "dims", "views"):
        if not t.get(key):
            err.append(f"нет раздела {key}")
    for name, items in (("features", t.get("features")), ("dims", t.get("dims")),
                        ("views", t.get("views")), ("gdt", t.get("gdt")), ("bases", t.get("bases")),
                        ("questions", t.get("questions"))):
        ids = _ids(items)
        dup = {i for i in ids if ids.count(i) > 1}
        if dup or None in ids:
            err.append(f"{name}: id повторяются или пусты: {sorted(map(str, dup))}")

    def ref_ok(where, r):
        """Ссылка «откуда»: элемент, «база X», «ось», вопрос."""
        if r is None:
            return
        r = str(r)
        if r in feats or r == "ось" or r.startswith("ось "):
            return
        if r.startswith("база "):
            if r[5:] not in bases:
                err.append(f"{where}: нет базы {r[5:]}")
            return
        err.append(f"{where}: ссылка «{r}» не на элемент/базу/ось")

    for f in t.get("features") or []:
        src = str(f.get("source", ""))
        if src.startswith("вопрос") and src.split()[-1] not in quests:
            err.append(f"{f.get('id')}: source ссылается на несуществующий {src}")
    for b in t.get("bases") or []:
        if b.get("feature") not in feats:
            err.append(f"база {b.get('id')}: нет элемента {b.get('feature')}")
        if not b.get("why"):
            err.append(f"база {b.get('id')}: нет why")
    seen = {}
    for d in t.get("dims") or []:
        did = d.get("id")
        if d.get("type") not in TYPES:
            err.append(f"{did}: тип {d.get('type')} не из {sorted(TYPES)}")
        fs = d.get("features") or []
        if not fs:
            err.append(f"{did}: нет features")
        for fid in fs:
            if fid not in feats:
                err.append(f"{did}: нет элемента {fid}")
        ref_ok(did, d.get("from"))
        if d.get("view") not in views:
            err.append(f"{did}: нет вида {d.get('view')}")
        if not d.get("why"):
            err.append(f"{did}: нет why")
        if d.get("tolerance"):
            e = source_error(f"{did} (допуск {d['tolerance']})", d.get("tol_source"), quests)
            if e:
                err.append(e)
        key = (tuple(sorted(fs)), d.get("type"), str(d.get("from")))
        if key in seen and seen[key][1] != d.get("view"):
            err.append(f"{did}: тот же размер, что {seen[key][0]}, в другом виде (повтор)")
        seen.setdefault(key, (did, d.get("view")))
        v, mv = d.get("value"), d.get("model_value")
        if isinstance(v, (int, float)) and isinstance(mv, (int, float)) and abs(v - mv) > 0.05:
            if not (d.get("reference") or "округл" in str(d.get("why", "")).lower()):
                err.append(f"{did}: номинал {v} ≠ модели {mv} без пояснения (справочный/округлён)")
    for g in t.get("gdt") or []:
        e = source_error(g.get("id"), g.get("source"), quests)
        if e:
            err.append(e)
        if g.get("feature") not in feats:
            err.append(f"{g.get('id')}: нет элемента {g.get('feature')}")
        if g.get("base") and g["base"] not in bases:
            err.append(f"{g.get('id')}: нет базы {g['base']}")
        if not g.get("why"):
            err.append(f"{g.get('id')}: нет why")
    ra_feats = {r.get("feature") for r in t.get("roughness") or []}
    for r in t.get("roughness") or []:
        if r.get("feature") not in feats:
            err.append(f"Ra: нет элемента {r.get('feature')}")
    tol_feats = {fid for d in t.get("dims") or [] if d.get("tolerance") for fid in d.get("features") or []}
    for fid, f in feats.items():
        fn = str(f.get("function", "")).lower()
        if any(w in fn for w in FIT_WORDS):
            if fid not in tol_feats:
                err.append(f"{fid}: посадочная поверхность без допуска/посадки")
            if fid not in ra_feats:
                err.append(f"{fid}: посадочная поверхность без Ra")
    for v in t.get("views") or []:
        if v.get("type") not in VIEW_TYPES:
            err.append(f"вид {v.get('id')}: тип {v.get('type')} не из {sorted(VIEW_TYPES)}")
        if not v.get("why"):
            err.append(f"вид {v.get('id')}: нет why")
        for fid in v.get("shows") or []:
            if fid not in feats:
                err.append(f"вид {v.get('id')}: нет элемента {fid}")
    all_ids = set(feats) | set(_ids(t.get("dims"))) | set(_ids(t.get("gdt"))) | views | bases
    for q in quests.values():
        if len(q.get("options") or []) < 2:
            err.append(f"{q.get('id')}: меньше двух вариантов ответа")
        for a in q.get("affects") or []:
            if a not in all_ids:
                err.append(f"{q.get('id')}: affects {a} — нет такого решения")
    return err


def _cell(x):
    s = "" if x is None else str(x)
    return s.replace("|", "/").replace("\n", " ")


def to_md(t):
    """Краткая версия для утверждения пользователем: что решено и почему, вопросы — в конце."""
    p = t.get("part", {})
    out = [f"# Таблица замысла: {p.get('marking', '')} {p.get('name', '')}", "",
           f"Модель: `{p.get('model', '')}` · материал: {p.get('material', '')}", ""]
    if t.get("summary"):
        out += [t["summary"], ""]
    out += ["## Элементы", "", "| id | элемент | геометрия | функция | откуда |", "|---|---|---|---|---|"]
    for f in t.get("features") or []:
        g = ", ".join(f"{k} {v}" for k, v in (f.get("geom") or {}).items())
        out.append(f"| {f.get('id')} | {_cell(f.get('kind'))} | {_cell(g)} | {_cell(f.get('function'))} | "
                   f"{_cell(f.get('source'))} |")
    if t.get("bases"):
        out += ["", "## Базы", "", "| база | элемент | роль | почему |", "|---|---|---|---|"]
        for b in t["bases"]:
            out.append(f"| {b.get('id')} | {b.get('feature')} | {_cell(b.get('role'))} | {_cell(b.get('why'))} |")
    out += ["", "## Виды", "", "| id | вид | показывает | масштаб | почему |", "|---|---|---|---|---|"]
    for v in t.get("views") or []:
        out.append(f"| {v.get('id')} | {_cell(v.get('type'))} | {', '.join(v.get('shows') or [])} | "
                   f"{_cell(v.get('scale'))} | {_cell(v.get('why'))} |")
    out += ["", "## Размеры", "", "| id | что | номинал | допуск | источник допуска | от | схема | вид | почему |",
            "|---|---|---|---|---|---|---|---|---|"]
    for d in t.get("dims") or []:
        val = _cell(d.get("value")) + (" *" if d.get("reference") else "")
        out.append(f"| {d.get('id')} | {d.get('type')} {', '.join(d.get('features') or [])} | {val} | "
                   f"{_cell(d.get('tolerance') or '—')} | {_cell(d.get('tol_source') or '—')} | "
                   f"{_cell(d.get('from'))} | {_cell(d.get('scheme'))} | "
                   f"{d.get('view')} | {_cell(d.get('why'))} |")
    if t.get("gdt"):
        out += ["", "## Допуски формы и расположения", "",
                "| id | элемент | допуск | значение | база | источник | почему |", "|---|---|---|---|---|---|---|"]
        for g in t["gdt"]:
            out.append(f"| {g.get('id')} | {g.get('feature')} | {_cell(g.get('kind'))} | {_cell(g.get('value'))} | "
                       f"{_cell(g.get('base') or '—')} | {_cell(g.get('source') or '—')} | {_cell(g.get('why'))} |")
    if t.get("roughness") or t.get("ra_general"):
        out += ["", "## Шероховатость", "", f"Общая (в углу): Ra {t.get('ra_general', '—')}", ""]
        if t.get("roughness"):
            out += ["| элемент | Ra | почему |", "|---|---|---|"]
            for r in t["roughness"]:
                out.append(f"| {r.get('feature')} | {_cell(r.get('ra'))} | {_cell(r.get('why'))} |")
    if t.get("tt"):
        out += ["", "## Технические требования", ""] + [f"{k}. {s}" for k, s in enumerate(t["tt"], 1)]
    if t.get("questions"):
        out += ["", "## Вопросы" + ("" if all(q.get("answer") for q in t["questions"])
                                     else " (без ответа решения выше — по варианту «по умолчанию»)"), ""]
        for q in t["questions"]:
            out.append(f"**{q.get('id')}. {q.get('text')}**  ")
            if q.get("answer"):
                out.append(f"Ответ: «{q['answer']}». Влияет на: {', '.join(q.get('affects') or [])}.")
            else:
                out.append("Варианты: " + " · ".join(q.get("options") or []) +
                           f". По умолчанию: {q.get('default', '—')}. Влияет на: {', '.join(q.get('affects') or [])}.")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    with open(args[0], encoding="utf-8") as f:
        t = json.load(f)
    errs = validate(t)
    if "--md" in args:
        with open(args[args.index("--md") + 1], "w", encoding="utf-8") as f:
            f.write(to_md(t))
    if errs:
        print("INTENT: ошибок", len(errs))
        for e in errs:
            print(" -", e)
        sys.exit(1)
    print(f"INTENT: OK ({len(t.get('features', []))} элементов, {len(t.get('dims', []))} размеров, "
          f"{len(t.get('gdt') or [])} допусков ФР, {len(t.get('views', []))} видов, "
          f"{len(t.get('questions') or [])} вопросов)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
