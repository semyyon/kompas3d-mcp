"""Правила проверки чертежа Ч01–Ч16 — чистая оценка без COM.

kompas_check_drawing (server.py) собирает факты чертежа через COM
(_collect_drawing_facts) и передаёт их сюда. Списки проекта (материалы, Ra,
ряд масштабов, запреты в ТТ, допуски) — данные в project_rules.json: поправить
конфигурацию можно без правки кода и без перезапуска MCP.

Формат фактов:
  {"formats": [3], "stamp": {"name","marking","material","mass","scale"},
   "views": [{"idx","name","kind": "proj|section|detail|system","scale",
              "scale_shown": bool,"has_geometry": bool,"bbox_paper": [x0,y0,x1,y1]|None}],
   "dims": [{"view","id","kind": "lin|dia|rad","text","text_xy_paper": [x,y]|None}],
   "leaders": [{"view","id","text"}], "sys_texts": [str], "tt_lines": [str]}
Необязательные поля (Ч12, задача 14c): у вида "title_rect_paper": [x0,y0,x1,y1] —
габарит заголовка («А-А», «Б (10:1)»); у выноски "text_rect_paper" — текст на полке,
"lines_paper": [[x1,y1,x2,y2], ...] — ветви от полки к стрелкам.
Листы (задача 14d): "sheet_markings" — графа 2 штампа каждого листа (Ч03 — на всех листах),
у вида "sheet" (1, 2…; нет — 1), "sys_text_sheets" — лист каждого
текста sys_texts (параллельный список; нет — лист 1). Ч12 сравнивает тексты только
одного листа, Ч13 ищет знак Ra на листе 1 и запрещает его на прочих (Р05).
Центры (Ч16, задача 15a) — у вида, все в СК вида, мм модели: "circles": [[xc,yc,r]],
"arcs": [[xc,yc,r,sweep°]], "centre_markers": [[x,y,SignType]] (ISymbols2DContainer.CentreMarkers;
без SignType — старые факты, считается крестом),
"axes": [[x1,y1,x2,y2]] (AxisLines + отрезки стиля 3). Нет "circles" — вид не судится.
"""
import json
import math
import os
import re

RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_rules.json")

# Не конфигурация проекта, а защита от недостоверных координат: точки вне
# листа (-50..1300 x -50..900 мм) не судим — калибровка на пилоте, вид 4.
_SANE_X, _SANE_Y = (-50.0, 1300.0), (-50.0, 900.0)
_INSIDE_MARGIN = 1.0       # мм: текст ближе 1 мм к краю bbox — ещё не «внутри»
_THREAD_RE = re.compile(r"М\d+([xх]\d+(?:,\d+)?)?-\d?[Hh6g]", re.IGNORECASE)


def load_rules(path=None) -> dict:
    with open(path or RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


_FIELD_RE = re.compile(r"\^\(#(\d+)~")


def stamp_text(raw: str) -> str:
    """Отображаемый текст ячейки штампа. Поле-ссылка КОМПАС хранится в Str как
    `^(#N~ЗНАЧЕНИЕ)служебное~` (N — длина значения; так в графе «Масштаб»:
    `^(#5~1:2,5)1;288314011823.000000;1~`) — оставляем только ЗНАЧЕНИЕ.
    Обычный текст возвращается как есть. Сверено с ITextItem.Str (тип 8192)."""
    raw = raw or ""
    out, pos = [], 0
    for m in _FIELD_RE.finditer(raw):
        if m.start() < pos:
            continue
        val_end = m.end() + int(m.group(1))
        close = raw.find("~", val_end)
        if raw[val_end:val_end + 1] != ")" or close < 0:
            continue                     # не похоже на поле — не трогаем
        out.append(raw[pos:m.start()])
        out.append(raw[m.end():val_end])
        pos = close + 1
    out.append(raw[pos:])
    return "".join(out)


def _sane(x, y):
    return _SANE_X[0] < x < _SANE_X[1] and _SANE_Y[0] < y < _SANE_Y[1]


def _pt_in(px, py, r, margin=0.0):
    return (r[0] + margin) < px < (r[2] - margin) and (r[1] + margin) < py < (r[3] - margin)


SHEET_SIZES = {3: (297.0, 420.0), 4: (210.0, 297.0)}   # (короткая, длинная), мм


def sheet_x_ranges(formats, verticals=None):
    """Бумажные полосы X листов [(x0, x1), ...]: КОМПАС кладёт лист N+1 вплотную правее
    листа N (замер 14d: лист 2 А3 гориз. начинается с x = 420). verticals — признак
    вертикальной ориентации каждого листа (нет — горизонтальные)."""
    out, x = [], 0.0
    for i, f in enumerate(formats):
        short, long_ = SHEET_SIZES.get(int(f), (297.0, 420.0))
        w = short if (verticals and verticals[i]) else long_
        out.append((x, x + w))
        x += w
    return out


def sheet_frame(fmt, vertical=False):
    """Внутренняя рамка листа в СВОЕЙ СК (x0, y0, x1, y1): поле подшивки 20 мм слева,
    5 мм с прочих сторон (ГОСТ 2.301; tt_area в jobs/_draw.py — та же рамка)."""
    short, long_ = SHEET_SIZES.get(int(fmt), (297.0, 420.0))
    w, h = (short, long_) if vertical else (long_, short)
    return (20.0, 5.0, w - 5.0, h - 5.0)


def sheet_of_x(x, ranges):
    """Номер листа (1…) по бумажной X в общей СК чертежа; вне всех полос — ближайший."""
    if not ranges:
        return 1
    for i, (a, b) in enumerate(ranges):
        if a <= x < b:
            return i + 1
    return 1 if x < ranges[0][0] else len(ranges)


def is_nonmetal(material: str, rules: dict) -> bool:
    """Материал штампа — неметалл проекта (Ч04/Ч13; штриховка ГОСТ 2.306 «в клетку»)."""
    material = material or ""
    return (material in rules["nonmetal_materials"]
            or any(m in material for m in rules["nonmetal_marks"]))


# Марка в форме ГОСТ 2.109 п.6.3: «<марка> ГОСТ/ТУ/ОСТ/СТО <номер>» («Сталь 40 ГОСТ 1050-2013»).
_GOST_FORM_RE = re.compile(r"\S.*\s(ГОСТ|ТУ|ОСТ|СТО)\s*\S")


def is_gost_form(material: str, rules: dict) -> bool:
    """Материал записан по форме стандарта на материал (решение пользователя 2026-10-15:
    принимать любую марку «… ГОСТ/ТУ …», если project_rules.json:material_gost_form)."""
    return bool(rules.get("material_gost_form")) and bool(_GOST_FORM_RE.search(material or ""))


# Разметка текста размерной надписи КОМПАС (замер эталона ACME.100.04.00.001, 2026-10-15):
# рамка теоретически точного размера — «@80~$(752)$», спецсимвол «°» — «@1~».
_FRAME_RE = re.compile(r"@80~\$\((.*?)\)\$")
_SPECIAL_RE = re.compile(r"@(\d+)~")
_SPECIAL_SYMBOLS = {"1": "°"}


def plain_dim_text(s: str) -> str:
    """Текст размера без разметки: «@80~$(67,5)$» → «67,5», «@80~$(45@1~)$» → «45°»."""
    s = _FRAME_RE.sub(lambda m: m.group(1), s or "")
    return _SPECIAL_RE.sub(lambda m: _SPECIAL_SYMBOLS.get(m.group(1), ""), s)


def is_framed(s: str) -> bool:
    """Текст размера в рамке разметкой (IDimensionText.Rectangle собирается отдельно)."""
    return bool(_FRAME_RE.search(s or ""))


def pos_tol_for_d(d: float, rules: dict) -> str:
    """Значение позиционного допуска группы отверстий по Ø (project_rules.json:pos_tol_by_d —
    [[Ø_макс, "значение"], …, [null, "значение"]]; практика эталона: ≤15 → 0,5, ≤300 → 1, иначе 2)."""
    table = rules.get("pos_tol_by_d") or [[15, "0,5"], [300, "1"], [None, "2"]]
    for lim, val in table:
        if lim is None or float(d) <= float(lim):
            return val
    return table[-1][1]


_HOLES_RE = re.compile(r"(\d+)\s*отв")


# Текст на полке выноски: высота над полкой (шрифт 5 мм) — замер v23, задача 14c:
# «4 фаски 1,5х45°» — полка y 281,0, верх габарита 289,1.
LEADER_TEXT_H = 8.1


def leader_text_rect(gab, shelf_xy, direction, h=LEADER_TEXT_H):
    """Бумажный прямоугольник текста на полке выноски: от точки полки (X0,Y0 на
    листе) в сторону полки (ShelfDirection 1 — вправо, -1 — влево) до края габарита
    выноски, по высоте — h над полкой. Иное направление/нет габарита — None."""
    if not gab or shelf_xy is None:
        return None
    x0, y0 = shelf_xy
    if direction == 1:
        x1, x2 = x0, gab[2]
    elif direction == -1:
        x1, x2 = gab[0], x0
    else:
        return None
    if x2 - x1 < 0.5:
        return None
    return [round(x1, 1), round(y0, 1), round(x2, 1), round(y0 + h, 1)]


def rects_cross(a, b):
    """Прямоугольники (x0,y0,x1,y1) пересекаются по площади (касание — нет)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def seg_rect_cross(seg, r):
    """Отрезок (x1,y1,x2,y2) проходит через внутренность прямоугольника r
    (Лианг–Барски; касание границы — нет)."""
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - r[0]), (dx, r[2] - x1), (-dy, y1 - r[1]), (dy, r[3] - y1)):
        if abs(p) < 1e-12:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return True


TOL_FRAME_W, TOL_FRAME_H = 29.4, 10.0   # мм листа: рамка «⊕ Ø0,5» шрифтом 5 (Р2 plate_leap)
CENTRE_TOL = 0.5          # мм вида: маркер/ось «в центре» окружности (Ч16)
ARC_MIN_SWEEP = 120.0     # град.: дуга контура больше — её центр отмечается (Ч16);
                          # скругления 90° не судятся (Стойка: R45 154°, ушки R10 180°)


def arc_sweep(xc, yc, x1, y1, x2, y2, x3, y3):
    """Дуга по центру, концам и точке на дуге (IArc X3/Y3): (начальный угол, угол
    дуги) в градусах, обход против часовой от начала."""
    a1 = math.degrees(math.atan2(y1 - yc, x1 - xc)) % 360.0
    a2 = math.degrees(math.atan2(y2 - yc, x2 - xc)) % 360.0
    a3 = math.degrees(math.atan2(y3 - yc, x3 - xc)) % 360.0
    sw = (a2 - a1) % 360.0
    if (a3 - a1) % 360.0 > sw:          # точка на дуге не попала — дуга идёт по часовой
        a1, sw = a2, 360.0 - sw
    return a1, sw


def _through(ax, x, y, tol):
    """Ось-отрезок проходит через точку (расстояние до ОТРЕЗКА ≤ tol)."""
    x1, y1, x2, y2 = ax
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
    return math.hypot(x1 + t * dx - x, y1 + t * dy - y) <= tol


CM_TWO_AXIS = 2           # ksCMTwoAxis; 0 — крестик-точка, 1 — одна ось: не крест (Ч16)


def centre_covered(x, y, markers, axes, r=0.0, tol=CENTRE_TOL):
    """Центр окружности радиуса r отмечен крестом в ОБЕ стороны (задача 15b): маркер
    «две оси» рядом или две непараллельные оси через центр, каждая через всю
    окружность (от −r до +r вдоль себя). Одна ось симметрии через отверстие — не крест.
    markers: [(x, y)] или [(x, y, SignType)]."""
    if any(math.hypot(m[0] - x, m[1] - y) <= tol and (len(m) < 3 or m[2] == CM_TWO_AXIS)
           for m in markers):
        return True
    dirs = []
    for ax in axes:
        if _through(ax, x, y, tol):
            L = math.hypot(ax[2] - ax[0], ax[3] - ax[1]) or 1.0
            u = ((ax[2] - ax[0]) / L, (ax[3] - ax[1]) / L)
            t = sorted(((ax[k] - x) * u[0] + (ax[k + 1] - y) * u[1]) for k in (0, 2))
            if t[0] <= -(r - tol) and t[1] >= r - tol:
                dirs.append(u)
    return any(abs(a[0] * b[1] - a[1] * b[0]) > 0.5 for a in dirs for b in dirs)


def _scale_txt(vs):
    return ("1:%g" % (1 / vs)).replace(".", ",") if vs < 1 else ("%g:1" % vs).replace(".", ",")


def evaluate(facts: dict, rules: dict) -> list:
    checks = []

    def add(code, title, ok, detail="", obj="", hint="", warn=False):
        # warn=True: нарушение — предупреждение (ok остаётся True, чистовой сохраняется),
        # текст и объект сохраняются в "warning" для отчёта и JSON
        checks.append({"code": code, "title": title, "ok": bool(ok) or bool(warn), "detail": detail,
                       "object": "" if ok else obj, "fix_hint": "" if ok else hint,
                       "warning": bool(warn and not ok)})

    hints = facts.get("hints") or {}

    # --- листы и штамп -----------------------------------------------------
    fmts = list(facts.get("formats") or [])
    allowed = rules["formats"]
    bad_sheets = [f"лист {i + 1}" for i, f in enumerate(fmts) if f not in allowed]
    add("Ч01", "формат А3/А4 (каждый лист)", not bad_sheets,
        f"листов {len(fmts)}: " + ", ".join(f"лист {i + 1} — {f}" for i, f in enumerate(fmts)),
        ", ".join(bad_sheets) or "листы",
        f"формат листа — один из {allowed} (3=А3, 4=А4); тесно — второй лист того же формата (Р05)")

    st = facts.get("stamp") or {}
    name, marking = st.get("name", ""), st.get("marking", "")
    material, mass, scale_txt = st.get("material", ""), st.get("mass", ""), st.get("scale", "")
    add("Ч02", "штамп: наименование", bool(name), name or "пусто",
        "штамп, графа 1", "заполнить наименование детали в штампе")
    # обозначение на КАЖДОМ листе (лист 2 — форма 2а, графа 2) и то же, что на листе 1
    sm = list(facts.get("sheet_markings") or [])
    bad_m = [f"лист {i + 1}" for i, m in enumerate(sm) if i and (m or "").strip() != marking]
    others = "; ".join(f"лист {i + 1}: {(m or '').strip() or 'пусто'}" for i, m in enumerate(sm) if i)
    add("Ч03", "штамп: обозначение (каждый лист)", bool(marking) and not bad_m,
        (marking or "пусто") + (f"; {others}" if others else ""),
        ", ".join((["штамп, графа 2"] if not marking else []) + [f"{b}, графа 2" for b in bad_m]),
        "заполнить обозначение в штампе; лист 2 (форма 2а) — то же обозначение, что на листе 1")

    # Происхождение списков (перенесено из server.py): неметаллы — решение
    # пользователя 2026-09-02 (Ra 12,5 как у прокладок ACME), капролон/Ф-4 —
    # PRJ.02 / ACME.100.01 (2026-09-30). Металлы: PRJB — AISI 304 (эталон ACME);
    # PRJC — 12Х18Н10Т, 95Х18, БрАЖ9-4; PRJ.02 — М0б, М1, Сталь 10, АД0.
    # Строка в штампе бывает с «Сталь » и с ГОСТ-суффиксом — проверяем вхождение марки.
    nonmetal = is_nonmetal(material, rules)
    is_known_metal = any(m in material for m in rules["metal_marks"])
    # Сборочный чертёж (обозначение «… СБ»): графа материала пуста,
    # знак шероховатости не ставится (эталоны ACME, ГОСТ Р 2.109 п.7).
    is_assembly = marking.rstrip().endswith("СБ")
    if is_assembly:
        add("Ч04", "штамп: материал (СБ — графа должна быть пустой)",
            material == "", material or "пусто", "штамп, графа 3",
            "на сборочном чертеже графу материала оставить пустой")
    else:
        add("Ч04", "штамп: материал из списка проекта (металл или неметалл) или в форме ГОСТ/ТУ",
            is_known_metal or nonmetal or is_gost_form(material, rules), material or "пусто",
            "штамп, графа 3",
            "материал не из списка и не в форме «марка ГОСТ/ТУ …»; если это верный материал проекта — "
            "добавить марку в metal_marks/nonmetal_marks в project_rules.json")
    add("Ч05", "штамп: масса", bool(mass), mass or "пусто",
        "штамп, графа 5", "заполнить массу в штампе (из модели)")
    add("Ч06", "штамп: масштаб из ряда ГОСТ 2.302",
        scale_txt in rules["gost_2302_scales"], scale_txt or "пусто", "штамп, графа 6",
        "масштаб из ряда ГОСТ 2.302; ряд масштабов — project_rules.json:gost_2302_scales")

    # --- виды ----------------------------------------------------------------
    real = [v for v in facts.get("views") or [] if v.get("has_geometry")]
    by_idx = {v["idx"]: v for v in real}
    per_sheet = {}
    for v in real:
        if v.get("kind") != "system":
            per_sheet[v.get("sheet") or 1] = per_sheet.get(v.get("sheet") or 1, 0) + 1
    add("Ч07", "не меньше двух видов", len(real) >= 2, f"видов с геометрией: {len(real)}"
        + ("; по листам " + ", ".join(f"{k}: {n}" for k, n in sorted(per_sheet.items()))
           if len(per_sheet) > 1 else ""),
        "виды", "добавить вид: деталь не определяется одним видом")

    bad_titles, bad_obj = [], []
    for v in real:
        if v.get("kind") == "system" or v.get("scale") is None:
            continue          # системный вид всегда 1:1, он не «вид детали»
        s_txt = _scale_txt(v["scale"])
        if (s_txt != scale_txt and not v.get("scale_shown")
                and "(" not in (v.get("name") or "")):
            bad_titles.append(f"вид {v['idx']} «{v.get('name')}» {s_txt}")
            bad_obj.append(f"v{v['idx']}")
    add("Ч08", "масштаб не по штампу — указан в заголовке", not bad_titles,
        "; ".join(bad_titles) or "все совпадают или подписаны", ", ".join(bad_obj),
        "включить показ масштаба в обозначении вида (IViewDesignation.ShowScale)")

    # --- размеры -------------------------------------------------------------
    dims = [d for d in facts.get("dims") or [] if d.get("view") in by_idx]
    dec_re = re.compile(r"\d+,(\d{%d,})" % (int(rules["dim_decimals_max"]) + 1))
    deep = [d for d in dims if dec_re.search(plain_dim_text(d.get("text")))]
    add("Ч09", "размеры округлены до десятых", not deep,
        "; ".join(f"v{d['view']} {d['id']}: {d['text']}" for d in deep[:4])
        or f"проверено размеров: {len(dims)}",
        ", ".join(f"v{d['view']}.{d['id']}" for d in deep),
        f"округлить номинал до {rules['dim_decimals_max']} знака после запятой")

    # позиции текстов: только линейные (у них есть X3/Y3). Координаты есть у всех
    # видов, включая разрезы/выносные (задача 4: D.dim_text_paper); если нет (None) —
    # это предупреждение, не ошибка.
    lin = [d for d in dims if d.get("kind") == "lin"]
    no_xy = [d for d in lin if d.get("text_xy_paper") is None
             and by_idx[d["view"]].get("kind") in ("section", "detail")]
    warn = (f"; не проверено (нет координат, разрез/выносной): {len(no_xy)}" if no_xy else "")

    # Ч10 судит только проекции: bbox разреза/выносного — не контур детали
    # (уступы тел вращения, осевые за контуром, неклипованная геометрия
    # выносного). Э0: 8 ложных Ч10 на 3 утверждённых чертежах, верных — 0.
    sec = [d for d in lin if d.get("text_xy_paper") is not None
           and by_idx[d["view"]].get("kind") in ("section", "detail")]
    warn10 = warn + (f"; разрезы/выносные по Ч10 не судятся: {len(sec)}" if sec else "")
    inside, judged = [], 0
    on_image = 0
    for d in lin:
        v, xy = by_idx[d["view"]], d.get("text_xy_paper")
        if hints.get("dia_on_image") and (d.get("sign") in (1, 4) or str(d.get("prefix") or "").lstrip()[:1] in ("Ø", "М", "M")):
            on_image += 1      # Ø ступени на изображении — решение утверждённой таблицы (ГОСТ 2.307)
            continue
        r = v.get("bbox_paper")
        if xy is None or r is None or v.get("kind") in ("system", "section", "detail"):
            continue
        if _sane(*xy) and _sane(r[0], r[1]):
            judged += 1
            if _pt_in(xy[0], xy[1], r, _INSIDE_MARGIN):
                inside.append((d, f"v{d['view']}.{d['id']} текст ({xy[0]:.0f},{xy[1]:.0f}) внутри контура"))
    add("Ч10", "текст размера вне контура вида", not inside,
        ("; ".join(s for _, s in inside[:4]) or f"линейных проверено: {judged}") + warn10
        + (f"; Ø/М на изображении по таблице замысла: {on_image}" if on_image else ""),
        ", ".join(f"v{d['view']}.{d['id']}" for d, _ in inside),
        "перенести текст размера наружу контура вида: x3/y3 за bbox_paper ±10 мм")

    holes = [l for l in facts.get("leaders") or [] if l.get("view") in by_idx
             and "отв" in (l.get("text") or "").lower()
             and ("ø" in l["text"].lower() or "b<" in l["text"].lower())
             and not _THREAD_RE.search(l["text"])]
    add("Ч11", "отверстия: резьба М..-6H или линейный размер, не Ø-текст", not holes,
        "; ".join(f"v{l['view']}.{l['id']}: {l['text'][:40]}" for l in holes[:3]) or "нарушений нет",
        ", ".join(f"v{l['view']}.{l['id']}" for l in holes),
        "резьбу обозначить М..-6H, гладкое отверстие — линейным размером, не Ø-текстом на выноске")

    # пересечения ТЕКСТОВ (полные габариты размеров не годятся: выносные линии
    # «лесенки» пересекаются по определению). Текст размера — квадрат ±tol/2 у точки
    # текста (для двух размеров это прежнее |dx|,|dy| < tol); текст выноски и заголовок
    # вида — их бумажные габариты; ветви выносок — отрезки (свой текст выноски с ними
    # не сравнивается). Задача 14c: на .007 текст размера и заголовок лежали на выноске.
    tol = float(rules["text_overlap_mm"])
    h = tol / 2
    # каждый текст — со своим листом (лист вида): листы не сравниваются (задача 14d)
    def sh(vi):
        return by_idx[vi].get("sheet") or 1
    # (имя, прямоугольник, владелец-выноска, лист, оценочная позиция): Ø/R-размеры несут
    # text_xy_paper как ОЦЕНКУ (est, задача 16) — их наложения только предупреждение
    boxes = [(f"v{d['view']}.{d['id']}", [x - h, y - h, x + h, y + h], None, sh(d["view"]),
              bool(d.get("est")))
             for d in dims if d.get("text_xy_paper") is not None and _sane(*d["text_xy_paper"])
             for x, y in [d["text_xy_paper"]]]
    led = [l for l in facts.get("leaders") or [] if l.get("view") in by_idx]
    boxes += [(f"v{l['view']}.{l['id']}", l["text_rect_paper"], f"v{l['view']}.{l['id']}",
               sh(l["view"]), False)
              for l in led if l.get("text_rect_paper") and _sane(*l["text_rect_paper"][:2])]
    boxes += [(f"v{v['idx']}.title", v["title_rect_paper"], None, v.get("sheet") or 1, False)
              for v in real
              if v.get("title_rect_paper") and _sane(*v["title_rect_paper"][:2])
              and v["title_rect_paper"][2] - v["title_rect_paper"][0] > 0.1]
    # рамки допусков — по точке привязки и BasePointPos (Р2 plate_leap: 29,4×10 мм при шрифте 5),
    # оценочно: их наложения — предупреждение
    for k, t in enumerate(facts.get("tolerances") or []):
        a, bp = t.get("anchor_paper"), int(t.get("base_pos") or 3)
        if not a or t.get("view") not in by_idx or not _sane(*a):
            continue
        w, hh = TOL_FRAME_W, TOL_FRAME_H
        x1 = a[0] if bp in (1, 3, 9) else a[0] - w
        y1 = a[1] if bp in (1, 7, 9) else a[1] - hh
        boxes.append((f"v{t['view']}.{t.get('id') or 'tol%d' % k}", [x1, y1, x1 + w, y1 + hh], None,
                      sh(t["view"]), True))
    lines = [(f"v{l['view']}.{l['id']}", s, sh(l["view"]))
             for l in led for s in l.get("lines_paper") or []]
    pairs, soft = [], []
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            if boxes[a][3] == boxes[b][3] and rects_cross(boxes[a][1], boxes[b][1]):
                (soft if boxes[a][4] or boxes[b][4] else pairs).append(f"{boxes[a][0]}~{boxes[b][0]}")
    for n, r, own, sn, est in boxes:
        for owner, s, so in lines:
            if owner != own and sn == so and seg_rect_cross(s, r):
                (soft if est else pairs).append(f"{n}~{owner}(линия)")
    soft_txt = f"; возможно (оценка позиции Ø/R): {'; '.join(soft[:4])}" if soft else ""
    add("Ч12", "тексты размеров, выносок и заголовки видов не налезают", not pairs,
        ("; ".join(pairs[:5]) or f"текстов проверено: {len(boxes)}") + warn + soft_txt,
        ", ".join(pairs), f"разнести тексты размеров ≥{tol:g} мм (шаг лесенки 8 мм); "
        "текст/заголовок на выноске — сдвинуть полку (leaders[].shelf), вид (x, y) или "
        "сторону размера (side)")

    # --- шероховатость и ТТ ----------------------------------------------------
    all_texts = list(facts.get("sys_texts") or [])
    t_sheets = list(facts.get("sys_text_sheets") or [])
    t_sheets += [1] * (len(all_texts) - len(t_sheets))
    texts = [t for t, n in zip(all_texts, t_sheets) if n == 1]          # Ra — только лист 1 (Р05)
    ra_other = [f"лист {n}" for t, n in zip(all_texts, t_sheets) if n != 1 and "Ra " in t]
    if ra_other:
        add("Ч13", "знак шероховатости только на листе 1 (Р05)", False,
            f"знак Ra на: {', '.join(ra_other)}", ", ".join(ra_other),
            "убрать знак шероховатости с листа 2 — он ставится только на листе 1 (Р05)")
    elif is_assembly:
        add("Ч13", "на СБ знака шероховатости нет", not any("Ra " in t for t in texts),
            f"текстов в системном виде: {len(texts)}", "системный вид",
            "убрать знак шероховатости со сборочного чертежа")
    elif hints.get("ra_declined"):
        # решение пользователя 2026-10-15: "ra": null в spec — явный отказ от знака
        add("Ч13", "знак шероховатости не ставится по решению spec (ra: null)",
            not any("Ra " in t for t in texts),
            "по решению spec (ra: null) знак не требуется; " + f"текстов в системном виде: {len(texts)}",
            "системный вид", "в spec ra: null, а знак Ra на листе есть — убрать знак или задать ra")
    else:
        need = (hints.get("ra_spec") or rules["ra_by_marking"].get(marking)
                or rules["ra_default"]["nonmetal" if nonmetal else "metal"])
        alt = need.replace(",", ".")
        add("Ч13", f"знак {need} присутствует (радикал в углу)",
            any(need in t or alt in t for t in texts),
            f"текстов в системном виде: {len(texts)}", "системный вид",
            f"поставить знак «{need}» в правом верхнем углу (неметалл — ra_default.nonmetal)")

    tt = list(facts.get("tt_lines") or [])
    add("Ч14", "технические требования присутствуют", bool(tt), f"строк ТТ: {len(tt)}",
        "ТТ", "добавить технические требования")
    cert = [s for s in tt if any(w in s.lower() for w in rules["tt_forbidden"])]
    add("Ч15", "в ТТ нет пункта о сертификате входного контроля", not cert,
        cert[0][:60] if cert else "", "ТТ", "удалить пункт о сертификате/входном контроле из ТТ")

    # --- центры (ГОСТ 2.303/2.305): окружности и большие дуги проекционных видов --------
    # Разрезы/выносные не судятся: оси отверстий в разрезе КОМПАС строит сам (AxisLines, Э7).
    bare, n_c, n_m, n_a, judged16 = [], 0, 0, 0, 0
    for v in real:
        # проекции и выносные (эталон ACME.100.04.00.001: кресты на всех отверстиях выносных)
        if v.get("kind") not in ("proj", "detail") or v.get("circles") is None:
            continue
        judged16 += 1
        mk, axes = v.get("centre_markers") or [], v.get("axes") or []
        n_m, n_a = n_m + len(mk), n_a + len(axes)
        pts = [(c[0], c[1], c[2]) for c in v["circles"]]
        n_c += len(pts)
        pts += [(a[0], a[1], a[2]) for a in v.get("arcs") or [] if a[3] > ARC_MIN_SWEEP]
        # соосные — одна точка с наибольшим радиусом (оси должны пройти через всю окружность)
        pts.sort(key=lambda p: -p[2])
        seen = []
        for x, y, r in pts:
            if any(math.hypot(x - a, y - b) <= CENTRE_TOL for a, b in seen):
                continue
            seen.append((x, y))
            if not centre_covered(x, y, mk, axes, r=r):
                bare.append((v["idx"], x, y))
    add("Ч16", "центровые: у окружностей и дуг контура проекций крест в обе стороны "
        "(маркер «две оси» или две оси через всю окружность)",
        not bare,
        ("; ".join(f"v{i} центр ({x:.0f},{y:.0f}) без отметки" for i, x, y in bare[:4])
         or f"видов {judged16}: окружностей {n_c}, маркеров центра {n_m}, осей {n_a}"),
        ", ".join(sorted({f"v{i}" for i, _, _ in bare})),
        "оси симметрии вида — D.add_symmetry_axes(view) (стиль 3, 3 мм за контур); "
        "центр без маркера — D.add_centre_marks(view) (сборка kompas_build_drawing делает это сама)")

    # --- группы отверстий (ГОСТ 2.307 п.4.14, эталон плиты): «N отв.» — число на изображении ---
    bad17, n17 = [], 0
    for d in dims:
        m = _HOLES_RE.search(d.get("prefix") or "")
        v = by_idx[d["view"]]
        if d.get("kind") != "dia" or not m or v.get("circles") is None:
            continue
        try:
            dval = float(plain_dim_text(d.get("text")).replace(",", "."))
        except ValueError:
            continue                      # резьба «М4-6H» и т.п. — не Ø
        n17 += 1
        cnt = sum(1 for c in v["circles"] if abs(2 * c[2] - dval) <= 0.05)
        if int(m.group(1)) > cnt:
            bad17.append((d, cnt))
    add("Ч17", "«N отв.» не больше числа таких отверстий на изображении "
        "(повторяющийся узел — «k места» в заголовке выносного)", not bad17,
        "; ".join(f"v{d['view']}.{d['id']}: «{d['prefix'].strip()}» при {c} отв. Ø{plain_dim_text(d['text'])}"
                  for d, c in bad17[:4]) or f"групп проверено: {n17}",
        ", ".join(f"v{d['view']}.{d['id']}" for d, _ in bad17),
        "в выносном одного узла считать отверстия узла («8 отв.»), повтор — title_prefix «2 места»")

    # --- рамки теоретически точных размеров ↔ допуски расположения (ГОСТ Р 2.308) — предупреждение
    framed = [d for d in dims if d.get("frame") or is_framed(d.get("text"))]
    # рамки ТТ-размеров нужны только позиционному допуску (знак 27); биение, цилиндричность и
    # прочие допуски формы/расположения их не требуют (вал ACME: ложное Ч18 на 4 рамках)
    tols = [t for t in facts.get("tolerances") or [] if t.get("view") in by_idx
            and int(t.get("sign", 27) or 27) == 27]
    if framed or tols:
        add("Ч18", "размеры в рамке есть ↔ есть допуски расположения (ГОСТ Р 2.308)",
            bool(framed) == bool(tols),
            f"в рамке: {len(framed)}, рамок допуска: {len(tols)}",
            ", ".join(f"v{d['view']}.{d['id']}" for d in framed[:4]) or ", ".join(
                f"v{t['view']}.tol{i}" for i, t in enumerate(tols[:4])),
            "теоретически точный размер (frame) имеет смысл только с допуском расположения на группу "
            "(dims[].tol) — и наоборот", warn=True)
    hits = facts.get("title_hits")
    if hits is not None:
        add("Ч19", "тексты обозначений и заголовки видов не пересекают чужие линии и тексты", not hits,
            "; ".join(hits[:4]) or "пересечений нет", ", ".join(hits[:6]),
            "развести: сдвинуть вид (x, y), размер (pos/row/side), полку выноски или рамку так, "
            "чтобы текст стоял на свободном месте")
    return checks


def annotation_hits(parts, shrink=0.8):
    """Пересечения «текст обозначения × чужая линия/чужой текст»: parts {имя: (отрезки, тексты)}
    в мм листа. Отрезки короче 4 мм (стрелки) не участвуют (их отфильтровывает сборщик фактов);
    габарит текста сжат на shrink мм (запас КОМПАС, рамка допуска под полкой касается краем)."""
    out = []
    names = list(parts)
    for a in names:
        for t in parts[a][1]:
            box = (t[0] + shrink, t[1] + shrink, t[2] - shrink, t[3] - shrink)
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            for b in names:
                if b == a:
                    continue
                segs, txts = parts[b]
                if any(seg_hits_rect(q, box) for q in segs) or any(
                        u[0] < box[2] and box[0] < u[2] and u[1] < box[3] and box[1] < u[3] for u in txts):
                    out.append(f"{a} × {b}")
                    break
    return sorted(set(out))


def seg_hits_rect(s, b):
    """Отрезок s (x1,y1,x2,y2) задевает прямоугольник b (x1,y1,x2,y2) — отсечение Лианга-Барски."""
    x1, y1, x2, y2 = s[:4]
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - b[0]), (dx, b[2] - x1), (-dy, y1 - b[1]), (dy, b[3] - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


_SNAPSHOT_FIELDS = ("views", "dims", "texts", "conflicts")


def _r1(v):
    return round(float(v), 1)


def _in_view(obj_token: str, view: int) -> bool:
    """Токен объекта проверки («v3.lin1», «v1.lin0~v1.lin1», «v2») относится к виду."""
    pat = re.compile(r"v%d(?!\d)" % view)
    return bool(pat.search(obj_token))


def snapshot_from_facts(facts: dict, view: int = -1, fields: str = "views,dims,texts,conflicts",
                        rules: dict = None) -> dict:
    """Чертёж как компактный словарь — замена скриншотов для проверки позиций и
    значений (kompas_snapshot). Чистая функция без COM.

    view != -1 — только этот вид (idx): его размеры, выноски и конфликты; ТТ и
    системные тексты — только для view=0 (системный вид). fields — подмножество
    views, dims, texts, conflicts через запятую; штамп (идентификация чертежа)
    присутствует всегда. Координаты в мм на листе, округлены до 0,1.
    conflicts — не прошедшие проверки evaluate (code, obj, fix); без rules
    берутся project_rules.json."""
    want = {f.strip() for f in (fields or "").split(",")} & set(_SNAPSHOT_FIELDS)
    one = view != -1
    st = facts.get("stamp") or {}
    out = {"stamp": {k: st.get(k, "") for k in ("name", "marking", "material", "mass", "scale")}}

    if "views" in want:
        vs = []
        for v in facts.get("views") or []:
            if one and v["idx"] != view:
                continue
            e = {"i": v["idx"], "name": v.get("name") or "", "kind": v.get("kind"),
                 "scale": _scale_txt(v["scale"]) if v.get("scale") else None}
            if v.get("scale_shown"):
                e["shown"] = True
            if v.get("sheet"):
                e["sheet"] = v["sheet"]
            if v.get("bbox_paper"):
                e["bb"] = [_r1(x) for x in v["bbox_paper"]]
            vs.append(e)
        out["views"] = vs

    if "dims" in want:
        ds = []
        for d in facts.get("dims") or []:
            if one and d["view"] != view:
                continue
            e = {"v": d["view"], "id": d["id"], "t": plain_dim_text(d.get("text"))}
            if d.get("prefix"):
                e["pre"] = d["prefix"]
            if d.get("frame") or is_framed(d.get("text")):
                e["frame"] = True
            if d.get("text_xy_paper"):
                e["xy"] = [_r1(x) for x in d["text_xy_paper"]]
                if d.get("est"):
                    e["est"] = True
            ds.append(e)
        out["dims"] = ds

    if "texts" in want:
        tx = {"leaders": [{"v": l["view"], "id": l["id"], "t": l.get("text") or ""}
                          for l in facts.get("leaders") or [] if not one or l["view"] == view]}
        if not one or view == 0:
            tx["sys"] = list(facts.get("sys_texts") or [])
            tx["tt"] = list(facts.get("tt_lines") or [])
        out["texts"] = tx

    if "conflicts" in want:
        cs = []
        for c in evaluate(facts, rules or load_rules()):
            if c["ok"]:
                continue
            obj = c["object"]
            if one:
                toks = [t for t in obj.split(", ") if _in_view(t, view)]
                if not toks:
                    continue          # глобальный конфликт или другой вид
                obj = ", ".join(toks)
            cs.append({"code": c["code"], "obj": obj, "fix": c["fix_hint"]})
        out["conflicts"] = cs
    return out
