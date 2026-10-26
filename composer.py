# -*- coding: utf-8 -*-
"""Компоновщик листа: утверждённая таблица замысла (tools/intent_table.py) → drawing_spec.json
для kompas_build_drawing. Детерминированный код, без LLM: решения «что образмерить» уже приняты
конструктором и пользователем, здесь — только «где» (этап 2в, docs/drawings-rework-2026-10-19.md).

    python -X utf8 composer.py <intent.json> <чистовой.cdw> [--out <spec.json>] [--stamp '{"first_use": "…"}']

Класс: тело вращения с осью X модели (вал, ось, втулка) — главный вид «#Слева» (ось горизонтально,
СК вида = (x, z) модели), А3 горизонтально. Правила:
  * Ø ступеней и резьба — на самой ступени (размерная линия внутри контура, pos), не лесенкой
    сбоку: длинные выносные через весь вал — главный перегруз базовой линии;
  * длины — ряды снизу по ГОСТ 2.307: первый ROW0 мм от контура, далее ROW_STEP; раскладка —
    упаковка интервалов (цепочка в одном ряду, габарит — в последнем);
  * сверху — колонки выносок (радиусы, рамки допусков) в порядке анкеров вдоль оси: при
    сохранённом порядке и общей высоте полок ножки не пересекаются; каждая ножка дополнительно
    проверяется на пересечение с профилем — ЖЁСТКОЕ ограничение (ошибка, а не штраф);
  * знаки Ra — на линии поверхности сверху, не ближе RA_GAP мм к анкерам выносок;
  * базы — снизу под поверхностью, между контуром и первым рядом;
  * расточка с торца — разрез вдоль оси до уступа за дном + волнистая линия обрыва; паз — поперечное
    сечение; канавка — выносной элемент.
Буквы видов не повторяют буквы баз (ГОСТ 2.316): берутся по алфавиту после занятых.

Ещё два класса (part.class таблицы, part_kind; PRJ.02, 2026-10-21):
  * втулка с осью Z модели — compose_bush: торец «#Сверху» + продольный разрез по плоскости пазов;
  * плита/рамка толщиной по Z — compose_plate: план rotate 90 + поперечный разрез + выносной из
    разреза; у массива отверстий в geom — centers [[x, y], …] (координаты от баз — в рамках).
"""
import json
import math
import os
import re
import sys

FRAME = (20.0, 5.0, 410.0, 292.0)        # рамка А3 гориз.
STAMP_TOP = 60.0                          # верх штампа (форма 1), мм
ROW0, ROW_STEP = 12.0, 8.0                # мм листа: первый ряд от контура, шаг рядов
TIER_UP = 20.0                            # мм: второй ярус полок (рамка 10 + подпись под полкой 7,5 + зазор)
TITLE_H = 14.0                            # мм над видом под заголовок «А-А (1:1)» (высота 10 + зазор)
TOP_GAP = 20.0                            # мм: от верхнего контура до полок/рамок сверху (было 16:
                                          # Ra на выноске и буква выносного у шеек Вал_v5 не помещались)
FRAME_H = 8.0                             # высота рамки допуска, мм листа
BASE_BAND = 26.0                          # мм под контуром для рамок баз (вместо ROW0, если базы снизу):
                                          # два яруса — цилиндры (8 мм под образующей), торцы (18 мм)
COL_GAP = 4.0                             # мм между колонками сверху
SHELF_W, SHELF_H = 10.3, 15.3             # мм: буква выносного на полке (_draw.SHELF_BOX_W/H)
RA_H = 9.0                                # мм: высота знака Ra с текстом
RA_LEAD_W = 11.0                          # мм: полка знака на выноске сверх текста (замер: «Ra 0,8» — 29,6)
RA_GAP = 7.0                              # мм — желаемый зазор знака Ra до ножек (цель, не запрет)
LETTERS = "АБВГДЕЖИКЛМНПРСТУФШЭЮЯ"
GDT_SIGN = {"радиальное биение": "runout", "торцовое биение": "runout", "биение": "runout",
            "полное биение": "runout_total", "цилиндричность": "cyl", "круглость": "round",
            "перпендикулярность": "perp", "параллельность": "par", "соосность": "coax",
            "симметричность": "pos", "позиционный": "pos", "плоскостность": "flat",
            "прямолинейность": "straight"}
STEP_KINDS = {"шейка", "ступень", "буртик", "резьба"}
DETAIL_SCALES = ["5:1", "4:1", "2,5:1", "2:1"]   # ряд ГОСТ 2.302 для выносных — от крупного
NARROW = 10.0                             # мм листа: ступень уже — Ø выносными линиями наружу
DETAIL_R_MIN = 3.5                        # мм листа: граница выносного на главном виде не мельче Ø7
                                          # (ревьюер Вал_v5: круг r 2,75 мм «почти не виден»)
BUNCH_W, BUNCH_N = 12.0, 2                # мм листа: не больше 2 анкеров выносок сверху на 12 мм
ANCHOR_MIN = 2.5                          # мм листа: анкеры выносок не ближе (ревьюер Вал_v6: пучок
                                          # Ø60/R1,5/R3/биение у буртика Ø60 в 10 мм)
TT_W = 175.0 - 6.0                        # мм: строка ТТ (блок 175 минус висячий отступ номера)


def num(v):
    return f"{float(v):g}".replace(".", ",")


def scale_of(s):
    a, b = (float(x.replace(",", ".")) for x in s.split(":"))
    return a / b


class Profile:
    """Наружный профиль вала: ступени (x0, x1, r) по возрастанию x."""

    def __init__(self, steps):
        self.steps = sorted(steps)

    def r_at(self, x, side=0):
        """Радиус в точке x; на границе ступеней side −1 — левая, +1 — правая, 0 — больший."""
        rs = [r for x0, x1, r in self.steps if x0 - 1e-6 <= x <= x1 + 1e-6]
        if not rs:
            return 0.0
        if side == 0 or len(rs) == 1:
            return max(rs)
        left = [r for x0, x1, r in self.steps if abs(x1 - x) < 1e-6]
        right = [r for x0, x1, r in self.steps if abs(x0 - x) < 1e-6]
        return (left or rs)[0] if side < 0 else (right or rs)[0]

    def r_max(self, a=None, b=None):
        a = self.steps[0][0] if a is None else a
        b = self.steps[-1][1] if b is None else b
        return max(r for x0, x1, r in self.steps if x1 > a and x0 < b)

    def bounds(self):
        xs = set()
        for x0, x1, _ in self.steps:
            xs.update((round(x0, 3), round(x1, 3)))
        return sorted(xs)

    def top_segments(self):
        """Верхний контур отрезками (x, z) модели: горизонтали ступеней + вертикали уступов."""
        segs, prev = [], None
        for x0, x1, r in self.steps:
            if prev is not None and abs(prev[0] - x0) < 1e-6 and abs(prev[1] - r) > 1e-6:
                segs.append((x0, prev[1], x0, r))
            segs.append((x0, r, x1, r))
            prev = (x1, r)
        x0, x1 = self.steps[0][0], self.steps[-1][1]
        segs.append((x0, 0.0, x0, self.steps[0][2]))
        segs.append((x1, 0.0, x1, self.steps[-1][2]))
        return segs


def seg_cross(p, q, eps=0.3):
    """Отрезки p, q пересекаются не у концов p (ножка начинается на контуре)."""
    x1, y1, x2, y2 = p
    x3, y3, x4, y4 = q
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return False
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    L = math.hypot(x2 - x1, y2 - y1) or 1.0
    return eps / L < t < 1 - eps / L and -1e-9 <= u <= 1 + 1e-9


def span_of(dim, feats, bounds):
    """Интервал [a, b] по оси для линейного размера: пара границ профиля/элементов с длиной
    = номиналу, касающаяся элемента размера. None — не нашли (ошибка компоновки)."""
    v = float(dim["value"])
    xs = set(bounds)
    for f in feats.values():
        g = f.get("geom") or {}
        for k in ("x", "x0", "x1", "from_x", "bottom_x"):
            if isinstance(g.get(k), (int, float)):
                xs.add(round(float(g[k]), 3))
    xs = sorted(xs)
    tgt = []
    for fid in dim.get("features") or []:
        g = feats[fid].get("geom") or {}
        if "x0" in g and "x1" in g:
            tgt.append((float(g["x0"]), float(g["x1"])))
        elif "x" in g:
            tgt.append((float(g["x"]), float(g["x"])))
        elif "from_x" in g:
            tgt.append((float(g["from_x"]), float(g.get("bottom_x", g["from_x"]))))
    best = None
    for i, a in enumerate(xs):
        for b in xs[i + 1:]:
            if abs(b - a - v) > 0.06:
                continue
            score = 0.0
            for t0, t1 in tgt:
                ov = min(b, t1) - max(a, t0)
                score += ov if ov > 0 else (1.0 if a - 1e-6 <= t0 <= b + 1e-6 else -abs(a - t0))
            if best is None or score > best[0]:
                best = (score, a, b)
    return (best[1], best[2]) if best else None


def pack_rows(items):
    """Упаковка интервалов по рядам: items [(id, a, b, last)] → {id: ряд}. Касание концов
    допускается (цепочка в одном ряду); last=True (габарит) — отдельный последний ряд."""
    rows, out = [], {}
    for did, a, b, last in sorted((i for i in items if not i[3]), key=lambda i: (i[2] - i[1], i[1])):
        for k, row in enumerate(rows):
            if all(b <= a2 + 1e-6 or a >= b2 - 1e-6 for a2, b2 in row):
                row.append((a, b))
                out[did] = k
                break
        else:
            rows.append([(a, b)])
            out[did] = len(rows) - 1
    n = len(rows)
    for did, a, b, last in items:
        if last:
            out[did] = n
    return out


def columns(anchors, widths, x_min, x_max, gap=COL_GAP):
    """Колонки сверху: анкеры (x бумаги) по возрастанию → левые края полок/рамок с сохранённым
    порядком, без наложения (сдвиг вправо, затем — если вышли за край — влево)."""
    order = sorted(range(len(anchors)), key=lambda i: anchors[i])
    xs = [0.0] * len(anchors)
    cur = x_min
    for i in order:
        x = max(anchors[i] + 2.0, cur)
        xs[i] = x
        cur = x + widths[i] + gap
    over = cur - gap - x_max
    if over > 0:
        cur = x_max
        for i in reversed(order):
            x = min(xs[i], cur - widths[i])
            xs[i] = x
            cur = x - gap
    return xs


def frame_width(value, base=None):
    """Ширина рамки «знак | значение | база» шрифтом 5, мм листа (замер вала v3: «↗|0,02|А-Б»
    44 мм, «◇|0,005» 34 мм)."""
    w = 10.0 + 3.6 * len(str(value)) + 6.0
    if base:
        w += 3.6 * len(str(base)) + 3.0
    return w


def text_w(txt):
    """Ширина надписи шрифтом 5, мм листа: 3,1 мм на знак (_draw.TEXT_CHAR_W, замер ГОСТ тип А)."""
    return 3.1 * len(txt or "")


def pt_seg_dist(px, py, q):
    x1, y1, x2, y2 = q
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy or 1e-12
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def number_tt(lines):
    """Пункты ТТ — с номерами «1. …» (ГОСТ 2.316); строки «* …» (справочные) — без номера."""
    out, n = [], 0
    for x in lines:
        x = x.strip()
        if x.startswith("*") or re.match(r"^\d+[.)]?\s", x):
            out.append(x)
        else:
            n += 1
            out.append(f"{n}. {x}")
    return out


GEN_TOL = re.compile(r"^(\s*(?:\d+[.)]\s*)?(?:неуказанные предельные отклонения размеров|общие допуски))"
                     r"\s*:?\s*(.*?)\.?\s*$", re.I)


def tt_gost(lines):
    """Пункт ТТ об общих допусках — со ссылкой на стандарт (ревьюер Вал_v5): без «ГОСТ» в
    строке «Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2.» →
    «… размеров — по ГОСТ 30893.1: H14, h14, ±IT14/2.». Значения таблицы не меняются."""
    out = []
    for x in lines:
        m = GEN_TOL.match(x)
        if m and "ГОСТ" not in x.upper():
            x = f"{m.group(1)} — по ГОСТ 30893.1" + (f": {m.group(2)}." if m.group(2) else ".")
        out.append(x)
    return out


def is_ref(d):
    """Справочный размер: флаг reference или схема «справочный» таблицы (Вал: ширина
    проточки 5 замыкала цепь 42,8 + 5 = 47,8 — ревьюер Вал_v6, ГОСТ 2.307 п. 1.5)."""
    return bool(d.get("reference")) or d.get("scheme") == "справочный"


def tt_lines(tt):
    """Физических строк ТТ с переносами (оценка шириной text_w): высота блока над штампом."""
    return sum(max(1, math.ceil(text_w(x) / TT_W)) for x in tt) or 1


def used_bases(t):
    """Буквы баз, на которые ссылаются допуски (ГОСТ Р 2.308: неиспользуемую базу не обозначают)."""
    out = set()
    for g in t.get("gdt") or []:
        out.update(c for c in str(g.get("base") or "") if c.isalpha())
    return out


def letters_free(used):
    return [c for c in LETTERS if c not in used]


def part_kind(intent):
    """Класс детали по полю part.class таблицы: «плита» (плоская, толщина по Z), «втулка»
    (тело вращения с осью Z модели), иначе — вал (тело вращения с осью X, исходный класс)."""
    c = str(intent.get("part", {}).get("class") or "").lower()
    if c.startswith("плоск") or "плит" in c or "рамк" in c:
        return "plate"
    if "вращения" in c and "ось модели z" in c:
        return "bush"
    return "shaft"


def compose(intent, cdw, model=None, intent_path=None):
    """Перебор края стрелки допуска на поверхностях с Ra (0,1 или 0,9 длины): берётся вариант
    без ошибок компоновки, при равенстве — с наименьшей суммарной длиной ножек сверху.
    Плиты и втулки с осью Z — свои компоновки (compose_plate, compose_bush)."""
    kind = part_kind(intent)
    if kind == "plate":
        return compose_plate(intent, cdw, model, intent_path)
    if kind == "bush":
        return compose_bush(intent, cdw, model, intent_path)
    import itertools
    ra_feats = {r["feature"] for r in intent.get("roughness") or []}
    keys = sorted({g["feature"] for g in intent.get("gdt") or [] if g["feature"] in ra_feats
                   and (next(f for f in intent["features"] if f["id"] == g["feature"]).get("geom") or {}).get("x0") is not None})
    rad_n = {}                           # радиус «N места»: какое из мест несёт выноску
    for d in intent["dims"]:
        if d["type"] == "rad":
            rad_n[float(d["value"])] = rad_n.get(float(d["value"]), 0) + len(d["features"])
    rads = sorted(rad_n)
    def repaired(gk, rp, dc, rd=()):
        """Раскладка + ремонт: ножка режет надпись соседней колонки — владельца надписи на
        второй ярус полок (до 6 шагов, пока ошибок становится меньше)."""
        spec, errs, length = _compose(intent, cdw, model, intent_path, gk, rp, (), dc, rad_down=rd)
        rc = tuple(sorted(spec.pop("_ra_fail", [])))   # Ra без места у поверхности — во 2-й ярус колонок
        if rc:
            spec, errs, length = _compose(intent, cdw, model, intent_path, gk, rp, (), dc, rc, rad_down=rd)
            spec.pop("_ra_fail", None)
        up = set()
        for _ in range(6):
            names = []                   # владельцы надписей и ножек из ошибок — кандидаты наверх
            for e in errs:
                for pat in (r"(?:режет надпись/рамку|налезают.*?|на букве секущей) (\S+)$",
                            r"^ножка (\S+) режет", r"^ножки (\S+) и (\S+) пересекаются"):
                    m = re.search(pat, e)
                    if m:
                        names += [n for n in m.groups() if n not in names and n not in up]
            trials = []
            for n in names:
                up2 = up | {n}
                spec2, errs2, len2 = _compose(intent, cdw, model, intent_path, gk, rp, tuple(sorted(up2)), dc, rc,
                                              rad_down=rd)
                spec2.pop("_ra_fail", None)
                trials.append((len(errs2), len2, spec2, errs2, up2))
            if not trials:
                break
            n2, len2, spec2, errs2, up2 = min(trials, key=lambda q: (q[0], q[1]))
            if n2 >= len(errs):
                break
            spec, errs, length, up = spec2, errs2, len2, up2
        return spec, errs, length, rc

    res = []
    for combo, pick, dc, rd in itertools.product(itertools.product((0.1, 0.9), repeat=len(keys)),
                                                 itertools.product(*[range(rad_n[r]) for r in rads]),
                                                 (False, True),      # буква выносного: у круга / в колонке
                                                 [()] + [(r,) for r in rads]):   # один радиус — снизу
        gk, rp = dict(zip(keys, combo)), dict(zip(rads, pick))
        spec, errs, length, rc = repaired(gk, rp, dc, rd)
        res.append(((len(errs), length), spec, errs, (gk, rp, dc, rc, rd)))
    res.sort(key=lambda q: q[0])
    best = res[0]
    if best[2]:                          # жадный ремонт не развёл — 1…3 надписи во 2-й ярус перебором
        for _, spec0, _, (gk, rp, dc, rc, rd) in res[:3]:
            for k in (1, 2, 3):
                for up in itertools.combinations(spec0.get("_names") or [], k):
                    spec, errs, length = _compose(intent, cdw, model, intent_path, gk, rp, up, dc, rc,
                                                  rad_down=rd)
                    if (len(errs), length) < best[0]:
                        best = ((len(errs), length), spec, errs, None)
                if not best[2]:
                    break
            if not best[2]:
                break
    for k in ("_names", "_ra_fail"):
        best[1].pop(k, None)
    return best[1], best[2]


def _compose(intent, cdw, model=None, intent_path=None, gdt_k=None, rad_pick=None, row_up=(),
             det_col=False, ra_col=(), rad_down=()):
    t = intent
    feats = {f["id"]: f for f in t["features"]}
    views_i = {v["id"]: v for v in t["views"]}
    main_i = next(v for v in t["views"] if v["type"] == "главный")
    s = scale_of(main_i.get("scale", "1:2"))
    steps = []
    for f in t["features"]:
        g = f.get("geom") or {}
        if f["kind"] in STEP_KINDS and all(k in g for k in ("d", "x0", "x1")):
            steps.append((float(g["x0"]), float(g["x1"]), float(g["d"]) / 2))
    prof = Profile(steps)
    X0, X1 = prof.steps[0][0], prof.steps[-1][1]
    R = prof.r_max()
    errors, notes = [], []
    # --- главный вид: по центру поля левее/выше, снизу — ряды, сверху — колонки
    ox = FRAME[0] + 25.0 - X0 * s          # слева, как у инженера: справа — место под выносной
    oy = 200.0
    spec = {"model": model or t["part"]["model"], "cdw": cdw, "format": 3, "vertical": False,
            "scale": main_i.get("scale", "1:2"),
            "views": [{"key": "front", "orient": "#Слева", "x": round(ox, 1), "y": oy}],
            "dims": [], "leaders": [], "bases": [], "gdt": [], "roughs": [],
            "tt": number_tt(tt_gost([x for x in (t.get("tt") or [])
                             if not x.lstrip().startswith("*") or any(is_ref(d) for d in t["dims"])])),
            **({"intent": intent_path} if intent_path else {}),
            "ra": f"Ra {t['ra_general']}" if t.get("ra_general") else None, "ra_form": "spec",
            "stamp": {"marking": t["part"].get("marking"), "name": t["part"].get("name"),
                      "material": t["part"].get("material"),
                      "company": t["part"].get("organization", "Acme Engineering"),
                      **{k: t["part"][k] for k in ("first_use", "litera") if t["part"].get(k)}}}

    def paper(x, z):
        return ox + x * s, oy + z * s

    # --- буквы видов: не повторять буквы баз
    used = set()
    for b in t.get("bases") or []:
        used.update(c for c in b["id"] if c.isalpha())
    free = letters_free(used)
    vkeys = {main_i["id"]: "front"}
    bore = next((f for f in t["features"] if f["kind"] == "расточка"), None)
    extra_views = []
    for v in t["views"]:
        if v is main_i:
            continue
        L = free.pop(0)
        if v["type"] in ("сечение", "разрез"):
            vkeys[v["id"]] = f"{L}-{L}"
        else:
            vkeys[v["id"]] = L
        extra_views.append(v)
    bore_key, cut_ends = None, []
    if bore is not None:                 # расточка: разрез вдоль оси + местный вид
        L = free.pop(0)
        bore_key = f"{L}-{L}"
        gb = bore["geom"]
        xb0 = float(gb.get("from_x", 0.0))
        x_dn = xb0 + float(gb["depth"])
        nxt = [b for b in prof.bounds() if b > x_dn + 5.0]      # секущая: за торцом … за уступом после
        bore_line = [xb0 - 3.0, (nxt[0] + 15.0) if nxt else x_dn + 15.0]   # дна (бурт виден, как у инженера)
        # секущая — на всю длину: штрихи, стрелки и буквы за торцами, вне контура (ГОСТ 2.305,
        # ревьюер Вал_v6: конец секущей Е стоял внутри Ø90); разрез обрезан прямоугольником
        # (crop.rect) по обрыву bore_line[1] — там волнистая линия
        cut_ends = [X0 - 3.0, X1 + 3.0]

    # --- Ø и резьба на ступенях (pos внутри контура)
    dia_x, outside, dim_shelves = [], [], []
    # буква секущей стоит СНАРУЖИ конца линии: у левого конца — 2…14 мм левее, у правого — правее
    letter_zones = []
    if cut_ends:
        a, b = paper(min(cut_ends), 0)[0], paper(max(cut_ends), 0)[0]
        letter_zones = [(a - 14.0, a - 2.0), (b + 2.0, b + 14.0)]

    def letter_hit(px_line):
        """Текст вертикального Ø (левее линии на 1…6 мм) задевает зону буквы секущей."""
        t0, t1 = px_line - 6.5, px_line - 0.5
        return any(t0 < z1 and z0 < t1 for z0, z1 in letter_zones)

    for d in t["dims"]:
        if d["view"] != main_i["id"] or d["type"] not in ("dia", "thread"):
            continue
        f = feats[d["features"][0]]
        g = f.get("geom") or {}
        if f["kind"] == "расточка":
            continue
        x0, x1, r = float(g["x0"]), float(g["x1"]), float(g["d"]) / 2
        xm = (x0 + x1) / 2
        pos, shelf_item = None, None
        if (x1 - x0) * s < NARROW:       # узкая ступень: Ø «выноской» — линия на ступени, текст на полке
            pos = xm
            dia_x.append(xm)
            shelf_item = True
        if pos is None:
            for xp in dia_x:             # ≥ 9 мм листа до соседней Ø-линии
                if abs(xm - xp) * s < 9.0:
                    xm = min(x1 - 1.0, xp + 9.0 / s)
            if letter_hit(paper(xm, 0)[0]):     # текст Ø — не на букве секущей
                cand = [x0 + k * (x1 - x0) / 10 for k in range(1, 10)]
                ok = [q for q in cand if not letter_hit(paper(q, 0)[0])]
                if ok:
                    xm = min(ok, key=lambda q: abs(q - (x0 + x1) / 2))
                else:
                    errors.append(f"{d['id']}: текст Ø на букве секущей при любом положении")
            dia_x.append(xm)
            pos = xm
        it = {"view": "front", "id": d["id"], "kind": "lin", "orient": "v",
              "ref": {"type": "points", "p1": [round(xm, 2), 0, -r], "p2": [round(xm, 2), 0, r]},
              "pos": round(pos, 2)}
        if d["type"] == "thread":
            m = re.match(r"[МM](\d+(?:[.,]\d+)?)(.*)", str(d["value"]))
            it["prefix"] = "М"
            it["suffix"] = (m.group(2) if m else "").replace("x", "×")
        else:
            it["prefix"] = "Ø"
            if d.get("tolerance"):
                it["suffix"] = str(d["tolerance"])
        if is_ref(d):
            it["suffix"] = it.get("suffix", "") + "*"
        label = it.get("prefix", "") + num(2 * r if it.get("prefix") == "Ø" else r * 2) + it.get("suffix", "")
        if shelf_item:
            dim_shelves.append((it, xm, r, label))
        else:                            # число — с осевой: центр текста выше оси, если помещается
            half = text_w(label) / 2 / s
            lo, hi = half + 1.5 / s, r - half - 1.0 / s
            if hi >= lo:
                it["text_at"] = round(lo, 2)
        spec["dims"].append(it)

    # --- длины: ряды снизу
    bounds = prof.bounds()
    rows_in, spans = [], {}
    for d in t["dims"]:
        if d["view"] != main_i["id"] or d["type"] != "lin":
            continue
        if any(feats[fid]["kind"] == "расточка" for fid in d["features"]):
            continue
        sp = span_of(d, feats, bounds)
        if sp is None:
            errors.append(f"{d['id']}: не нашёл границ длины {d['value']}")
            continue
        spans[d["id"]] = sp
        rows_in.append((d["id"], sp[0], sp[1], d.get("scheme") == "габарит"))
    rows = pack_rows(rows_in)
    row0 = BASE_BAND if any("-" not in b["id"] and b["id"] in used_bases(t)
                            for b in t.get("bases") or []) else ROW0
    for d in t["dims"]:
        if d["id"] not in spans:
            continue
        a, b = spans[d["id"]]
        y = -(R * s + row0 + ROW_STEP * rows[d["id"]]) / s
        it = {"view": "front", "id": d["id"], "kind": "lin", "orient": "h",
              "ref": {"type": "points", "p1": [a, 0, -prof.r_at(a)], "p2": [b, 0, -prof.r_at(b)]},
              "pos": round(y, 2)}
        if abs(float(d["value"]) - round(float(d["value"]))) > 1e-6:
            it["text"] = num(d["value"])
        suf = (str(d["tolerance"]) if d.get("tolerance") else "") + ("*" if is_ref(d) else "")
        if suf:
            it["suffix"] = suf
        spec["dims"].append(it)
    n_rows = (max(rows.values()) + 1) if rows else 0
    bottom_y = oy - R * s - row0 - ROW_STEP * max(n_rows - 1, 0) - 6.0      # низ надписей рядов

    # --- секущие поперечных сечений и центр выносного — до колонок: их буквы стоят сверху
    detail_x = []
    for v in extra_views:
        if v["type"] not in ("сечение", "разрез"):
            g = feats[(v.get("shows") or [None])[0]]["geom"]
            detail_x.append((float(g["x0"]) + float(g["x1"])) / 2)
    cut_x = {}
    mids = [paper((a + b) / 2, 0)[0] for a, b in spans.values()]
    for v in extra_views:
        if v["type"] in ("сечение", "разрез"):
            g = feats[(v.get("shows") or [None])[0]]["geom"]
            x0, x1 = float(g["x0"]), float(g["x1"])
            cands = [x0 + k * (x1 - x0) / 20 for k in range(2, 19)]
            obst = [paper(x, 0)[0] for x in detail_x] + [paper(xm, 0)[0] for _, xm, _, _ in dim_shelves]
            # буква секущей встаёт ЛЕВЕЕ штриха на 1…14 мм (стрелка взгляда по +X, превью v4) — там не
            # должно быть выносных линий длин
            ext = [paper(q, 0)[0] for a, b in spans.values() for q in (a, b)]
            free_c = [x for x in cands if not any(paper(x, 0)[0] - 15.0 < e < paper(x, 0)[0] + 1.0 for e in ext)]
            if not free_c:
                errors.append(f"секущая {v['id']}: буква попадает на выносную линию при любом положении")
            cut_x[v["id"]] = max(free_c or cands,
                                 key=lambda x: min([abs(paper(x, 0)[0] - m) for m in obst] or [99]))
    top_obst = [paper(x, 0)[0] for x in list(cut_x.values()) + detail_x]

    def rects_cross(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    def cut_letter_boxes():
        """Габариты букв и стрелок секущих на главном виде, мм листа (оценка по превью v4/v5:
        у поперечной — левее штриха на 1…16 мм, до 22 мм за концом; у секущей расточки — над
        осью у концов линии)."""
        occ = []
        for xq in cut_x.values():
            qx = paper(xq, 0)[0]
            rq = (prof.r_at(xq) + 6.0) * s
            occ += [(qx - 16.0, oy + rq - 1.0, qx + 2.0, oy + rq + 22.0),
                    (qx - 16.0, oy - rq - 22.0, qx + 2.0, oy - rq + 1.0)]
        occ += [(z0, oy - 1.0, z1, oy + 17.0) for z0, z1 in letter_zones]
        return occ

    # --- выносные: круг на главном виде; буква — на полке в колонке сверху (ревьюер Вал_v5:
    # «Д» у круга вставала на стрелку и букву секущей «В» — читалось «ВД»)
    det, det_shelf = {}, {}
    for v in extra_views:
        if v["type"] in ("сечение", "разрез"):
            continue
        g = feats[(v.get("shows") or [None])[0]]["geom"]
        x0, x1 = float(g["x0"]), float(g["x1"])
        rb = float(g["d_bottom"]) / 2
        ro = max(prof.r_at(x0 - 1e-3), prof.r_at(x1 + 1e-3))
        det[v["id"]] = ((x0 + x1) / 2, (rb + ro) / 2,
                        max(max(x1 - x0, ro - rb) * 0.8 + 1.5, DETAIL_R_MIN / s))

    # --- сверху: радиусы (выноски) и рамки допусков колонками
    tops = []          # (анкер x модели, z модели, ширина колонки мм, что)
    rad_groups, low_boxes = {}, []
    row_top = oy - R * s - (BASE_BAND if any("-" not in b["id"] and b["id"] in used_bases(t)
                                              for b in t.get("bases") or []) else ROW0) + 6.0

    def low_rad(pts, w, under):
        """Выноска радиуса снизу: анкер — зеркальная точка галтели на нижней образующей, полка
        вправо на 12 мм ниже контура, над первым рядом длин и между их выносными линиями (мм
        листа). Ответ ((x, z) модели, (sx, sy) полки, габарит надписи) или None."""
        ext = sorted({paper(q, 0)[0] for a, b in spans.values() for q in (a, b)})
        for x, z in pts:
            ax, ay = paper(x, -z)
            sx, sy = ax + 3.0, oy - prof.r_max(x - 2.0 / s, x + (w + 5.0) / s) * s - 12.0
            box = (sx, sy - (7.5 if under else 0.0), sx + w, sy + 6.5)
            if box[1] < row_top:
                continue
            if any(box[0] - 1.0 < e < box[2] + 1.0 or ax < e < sx + 0.5 for e in ext):   # ножка — вправо
                continue
            return (x, z), (sx, sy), box
        return None
    for d in t["dims"]:
        if d["view"] == main_i["id"] and d["type"] == "rad":
            rad_groups.setdefault(float(d["value"]), []).extend(d["features"])
    for rr, fids in sorted(rad_groups.items()):
        pts = []
        for fid in fids:
            g = feats[fid]["geom"]
            x = float(g["x"])
            rl, rr_ = prof.r_at(x - 1e-3), prof.r_at(x + 1e-3)
            small = min(rl, rr_)
            dx = rr * 0.3 if rr_ < rl else -rr * 0.3       # во входящий угол со стороны малой ступени
            pts.append((x + dx, small + rr * 0.3))
        txt = "R" + num(rr)
        under = f"{len(pts)} места" if len(pts) > 1 else None
        w = 4.0 + max(text_w(txt), text_w(under))
        if rr in rad_down:               # снизу, между выносными линиями длин (ревьюер Вал_v6: R3 —
            low = low_rad(pts, w, under)  # «с другой стороны», не в пучок сверху у буртика Ø60)
            if low is not None:
                (x, z), (sx, sy), box = low
                spec["leaders"].append({"view": "front", "at": [round(x, 2), 0, round(-z, 2)],
                                        "shelf": [round((sx - ox) / s, 2), round((sy - oy) / s, 2)],
                                        "text": txt, **({"under": under} if under else {})})
                low_boxes.append(box)
                continue
            errors.append(f"{txt} снизу: нет места между выносными линиями")
        tops.append({"pts": pts, "w": w, "kind": "rad", "text": txt, "under": under, "r": rr})
    ra_feats = {r["feature"] for r in t.get("roughness") or []}
    for g in t.get("gdt") or []:
        f = feats[g["feature"]]
        geo = f.get("geom") or {}
        if f["kind"] == "торец":
            x = float(geo["x"])
            r_in = min(prof.r_at(x - 1e-3), prof.r_at(x + 1e-3))
            r_out = max(prof.r_at(x - 1e-3), prof.r_at(x + 1e-3))
            z = (r_in + r_out) / 2
            x_at = x
        else:
            x0, x1 = float(geo["x0"]), float(geo["x1"])
            # на поверхности с Ra стрелка — у левого края: правее встанет знак шероховатости
            k = (gdt_k or {}).get(g["feature"], 0.1) if g["feature"] in ra_feats else 0.7
            x_at, z = x0 + k * (x1 - x0), float(geo["d"]) / 2
        sign = GDT_SIGN.get(str(g["kind"]).lower())
        if sign is None:
            errors.append(f"{g['id']}: вид допуска «{g['kind']}» не знаю")
            continue
        tops.append({"x": x_at, "z": z, "w": frame_width(g["value"], g.get("base")), "kind": "gdt",
                     "g": g, "sign": sign})
    for it, xm, r, label in dim_shelves:  # Ø узких ступеней: полка в той же колонке сверху
        tops.append({"x": xm, "z": r, "w": 4.0 + text_w(label), "kind": "dimshelf", "it": it})
    for r in t.get("roughness") or []:   # Ra без места у поверхности — на выноске во 2-м ярусе колонок
        if r["feature"] not in ra_col:
            continue
        geo = feats[r["feature"]]["geom"]
        x0, x1 = float(geo["x0"]), float(geo["x1"])
        # анкер — подальше от всех анкеров сверху (радиусы — по их местам): иначе ножка Ra
        # уходит в пучок (Вал_v6: Ø60/R1,5/R3/биение у буртика Ø60)
        near = [c["x"] for c in tops if "x" in c] + [p[0] for c in tops if "pts" in c for p in c["pts"]]
        xa = max([x0 + k * (x1 - x0) / 10 for k in range(2, 9)],
                 key=lambda x: min([abs(x - o) for o in near] or [99.0]))
        tops.append({"x": xa, "z": float(geo["d"]) / 2, "w": RA_LEAD_W + text_w("Ra " + str(r["ra"])),
                     "kind": "rough", "ra": str(r["ra"]), "text": "Ra_" + r["feature"], "tier2": True,
                     "cands": [x0 + k * (x1 - x0) / 10 for k in range(2, 9)]})
    for vid, (xc, zc, rz) in (det.items() if det_col else ()):   # буква выносного в колонке
        px_c = paper(xc, 0)[0]
        tops.append({"x": (px_c - rz * s - SHELF_W - 4.0 - ox) / s, "z": zc + rz, "w": SHELF_W,
                     "kind": "detail", "vid": vid, "c": (xc, zc, rz), "text": vkeys[vid]})
    fixed = [paper(c["x"], 0)[0] for c in tops if "pts" not in c] + top_obst
    for c in tops:                       # радиус «N места» — у места, где меньше соседей сверху
        if "pts" in c:
            def crowd(p):
                px = paper(p[0], 0)[0]
                return sum(1 for a in fixed if abs(a - px) < 45.0), px
            if rad_pick and c["r"] in rad_pick:
                c["x"], c["z"] = c["pts"][rad_pick[c["r"]] % len(c["pts"])]
            else:
                c["x"], c["z"] = min(c["pts"], key=crowd)
            fixed.append(paper(c["x"], 0)[0])
    y_top = oy + R * s + TOP_GAP

    def cname(c):
        return c.get("text") or c.get("g", {}).get("id") or c.get("it", {}).get("id")
    for c in tops:
        c["up"] = cname(c) in row_up or c.get("tier2", False)   # второй ярус полок (выше на 20 мм)
    xs = [0.0] * len(tops)
    for tier in (False, True):           # колонки — по ярусам независимо
        idx_t = [i for i, c in enumerate(tops) if c["up"] == tier]
        if tier:                         # Ra на выноске во 2-м ярусе: ножка — между надписями
            low = [(xs[i] - 1.0, y_top - 8.5, xs[i] + c["w"] + 1.0, y_top + 11.0)   # 1-го яруса
                   for i, c in enumerate(tops) if not c["up"]]          # (Вал_v6: резала полку R3)
            for i in idx_t:
                c = tops[i]
                if not c.get("cands"):
                    continue
                def cost(x):
                    c["x"] = x
                    xt = columns([paper(tops[j]["x"], 0)[0] for j in idx_t], [tops[j]["w"] for j in idx_t],
                                 FRAME[0] + 5, FRAME[2] - 5)
                    ax, ay = paper(x, c["z"])
                    ln = (ax, ay, xt[idx_t.index(i)], y_top + TIER_UP)
                    hit = sum(1 for bx in low for e in ((bx[0], bx[1], bx[0], bx[3]), (bx[2], bx[1], bx[2], bx[3]),
                                                         (bx[0], bx[1], bx[2], bx[1]))
                              if seg_cross(ln, e, eps=0.0))
                    return hit, -min([abs(ax - paper(q["x"], 0)[0]) for q in tops if q is not c] or [99.0])
                c["x"] = min(c["cands"], key=cost)
        xt = columns([paper(tops[i]["x"], 0)[0] for i in idx_t], [tops[i]["w"] for i in idx_t],
                     FRAME[0] + 5, FRAME[2] - 5)
        for i, x in zip(idx_t, xt):
            xs[i] = x
    top_lines, top_boxes = [], []      # ножки и габариты надписей/рамок сверху (мм листа)
    y_base = y_top
    for c, xl in zip(tops, xs):
        y_top = y_base + (TIER_UP if c["up"] else 0.0)
        ax, az = paper(c["x"], c["z"])
        if c["kind"] == "dimshelf":
            sx, sy = xl, y_top
            c["it"]["shelf"] = [round((sx - ox) / s, 2), round((sy - oy) / s, 2)]
            top_lines.append(((ax, az, sx, sy), c["it"]["id"]))
            top_boxes.append(((sx, sy, sx + c["w"], sy + 6.5), c["it"]["id"]))
        elif c["kind"] == "rough":       # стрелка выноски на поверхности, знак на полке вправо
            sx, sy = xl, y_top
            spec["roughs"].append({"view": "front", "at": [round(c["x"], 2), 0, round(c["z"], 2)],
                                   "ra": c["ra"], "shelf": [round((sx - ox) / s, 2), round((sy - oy) / s, 2)]})
            top_lines.append(((ax, az, sx, sy), c["text"]))
            top_boxes.append(((sx, sy, sx + c["w"], sy + RA_H), c["text"]))
        elif c["kind"] == "detail":      # ветвь КОМПАС — от окружности к точке полки
            sx, sy = xl, y_top
            det_shelf[c["vid"]] = [round((sx - ox) / s, 2), round((sy - oy) / s, 2)]
            (xc, zc, rz) = c["c"]
            pcx, pcz = paper(xc, zc)
            L = math.hypot(sx - pcx, sy - pcz) or 1.0
            top_lines.append(((pcx + rz * s * (sx - pcx) / L, pcz + rz * s * (sy - pcz) / L, sx, sy), c["text"]))
            top_boxes.append(((sx, sy, sx + SHELF_W, sy + SHELF_H), c["text"]))
        elif c["kind"] == "rad":
            sx, sy = xl, y_top
            spec["leaders"].append({"view": "front", "at": [round(c["x"], 2), 0, round(c["z"], 2)],
                                    "shelf": [round((sx - ox) / s, 2), round((sy - oy) / s, 2)],
                                    "text": c["text"], **({"under": c["under"]} if c["under"] else {})})
            top_lines.append(((ax, az, sx, sy), c["text"]))
            top_boxes.append(((sx, sy - (7.5 if c["under"] else 0.0), sx + c["w"], sy + 6.5), c["text"]))
        else:
            g = c["g"]
            fx, fy = xl, y_top + 2.0          # левый низ рамки
            spec["gdt"].append({"view": "front", "at": [round(c["x"], 2), 0, round(c["z"], 2)],
                                "frame": [round((fx - ox) / s, 2), round((fy - oy) / s, 2)],
                                "sign": c["sign"], "value": str(g["value"]),
                                **({"base": g["base"]} if g.get("base") else {}), "base_pos": 1})
            top_lines.append(((ax, az, fx, fy), g["id"]))
            top_boxes.append(((fx, fy, fx + c["w"], fy + FRAME_H), g["id"]))
    # буквы секущих/выносного: анкер выноски не ближе 5 мм (иначе ножка ложится на букву)
    for c in tops:
        xa = paper(c["x"], 0)[0]
        if c["kind"] != "detail" and any(abs(xa - o) < 5.0 for o in top_obst):
            errors.append(f"анкер {cname(c)} "
                          "ближе 5 мм к букве секущей/выносного")
    # пучок: выноски сверху не сходятся к контуру по 4 в одном месте (ревьюер Вал_v6, Р32)
    anch = sorted((paper(c["x"], c["z"]), cname(c)) for c in tops if c["kind"] != "detail")
    for i, ((ax, ay), n) in enumerate(anch):
        grp = [m for (bx, _), m in anch[i:] if bx - ax <= BUNCH_W]
        if len(grp) > BUNCH_N:
            errors.append(f"пучок выносок {', '.join(grp)} в {BUNCH_W:g} мм")
        for (bx, by), m in anch[i + 1:]:
            if math.hypot(bx - ax, by - ay) < ANCHOR_MIN:
                errors.append(f"анкеры {n} и {m} ближе {ANCHOR_MIN:g} мм")
    # жёсткое ограничение: ножки сверху не режут профиль и друг друга
    prof_p = [(*paper(a, b), *paper(c, d)) for a, b, c, d in prof.top_segments()]
    for ln, name in top_lines:           # ножка не режет надпись/рамку соседней колонки
        for bx, nb in top_boxes:
            if nb == name:
                continue
            edges = [(bx[0], bx[1], bx[2], bx[1]), (bx[2], bx[1], bx[2], bx[3]),
                     (bx[2], bx[3], bx[0], bx[3]), (bx[0], bx[3], bx[0], bx[1])]
            if any(seg_cross(ln, e, eps=0.0) for e in edges):
                errors.append(f"ножка {name} режет надпись/рамку {nb}")
    for i, (bx, nb) in enumerate(top_boxes):     # надписи/рамки сверху не налезают друг на друга
        for by, nc in top_boxes[i + 1:]:
            if bx[0] < by[2] and by[0] < bx[2] and bx[1] < by[3] and by[1] < bx[3]:
                errors.append(f"надписи {nb} и {nc} налезают")
    for bx, nb in top_boxes:             # буквы секущих и выносного — не под рамками и полками
        if any(bx[0] - 3.0 < o < bx[2] + 3.0 for o in top_obst) and bx[1] < oy + R * s + 14.0:
            errors.append(f"надпись {nb} над буквой секущей/выносного")
    for bx, nb in top_boxes:             # надписи/рамки/буквы сверху — не ближе 3 мм к букве секущей
        if any(rects_cross(bx, (o[0] - 3.0, o[1] - 3.0, o[2] + 3.0, o[3] + 3.0)) for o in cut_letter_boxes()):
            errors.append(f"надпись на букве секущей {nb}")
    for ln, name in top_lines:           # ножка не идёт через букву и стрелку секущей
        for bx in cut_letter_boxes():
            edges = [(bx[0], bx[1], bx[2], bx[1]), (bx[2], bx[1], bx[2], bx[3]),
                     (bx[2], bx[3], bx[0], bx[3]), (bx[0], bx[3], bx[0], bx[1])]
            if any(seg_cross(ln, e, eps=0.0) for e in edges):
                errors.append(f"ножка {name} режет букву секущей")
                break
    for i, (ln, name) in enumerate(top_lines):
        if any(seg_cross(ln, q) for q in prof_p):
            errors.append(f"ножка {name} пересекает контур")
        for ln2, name2 in top_lines[i + 1:]:
            if seg_cross(ln, ln2, eps=0.0):
                errors.append(f"ножки {name} и {name2} пересекаются")

    # --- базы снизу: треугольник на нижней образующей, рамка ниже; место — подальше от
    # выносных линий длин, секущих разрезов и соседних рамок (препятствия по X листа)
    cut_xs = []
    for v in extra_views:
        if v["type"] in ("сечение", "разрез"):
            g = feats[(v.get("shows") or [None])[0]]["geom"]
            cut_xs.append(None)          # уточняется ниже, когда выбрана секущая
    bottom_obst = ([paper(x, 0)[0] for a, b in spans.values() for x in (a, b)] + top_obst
                   + [q for b in low_boxes for q in (b[0], b[2])])
    base_specs = []
    ref_bases = used_bases(t)
    for b in t.get("bases") or []:
        if "-" in b["id"]:
            continue                     # «А-Б» — общая база из А и Б, знака нет
        if b["id"] not in ref_bases:
            continue                     # база размеров (Г — упорный торец), не база допусков
        f = feats[b["feature"]]
        g = f.get("geom") or {}
        if f["kind"] == "торец":
            x = float(g["x"])
            r_in = min(prof.r_at(x - 1e-3), prof.r_at(x + 1e-3))
            r_out = max(prof.r_at(x - 1e-3), prof.r_at(x + 1e-3))
            base_specs.append((b["id"], (x, -(r_in + r_out) / 2), [x + k / s for k in (10, 14, 18, 22)],
                               -r_in - 18.0 / s))
        else:
            x0, x1, r = float(g["x0"]), float(g["x1"]), float(g["d"]) / 2
            base_specs.append((b["id"], None, [x0 + k * (x1 - x0) / 10 for k in range(2, 9)],
                               -r - 8.0 / s, r))
    placed_frames, tiers = [], []
    for item in base_specs:
        letter, at, cands, fy = item[0], item[1], item[2], item[3]
        same = [x for x, y in tiers if abs(y - fy) * s < 9.0]      # рамки того же яруса

        def score(x):
            px = paper(x, 0)[0]
            near = min([abs(px - o) for o in same] or [99.0])
            d_obst = min([abs(px - o) for o in bottom_obst + placed_frames] or [99.0])
            return (near >= 14.0, d_obst)
        fx = max(cands, key=score)
        if not score(fx)[0]:
            errors.append(f"база {letter}: рамка ближе 14 мм к соседней в том же ярусе")
        if at is None:                   # треугольник на образующей под рамкой
            at = (fx, -item[4])
        placed_frames.append(paper(fx, 0)[0])
        tiers.append((paper(fx, 0)[0], fy))
        spec["bases"].append({"view": "front", "at": [round(at[0], 2), 0, round(at[1], 2)],
                              "frame": [round(fx, 2), round(fy, 2)], "letter": letter})
    base_px = list(placed_frames)

    # --- нижние виды: зоны листа под рядами — слева от штампа и над ТТ (справа)
    tt_top = STAMP_TOP + 5.0 + 7.0 * tt_lines(spec["tt"]) + 6.0
    top_band = bottom_y - 8.0
    right_dims = [d["pos"] for d in spec["dims"] if d["view"] == "front" and d["orient"] == "v"
                  and d.get("pos") is not None and d["pos"] > X1]
    right_x = ox + max([X1] + right_dims) * s + (14.0 if right_dims else 8.0)   # правее главного вида и его Ø
    zones = [[FRAME[0] + 4.0, 225.0 - 4.0, FRAME[1] + 4.0],
             [225.0 + 4.0, FRAME[2] - 4.0, tt_top],
             [right_x, FRAME[2] - 4.0, tt_top]]
    # справа от главного вида — под его правым торцом: правее X1 геометрии вида нет
    tops_k = [top_band, top_band, oy - (R if right_dims else prof.r_at(X1)) * s - 4.0]

    def assign(sizes):
        """Нижние виды по зонам: первый порядок (перестановки от порядка spec), в котором все
        помещаются, — {ключ: левый-нижний угол} или None. Порядок spec отдавал место первым
        видам, и выносной в масштабе таблицы не входил (Вал_v6: Д 4:1 → 2,5:1)."""
        import itertools
        for perm in itertools.permutations(range(len(sizes))):
            cursor, out = [z[0] for z in zones], {}
            for i in perm:
                key, w, h = sizes[i]
                for k, (x_lo, x_hi, y_lo) in enumerate(zones):
                    if cursor[k] + w <= x_hi and tops_k[k] - TITLE_H - h >= y_lo:
                        out[key] = (cursor[k], tops_k[k] - TITLE_H - h)
                        cursor[k] += w + 14.0
                        break
                else:
                    break
            if len(out) == len(sizes):
                return out
        return None

    def detail_geom(v):
        g = feats[(v.get("shows") or [None])[0]]["geom"]
        x0, x1 = float(g["x0"]), float(g["x1"])
        rb = float(g["d_bottom"]) / 2
        ro = max(prof.r_at(x0 - 1e-3), prof.r_at(x1 + 1e-3))
        rz = max(max(x1 - x0, ro - rb) * 0.8 + 1.5, DETAIL_R_MIN / s)
        return x0, x1, rb, ro, (x0 + x1) / 2, (rb + ro) / 2, rz

    if bore is not None:
        xe, xb0 = bore_line[1], float(bore["geom"].get("from_x", 0.0))
        rv = prof.r_max(xb0, xe)
    det_scale, pos = {}, {}
    for alt in DETAIL_SCALES:            # нет места в масштабе таблицы — следующий меньший ГОСТ 2.302
        sizes = []
        if bore is not None:
            sizes.append((bore_key, (xe - xb0) * s + 16.0, 2 * rv * s + 16.0))   # + Ø слева, длина сверху
        for v in extra_views:
            vs = scale_of(v.get("scale", spec["scale"]))
            if v["type"] in ("сечение", "разрез"):
                r = prof.r_at(cut_x[v["id"]])
                sizes.append((vkeys[v["id"]], 2 * r * vs + 16.0, 2 * r * vs + 16.0))
            else:
                sc_txt = v.get("scale", "4:1")
                if scale_of(alt) < vs:
                    sc_txt, vs = alt, scale_of(alt)
                det_scale[v["id"]] = sc_txt
                rz = detail_geom(v)[6]
                sizes.append((vkeys[v["id"]], 2 * rz * vs + 16.0, 2 * rz * vs + 12.0))
        pos = assign(sizes) or {}
        if pos:
            break
    for v in extra_views:
        if v["id"] in det_scale and det_scale[v["id"]] != v.get("scale", "4:1"):
            notes.append(f"выносной {vkeys[v['id']]}: масштаб {v.get('scale', '4:1')} → "
                         f"{det_scale[v['id']]} — не хватило места на А3")

    def place(key, w, h):
        if key in pos:
            return pos[key]
        errors.append(f"вид {key} {w:.0f}×{h:.0f} мм не помещается под рядами")
        return zones[0][0], top_band - TITLE_H - h

    if bore is not None:
        g = bore["geom"]
        d0, dep = float(g["d"]), float(g["depth"])
        # разрез по секущей на всю длину, обрезан прямоугольником по обрыву xe; волнистая линия
        # обрыва (ГОСТ 2.305) — по краю. Окружность (crop.r) прямого края не давала: металл за
        # дном расточки и бурт оставались за кадром (ревьюер Вал_v5)
        hw = prof.r_at(xe) + 2.0
        w, h = (xe - xb0) * s + 16.0, 2 * rv * s + 16.0
        px, py = place(bore_key, w, h)
        spec["views"].append({"key": bore_key, "type": "section", "from": "front",
                              "line": [[cut_ends[0], 0], [cut_ends[1], 0]],
                              "crop": {"rect": [[round(X0 - 5.0, 1), round(-rv - 5.0, 1), 0],
                                                [xe, round(rv + 5.0, 1), 0]]},
                              "x": round(px + 14.0 - xb0 * s, 1), "y": round(py + rv * s, 1),
                              "wave": {"at": [xe, 0, 0], "half": round(hw, 1)}})
        for d in t["dims"]:
            if any(feats[fid]["kind"] == "расточка" for fid in d["features"]):
                suf = {"suffix": "*"} if is_ref(d) else {}
                if d["type"] == "dia":
                    spec["dims"].append({"view": bore_key, "id": d["id"], "kind": "lin", "orient": "v",
                                         "side": "left", "prefix": "Ø", **suf,  # от торца наружу, как у инженера
                                         "ref": {"type": "points", "p1": [xb0, -d0 / 2, 0],
                                                 "p2": [xb0, d0 / 2, 0]}})
                else:
                    spec["dims"].append({"view": bore_key, "id": d["id"], "kind": "lin", "orient": "h",
                                         "side": "top", **suf,
                                         "ref": {"type": "points", "p1": [xb0, d0 / 2, 0],
                                                 "p2": [xb0 + float(d["value"]), d0 / 2, 0]}})
    shelf_boxes = []                     # буквы выносных у круга (мм листа) — препятствия для Ra

    def detail_shelf(xc, zc, rz):
        """Точка полки буквы выносного у круга (СК вида = x, z модели): буква 10,3×15,3 мм
        вправо-вверх от неё. Кандидаты — справа/слева сверху на 3…15 мм за кругом; запрет —
        буквы и стрелки секущих, контур, полки/рамки и ножки колонок сверху."""
        pcx, pcy = paper(xc, zc)
        rp = rz * s
        occ = [b for b, _ in top_boxes] + cut_letter_boxes()
        occ += [(ox + a * s, oy - rr * s, ox + b * s, oy + rr * s) for a, b, rr in prof.steps]
        for d in (3.0, 5.0, 7.0, 10.0, 15.0):
            for sx, sy in ((pcx + rp + d, pcy + rp + d), (pcx - rp - d - SHELF_W, pcy + rp + d)):
                bx = (sx - 1.0, sy - 1.0, sx + SHELF_W + 1.0, sy + SHELF_H + 1.0)
                edges = [(bx[0], bx[1], bx[2], bx[1]), (bx[2], bx[1], bx[2], bx[3]),
                         (bx[2], bx[3], bx[0], bx[3]), (bx[0], bx[3], bx[0], bx[1])]
                if any(rects_cross(bx, o) for o in occ):
                    continue
                if any(seg_cross(ln, e, eps=0.0) for ln, _ in top_lines for e in edges):
                    continue
                shelf_boxes.append(bx)
                return [round((sx - ox) / s, 2), round((sy - oy) / s, 2)]
        errors.append("полка буквы выносного: нет места у круга без касания секущих, контура и колонок")
        return None

    for v in extra_views:
        key = vkeys[v["id"]]
        shown = [feats[fid] for fid in v.get("shows") or []]
        vs = scale_of(v.get("scale", spec["scale"]))
        if v["type"] in ("сечение", "разрез"):
            f = shown[0]
            g = f["geom"]
            x0, x1 = float(g["x0"]), float(g["x1"])
            # секущая — подальше от текстов длин (середины пролётов) внутри элемента
            xc = cut_x[v["id"]]
            r = prof.r_at(xc)
            px, py = place(key, 2 * r * vs + 16.0, 2 * r * vs + 16.0)
            spec["views"].append({"key": key, "type": "section", "from": "front",
                                  "line": [[round(xc, 1), round(-r - 6, 1)], [round(xc, 1), round(r + 6, 1)]],
                                  "scale": v.get("scale", spec["scale"]),
                                  "x": round(px + r * vs, 1), "y": round(py + r * vs, 1)})
            for d in t["dims"]:
                if d["view"] != v["id"]:
                    continue
                suf = {"suffix": "*"} if is_ref(d) else {}
                if f["kind"] == "паз" and "b" in g and abs(float(d["value"]) - float(g["b"])) < 1e-6:
                    spec["dims"].append({"view": key, "id": d["id"], "kind": "lin", "orient": "h", **suf,
                                         "side": "top", "ref": {"type": "points",
                                                                 "p1": [xc, -float(g["b"]) / 2, r],
                                                                 "p2": [xc, float(g["b"]) / 2, r]}})
                else:
                    zb = float(g.get("bottom_z", r - float(g.get("depth_from_d", 0))))
                    spec["dims"].append({"view": key, "id": d["id"], "kind": "lin", "orient": "v", **suf,
                                         "side": "right", "ref": {"type": "points",
                                                                   "p1": [xc, 0, zb], "p2": [xc, 0, -r]}})
        else:                            # выносной: канавка
            x0, x1, rb, ro, xc, zc, rz = detail_geom(v)
            sc_txt = det_scale[v["id"]]
            vs = scale_of(sc_txt)
            px, py = place(key, 2 * rz * vs + 16.0, 2 * rz * vs + 12.0)
            shelf = det_shelf.get(v["id"]) or detail_shelf(xc, zc, rz)
            spec["views"].append({"key": key, "type": "detail", "from": "front",
                                  "center": [round(xc, 2), round(zc, 2)], "r": round(rz, 2),
                                  **({"shelf": shelf} if shelf else {}),
                                  "scale": sc_txt,
                                  "x": round(px + rz * vs, 1), "y": round(py + 12.0 + rz * vs, 1)})
            r_thread = prof.r_at(x1 + 1e-3)
            for d in t["dims"]:
                if d["view"] != v["id"]:
                    continue
                suf = {"suffix": "*"} if is_ref(d) else {}
                if d["type"] == "dia":    # Ø дна во фрагменте не виден целиком → глубина от резьбы
                    spec["dims"].append({"view": key, "id": d["id"], "kind": "lin", "orient": "v", **suf,
                                         "side": "right", "ref": {"type": "points",
                                                                   "p1": [x1 + 1.0, 0, rb], "p2": [x1 + 1.0, 0, r_thread]}})
                else:
                    spec["dims"].append({"view": key, "id": d["id"], "kind": "lin", "orient": "h", **suf,
                                         "side": "bottom", "ref": {"type": "points",
                                                                    "p1": [x0, 0, rb], "p2": [x1, 0, rb]}})
    # --- Ra сверху на поверхности, в стороне от анкеров выносок
    lines = [ln for ln, _ in top_lines]
    ra_boxes, ra_fail = [], []           # поставленные знаки Ra (мм листа); Ra без места

    def lead_rough(x0, x1, z, w):
        """Знак Ra на линии-выноске с полкой (ГОСТ 2.309 п. 2.4) между контуром и колонками
        сверху: стрелка на поверхности шейки, знак на полке справа или слева (ShelfDirection ±1) — там, где ножки,
        буквы и чужие знаки не мешают (ревьюер Вал_v5: рамки допусков упирались в полки Ra
        на шейках короче знака). Ответ (x стрелки, точка полки (мм листа), габарит знака) или None."""
        y_hi = oy + R * s + TOP_GAP - 1.0          # верх знака — ниже колонок сверху
        occ = shelf_boxes + ra_boxes + cut_letter_boxes()
        best = None
        for xa in [x0 + k * (x1 - x0) / 10 for k in range(2, 9)]:
            ax, ay = paper(xa, z)
            for dx in [k * 2.0 for k in range(-35, 36) if abs(k) >= 2]:   # полка вправо/влево от точки S
                sx = ax + dx
                bx0, bx1 = (sx, sx + w) if dx > 0 else (sx - w, sx)
                top = max(prof.r_at(x, 0) for x in
                          [((bx0 - ox) / s) + q * (bx1 - bx0) / s / 8 for q in range(9)])
                sy = max(ay, oy + top * s) + 3.0
                box = (bx0, sy, bx1, sy + RA_H)
                if box[3] > y_hi:
                    continue
                ln = (ax, ay, sx, sy)
                edges = [(box[0], box[1], box[2], box[1]), (box[2], box[1], box[2], box[3]),
                         (box[2], box[3], box[0], box[3]), (box[0], box[3], box[0], box[1])]
                if any(rects_cross((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1), o) for o in occ):
                    continue
                if any(box[0] - 2 <= lx <= box[2] + 2 and box[1] - 2 <= ly <= box[3] + 2 for lx, ly in letters):
                    continue
                if any(seg_cross(q, e, eps=0.0) for q in lines for e in edges + [ln]):
                    continue
                if any(seg_cross(ln, q) for q in prof_p):
                    continue
                L = math.hypot(sx - ax, sy - ay)
                score = L + (20.0 if abs(sy - ay) < 0.5 * abs(sx - ax) else 0.0)   # пологая ножка — хуже
                if best is None or score < best[0]:
                    best = (score, (xa, (sx, sy), box))
        return best[1] if best else None
    letters = [(x, oy + R * s + 4.0) for x in top_obst]   # буквы секущих и выносного — препятствия для Ra
    for v in spec["views"][1:]:
        if v.get("type") == "section" and abs(v["line"][0][0] - v["line"][1][0]) < 1e-6:
            letters.append(paper(v["line"][0][0], max(v["line"][0][1], v["line"][1][1])))
        elif v.get("type") == "detail":
            cx, cz = v["center"]
            letters.append(paper(cx + v["r"], cz + v["r"]))
            letters.append(paper(cx, cz))
    for r in t.get("roughness") or []:
        f = feats[r["feature"]]
        g = f.get("geom") or {}
        if f["kind"] == "торец" or "x0" not in g or r["feature"] in ra_col:
            continue                     # торцы — Ra по общему знаку / в ТТ; во 2-м ярусе — выше
        x0, x1, z = float(g["x0"]), float(g["x1"]), float(g["d"]) / 2
        w_ra = 6.0 + text_w("Ra " + str(r["ra"]))

        def clear(x):
            """Наименьшее расстояние от точек знака Ra (вершина, текст) до ножек сверху."""
            px, py = paper(x, z)
            pts = [(px + k * w_ra / 4, py + h) for k in range(5) for h in (0.5, 4.0, 8.0)]
            d_lines = min([pt_seg_dist(a, b, q) for a, b in pts for q in lines] or [99.0])
            d_let = min([math.hypot(a - lx, b - ly) for a, b in pts for lx, ly in letters] or [99.0])
            return min(d_lines, d_let)
        def hits(x):
            """Ножка сверху режет габарит знака Ra (жёсткое ограничение)."""
            px, py = paper(x, z)
            box = [(px, py, px + w_ra, py), (px + w_ra, py, px + w_ra, py + 9.0),
                   (px + w_ra, py + 9.0, px, py + 9.0), (px, py + 9.0, px, py)]
            if any(px - 4 <= lx <= px + w_ra + 4 and py - 2 <= ly <= py + 13 for lx, ly in letters):
                return True              # на букве секущей/выносного
            if any(rects_cross((px, py, px + w_ra, py + RA_H), b) for b in shelf_boxes):
                return True              # на букве выносного у круга
            return any(seg_cross(q, e, eps=0.0) for q in lines for e in box)
        cands = [x0 + k * (x1 - x0) / 10 for k in range(0, 9)]
        ok = [x for x in cands if not hits(x)]
        if ok:
            best = max(ok, key=clear)
            px, py = paper(best, z)
            ra_boxes.append((px, py, px + w_ra, py + RA_H))
            spec["roughs"].append({"view": "front", "at": [round(best, 2), 0, z], "ra": str(r["ra"])})
            continue
        lead = lead_rough(x0, x1, z, RA_LEAD_W + text_w("Ra " + str(r["ra"])))   # шейка короче знака — на выноске
        if lead is None:                 # и у поверхности нет — второй проход: во 2-й ярус колонок
            ra_fail.append(r["feature"])
            errors.append(f"Ra на {r['feature']}: нет места ни на поверхности, ни на выноске")
            continue
        (xa, (sx, sy), box) = lead
        ra_boxes.append(box)
        spec["roughs"].append({"view": "front", "at": [round(xa, 2), 0, z], "ra": str(r["ra"]),
                               "shelf": [round((sx - ox) / s, 2), round((sy - oy) / s, 2)]})

    spec["_names"] = [cname(c) for c in tops]
    if ra_fail:
        spec["_ra_fail"] = ra_fail
    for k in ("leaders", "bases", "gdt", "roughs"):
        if not spec[k]:
            spec.pop(k)
    length = sum(math.hypot(ln[2] - ln[0], ln[3] - ln[1]) for ln, _ in top_lines)
    if notes:
        spec["composer_notes"] = notes
    return spec, errors, length


# ============================================================ втулка с осью Z и плита
# Сборка (server.py) не поворачивает разрезы: разрез стоит так, как его проецирует КОМПАС —
# направление секущей на листе сохраняется, глубина (взгляд родителя) идёт поперёк. Отсюда
# схемы ниже (опыт чертежей PRJ.02 от 30.09, память чертёжника sleeve-vertical-cutline /
# long-plate-projection-rotated-section):
#   * втулка: главный вид «#Сверху» (торец, СК вида (y, −x)), секущая вертикальная по y = 0 →
#     разрез с осью горизонтально, СК разреза (z, −x);
#   * плита: план «#Сверху» + rotate 90 (длина по горизонтали, СК вида (x, y)), поперечная
#     секущая вертикальная → разрез стоит вертикально, СК разреза (z, y).

ROW_CUT = 3                                # ряд лесенки плиты вдоль длины: секущая (штрих 12 + буква 14 мм)
GOST_DOWN = ["5:1", "4:1", "2,5:1", "2:1", "1:1", "1:2", "1:2,5", "1:4", "1:5", "1:10"]


def _spec_head(t, cdw, model, intent_path, scale, views):
    """Общая часть spec по таблице: ТТ с номерами, общий Ra штатным знаком, штамп."""
    p = t["part"]
    tt = [re.sub(r"\s*\(по умолчанию до Q\d+\)", "", x) for x in (t.get("tt") or [])
          if not x.lstrip().startswith("*") or any(is_ref(d) for d in t["dims"])]
    # радиусы с view «ТТ» — пунктом ТТ, не на виде (решение пользователя 2026-10-21: R1/R2 .008
    # при 1:2,5 — мелкие выноски на плане); пункт — после общих допусков
    rr = [f"R{num(d['value'])}" + (f" {d['tt_name']}" if d.get("tt_name") else "")
          for d in t["dims"] if d.get("view") == "ТТ" and d["type"] == "rad"]
    if rr:
        at = next((i + 1 for i, x in enumerate(tt) if GEN_TOL.match(x)), 0)
        tt.insert(at, "Радиусы " + ", ".join(rr) + ".")
    # ГОСТ 2.316: «* Размеры для справок» — такой же пункт со сквозным номером (ревьюер .008)
    tt = [f"{i}. {x.strip()}" for i, x in enumerate(tt_gost(tt), 1)]
    return {"model": model or p["model"], "cdw": cdw, "format": 3, "vertical": False,
            "scale": scale, "views": views, "dims": [], "leaders": [], "bases": [], "gdt": [], "roughs": [],
            "tt": tt,
            **({"intent": intent_path} if intent_path else {}),
            "ra": f"Ra {t['ra_general']}" if t.get("ra_general") else None, "ra_form": "spec",
            "stamp": {"marking": p.get("marking"), "name": p.get("name"), "material": p.get("material"),
                      "company": p.get("organization", "Acme Engineering"),
                      **{k: p[k] for k in ("first_use", "litera") if p.get(k)}}}


def _spec_tail(spec, notes):
    for k in ("leaders", "bases", "gdt", "roughs"):
        if not spec[k]:
            spec.pop(k)
    if notes:
        spec["composer_notes"] = notes
    return spec


def _fit_scale(want, size, limit):
    """Масштаб таблицы или ближайший меньший ГОСТ 2.302, при котором size·s ≤ limit (мм листа)."""
    i = GOST_DOWN.index(want) if want in GOST_DOWN else len(GOST_DOWN) - 1
    for sc in GOST_DOWN[i:]:
        if size * scale_of(sc) <= limit:
            return sc
    return GOST_DOWN[-1]


def _tol_text(tol):
    """«±0,05» / «+0,2» / «-0,1» → суффикс размера; Ø у позиционного снимается (знак ставит сборка)."""
    return str(tol).replace("−", "-").strip() if tol else ""


def _letters(drawn):
    """Буквы видов после букв баз, которые на листе есть (ГОСТ 2.316: не повторять)."""
    used = set()
    for b in drawn:
        used.update(c for c in b if c.isalpha())
    return letters_free(used)


DEV_RE = re.compile(r"^(±\d+([.,]\d+)?|[+-]\d+([.,]\d+)?(/[+-]?\d+([.,]\d+)?)?)$")


def _tol_fields(d):
    """Допуск таблицы → поля размера spec: числовые отклонения («+0,2», «-0,1», «±0,05») —
    индексами (dev, ГОСТ 2.307 п. 1.9; ревьюер .008: строкой того же кегля — нарушение),
    поле допуска (j6, H7) — суффиксом; «*» справочного — суффиксом."""
    tol = _tol_text(d.get("tolerance"))
    out = {}
    if tol and DEV_RE.match(tol.replace(" ", "")):
        out["dev"] = tol.replace(" ", "").replace(".", ",")
        tol = ""
    suf = tol + ("*" if is_ref(d) else "")
    if suf:
        out["suffix"] = suf
    return out


def compose_bush(t, cdw, model=None, intent_path=None):
    """Втулка с осью Z модели: торцевой вид (главный, «#Сверху», СК (y, −x)) + продольный разрез
    по плоскости пазов (вертикальная секущая y = 0, разрез слева, СК (z, −x)) + выносные: из
    разреза — угол торец/образующая (глубины канавки и паза), из торца — ширины канавки и паза
    (решение пользователя 2026-10-21: на самом торце их выносные шли по штриху секущей).
    Ø — на торце (решение пользователя 2026-10-21) или на разрезе (pos, число над осью — text_at,
    не влезает в половину отверстия — на полке); длина — сверху; база, допуск и Ra торцов — у середины торца."""
    feats = {f["id"]: f for f in t["features"]}
    kinds = lambda k: [f for f in t["features"] if f["kind"] == k]
    errors, notes = [], []
    outer = next(f for f in kinds("ступень"))["geom"]
    hole = next(f for f in kinds("отверстие"))["geom"]
    grooves, slots = kinds("канавка"), kinds("паз")
    R, rh = float(outer["d"]) / 2, float(hole["d"]) / 2
    z0, z1 = float(outer["z0"]), float(outer["z1"])
    L = z1 - z0
    for f in grooves + slots:
        if str(f["geom"].get("angle", "+X")).upper() not in ("+X", "X"):
            errors.append(f"{f['id']}: паз не в плоскости +X — секущая y = 0 его не покажет")
    vsec = next(v for v in t["views"] if v["type"] in ("разрез", "главный"))
    vend = next((v for v in t["views"] if v["type"] == "вид"), None)
    dets = [v for v in t["views"] if v["type"] == "выносной"]
    det_sec = next((v for v in dets if v.get("parent") != (vend or {}).get("id")), None)
    det_end = next((v for v in dets if vend and v.get("parent") == vend["id"]), None)
    sc = _fit_scale(vsec.get("scale", "4:1"), max(L, 2 * R), 70.0)
    if sc != vsec.get("scale"):
        notes.append(f"масштаб {vsec.get('scale')} → {sc}: не хватает места на А3")
    s = scale_of(sc)
    free = _letters(used_bases(t))
    cut = free.pop(0)
    kS, kE = f"{cut}-{cut}", "top"
    kD = free.pop(0) if det_sec else None
    kD2 = free.pop(0) if det_end else None
    sl = slots[0]["geom"] if slots else None
    gv = grooves[0]["geom"] if grooves else None
    xb = float(sl["bottom_x"]) if sl else R
    # --- места видов на листе (мм): разрез слева, торец, выносные — справа в ряд, ось на AXIS_Y
    AXIS_Y = 185.0
    xs0 = 95.0                                           # z0 разреза
    gdt_w = frame_width("0,02", "А")
    xe = xs0 + L * s + 14.0 + gdt_w + 10.0 + R * s       # центр торца — правее рамки допуска
    rz = 1.4
    x_next = xe + R * s + 40.0
    views = [{"key": kE, "orient": "#Сверху", "x": round(xe, 1), "y": AXIS_Y},
             {"key": kS, "type": "section", "from": kE, "line": [[0, -round(R + 2.5, 2)], [0, round(R + 2.5, 2)]],
              "x": round(xs0 - z0 * s, 1), "y": AXIS_Y}]
    if det_sec:                                          # угол z1: дно канавки торца и начало паза
        sd = scale_of(det_sec["scale"])
        zc = float(sl["z1"]) if sl else z1 - 0.5
        xc = (xb + float(gv["r_from"])) / 2 + 0.6 if gv else xb
        xd = x_next + rz * sd
        views.append({"key": kD, "type": "detail", "from": kS, "center": [round(zc, 2), round(-xc, 2)],
                      "r": rz, "scale": det_sec["scale"], "x": round(xd, 1), "y": AXIS_Y + 38.0})
    if det_end:                                          # торец снизу (+X): канавка и паз по ширине
        sd2 = scale_of(det_end["scale"])
        xc2 = (float(gv["r_from"]) + R) / 2 + 0.2 if gv else xb
        xd2 = x_next + rz * sd2
        views.append({"key": kD2, "type": "detail", "from": kE, "center": [0.0, round(-xc2, 2)],
                      "r": rz, "scale": det_end["scale"], "x": round(xd2, 1), "y": AXIS_Y - 30.0})   # размеры снизу — выше ТТ
    for v in views[2:]:
        if v["x"] + rz * scale_of(v["scale"]) + 15.0 > FRAME[2]:
            errors.append(f"выносной {v['key']}: правый край за рамкой")
    spec = _spec_head(t, cdw, model, intent_path, sc, views)
    if float((t["part"].get("model_props") or {}).get("mass_g") or 1e9) < 100:
        spec["mass_grams"] = True
    vkey = {vsec["id"]: kS}
    if vend:
        vkey[vend["id"]] = kE
    if det_sec:
        vkey[det_sec["id"]] = kD
    if det_end:
        vkey[det_end["id"]] = kD2
    for d in t["dims"]:
        f = feats[d["features"][0]]
        g = f["geom"]
        key = vkey.get(d["view"])
        it = {"view": key, "id": d["id"], "kind": "lin", **_tol_fields(d)}
        if key == kS and d["type"] == "dia":
            r = float(g["d"]) / 2
            zp = z0 + (0.28 if f["kind"] == "ступень" else 0.72) * L
            label = "Ø" + num(2 * r)
            half = text_w(label) / 2 / s
            it.update({"orient": "v", "prefix": "Ø", "pos": round(zp, 2),
                       "ref": {"type": "points", "p1": [-r, 0, round(zp, 2)], "p2": [r, 0, round(zp, 2)]}})
            lo, hi = half + 1.5 / s, rh - half - 1.0 / s     # число — в пустоте отверстия, над осью
            if hi >= lo:
                it["text_at"] = round(lo, 2)
            else:                                         # не влезает в половину — на полке снизу
                it["shelf"] = [round(zp + 6.0 / s, 2), round(-(R + 14.0 / s), 2)]
        elif key == kE and d["type"] == "dia":           # Ø на торце (решение пользователя 2026-10-21:
            # на разрезе размерные линии Ø резали штриховку): вертикаль — секущая, низ (+X) — паз,
            # поэтому Ø9 — влево-вверх, Ø5,3 — влево-вниз, ножка выводит полку за наружный контур
            r = float(g["d"]) / 2
            out = f["kind"] == "ступень"
            it.update({"kind": "dia", "angle": 135.0 if out else 225.0,
                       "leg": 8.0 if out else round((R - r) * s + 8.0, 1),
                       "ref": {"type": "circle", "center": [0.0, 0.0, z1], "d": float(g["d"])}})
        elif key == kS and d["type"] == "lin":
            it.update({"orient": "h", "side": "top",
                       "ref": {"type": "points", "p1": [-R, 0, z0], "p2": [-R, 0, z1]}})
        elif key == kD2 and d["type"] == "lin":           # ширины — на выносном торца, обе снизу
            w = float(d["value"])
            n_same = len([q for q in d["features"] if feats[q]["kind"] == f["kind"]])
            if n_same > 1:
                it["prefix"] = f"{n_same} паза "
            # точки — у образующей: выносные продолжают боковые стенки паза, ряд 0 — паз, 1 — канавка
            xo = R - 0.01
            it.update({"orient": "h", "side": "bottom", "row": 1 if f["kind"] == "канавка" else 0,
                       "ref": {"type": "points", "p1": [xo, -w / 2, z1], "p2": [xo, w / 2, z1]}})
        elif key == kD and d["type"] == "lin":
            dep = float(d["value"])
            if f["kind"] == "канавка":               # глубина канавки торца z1 — вдоль оси
                x = (float(gv["r_from"]) + xb) / 2
                it.update({"orient": "h", "side": "bottom",
                           "ref": {"type": "points", "p1": [round(x, 3), 0, z1 - dep], "p2": [round(x, 3), 0, z1]}})
            else:                                    # глубина паза от образующей — поперёк
                zq = float(sl["z1"]) - 0.6
                it.update({"orient": "v", "side": "left",
                           "ref": {"type": "points", "p1": [xb, 0, round(zq, 2)], "p2": [R, 0, round(zq, 2)]}})
        else:
            errors.append(f"{d['id']}: {d['type']} в виде {d['view']} — схема втулки не знает")
            continue
        if abs(float(d["value"]) - round(float(d["value"]))) > 1e-6:
            it["text"] = num(d["value"])
        spec["dims"].append(it)
    # --- база, допуск и Ra торцов (СК разреза: x = z, y = −x): стрелки — у середины торца стенки −X
    # (ревьюер .014: у образующей стрелка упиралась в угол торец/образующая); база ближе к отверстию
    off = 20.0 / s
    xa, xr = rh + 0.2 * (R - rh), rh + 0.55 * (R - rh)
    for b in t.get("bases") or []:
        if b["id"] not in used_bases(t):
            continue
        zb = float(feats[b["feature"]]["geom"]["z"])
        side = -1 if abs(zb - z0) < 1e-6 else 1
        spec["bases"].append({"view": kS, "at": [-xa, 0, zb], "frame": [round(zb + side * off, 2), round(xa, 2)],
                              "letter": b["id"]})
    for g in t.get("gdt") or []:
        sign = GDT_SIGN.get(str(g["kind"]).lower())
        zg = float(feats[g["feature"]]["geom"]["z"])
        if sign is None or abs(zg - z1) > 1e-6:
            errors.append(f"{g['id']}: допуск «{g['kind']}» на {g['feature']} — схема втулки ставит только на торец z1")
            continue
        spec["gdt"].append({"view": kS, "at": [-xa, 0, zg], "frame": [round(zg + 11.2 / s, 2), round(R + 8.0 / s, 2)],
                            "sign": sign, "value": str(g["value"]), **({"base": g["base"]} if g.get("base") else {}),
                            "base_pos": 1})
    for r in t.get("roughness") or []:
        if str(r["ra"]) == str(t.get("ra_general")):
            continue
        zr = float(feats[r["feature"]]["geom"]["z"])
        sgn = -1 if abs(zr - z0) < 1e-6 else 1        # Ra выше базы/рамки, полка — наружу от торца
        spec["roughs"].append({"view": kS, "at": [-xr, 0, zr], "ra": str(r["ra"]),
                               "shelf": [round(zr + sgn * (18.0 if sgn < 0 else 9.2) / s, 2),
                                         round(R + (12.0 if sgn < 0 else 22.0) / s, 2)]})
    return _spec_tail(spec, notes), errors


def _rect(g):
    """(x0, x1, y0, y1) прямоугольного элемента плиты: поля x/y или opening.x/y."""
    src = g if "x" in g and isinstance(g["x"], list) else (g.get("opening") or {})
    if not (isinstance(src.get("x"), list) and isinstance(src.get("y"), list)):
        return None
    return float(src["x"][0]), float(src["x"][1]), float(src["y"][0]), float(src["y"][1])


def _z_overlap(a, b):
    """Середина общего участка по Z двух элементов (цековка и окно: где цековка вскрывает
    стенку); без общего участка или без окна — середина первого."""
    za = [float(q) for q in a["z"]]
    if b and b.get("z"):
        zb = [float(q) for q in b["z"]]
        lo, hi = max(min(za), min(zb)), min(max(za), max(zb))
        if hi > lo:
            return round((lo + hi) / 2, 2)
    return round((min(za) + max(za)) / 2, 2)


PLAN_W = 250.0     # мм листа: длина плана А3 (справа — ряды поперечных размеров, буква базы)
PLAN_Y = 214.0     # мм листа: ось плана (сверху — два яруса выносок под графой 26 до рамки)
PLATE_TIER = 14.0  # мм: второй ярус выносок плана (полка 7 + текст над ней 6,5)
PLATE_ROW = 10.0   # мм: шаг рядов плана (8 — тексты с отклонением индексом и рамки касались, Ч19)
PLATE_ROW_V = 12.0 # мм: шаг рядов поперечных (рамки теоретически точных вертикальных текстов шире)
BREAK_GAP = 10.0   # мм листа: зазор разрыва вида (= _draw.BREAK_GAP)


def _break_zone(xs, need, cx):
    """Зона разрыва плана [a, b] длиной ≥ need по серединам между колоннами отверстий xs
    (линия обрыва не режет отверстие), ближе к середине cx; None — не нашли."""
    mids = [(p + q) / 2 for p, q in zip(xs, xs[1:])]
    best = None
    for i, a in enumerate(mids):
        for b in mids[i + 1:]:
            if b - a + 1e-6 >= need:
                key = (round(b - a, 3), abs((a + b) / 2 - cx))
                if best is None or key < best[0]:
                    best = (key, a, b)
                break
    return (best[1], best[2]) if best else None


def compose_plate(t, cdw, model=None, intent_path=None):
    """Плита (рамка) толщиной по Z, длина по X: план со стороны базы А («#Сверху», rotate 90 —
    длина по горизонтали) в масштабе таблицы, длинный — С РАЗРЫВОМ (решение пользователя
    2026-10-21: 1:2,5 с разрывом, а не 1:4); поперечный разрез по колонне отверстий (стоит
    вертикально; не влезает справа — на лист 2 в масштабе таблицы), выносной из разреза.
    План: вдоль длины — все размеры снизу вложенными пролётами от ряда ROW_CUT (линии через
    разрыв — с номиналом text), поперёк — справа; позиции рядов заданы (pos) — к ним
    привязаны знаки баз Б (на размерной линии ширины) и В (на размерной линии длины). Координаты
    массива — в рамках, позиционный — под полкой выноски группы с базами А|Б|В; база А — на
    разрезе. Выноски (радиусы, фаски, группа отверстий) — сверху колонками, соседние — ярусами."""
    feats = {f["id"]: f for f in t["features"]}
    errors, notes = [], []
    outer = next(f for f in t["features"] if _rect(f.get("geom") or {}) and f["kind"] == "прочее")
    x0, x1, y0, y1 = _rect(outer["geom"])
    zf = [float(z) for z in outer["geom"]["z"]]
    z_lo, z_hi = min(zf), max(zf)
    Lx, Wy = x1 - x0, y1 - y0
    vmain = next(v for v in t["views"] if v["type"] == "главный")
    vsec = next((v for v in t["views"] if v["type"] == "разрез"), None)
    vdet = next((v for v in t["views"] if v["type"] == "выносной"), None)
    holes = [f for f in t["features"] if f["kind"] == "отверстие" and (f.get("geom") or {}).get("centers")]
    cbs = [f for f in t["features"] if f["kind"] == "расточка"]       # окна (прямоугольник) и цековки (d)
    hc = [tuple(map(float, c)) for c in holes[0]["geom"]["centers"]] if holes else []
    cols = sorted({c[0] for c in hc})
    # --- масштаб и разрыв плана
    sc = vmain.get("scale", "1:4")
    s = scale_of(sc)
    zone = None
    if Lx * s > PLAN_W:
        need = Lx - (PLAN_W - BREAK_GAP) / s
        zone = _break_zone(cols or [x0 + k * Lx / 20 for k in range(21)], need, (x0 + x1) / 2)
        if zone is None:
            sc = _fit_scale(sc, Lx, PLAN_W)
            s = scale_of(sc)
            notes.append(f"план: разрыв не найден — масштаб {vmain.get('scale')} → {sc}")
    gap_v = BREAK_GAP / s

    def mx(x):
        """x модели → x вида с разрывом (правее зоны — сдвиг); в зоне — край зоны."""
        if zone is None or x <= zone[0]:
            return x
        if x >= zone[1]:
            return x - (zone[1] - zone[0]) + gap_v
        return zone[0]

    def raw(u):
        """u вида на листе (с разрывом) → сырая u: обозначения задаются сырыми, КОМПАС переносит их
        вместе с геометрией (сборка .008)."""
        if zone is None or u <= zone[0]:
            return u
        return u + (zone[1] - zone[0]) - gap_v

    def crosses(a, b):
        return zone is not None and min(a, b) < zone[0] and max(a, b) > zone[1]
    ox, oy = 35.0 - x0 * s, PLAN_Y - (y0 + y1) / 2 * s
    paper = lambda x, y: (ox + mx(x) * s, oy + y * s)
    gdt_pos = {g["feature"]: g for g in t.get("gdt") or [] if "позицион" in str(g["kind"]).lower()}
    pos_bases = []
    for g in gdt_pos.values():
        pos_bases = list(g.get("base_system") or ([g["base"]] if g.get("base") else []))
    bases_i = {b["id"]: b for b in t.get("bases") or []}
    free = _letters(pos_bases)
    kM = "main"
    views = [{"key": kM, "orient": "#Сверху", "x": round(ox, 2), "y": round(oy, 2), "rotate": 90, "axes": ["h", "v"]}]
    if zone:
        views[0]["break"] = [{"p1": [round(zone[0], 3), 0.0, z_hi], "p2": [round(zone[1], 3), 0.0, z_hi],
                              "gap": BREAK_GAP}]
        notes.append(f"план {sc} с разрывом x {num(zone[0])}…{num(zone[1])} (зазор {num(BREAK_GAP)} мм листа)")
    vkey = {vmain["id"]: kM}
    cut_x, ss, sheet2 = None, s, False
    if vsec:
        m = re.search(r"X\s*=\s*([-\d.,]+)", str(vsec.get("cut", "")))
        cut_x = float(m.group(1).replace(",", ".")) if m else (cols[1] if len(cols) > 1 else (x0 + x1) / 2)
        ssc = vsec.get("scale", "1:1")
        ss = scale_of(ssc)
        sheet2 = Wy * ss > 125.0      # разрез стоит вертикально: выше места справа — на лист 2 (Р05)
        if sheet2:
            notes.append(f"разрез {ssc} и выносной — на листе 2: {Wy:g}·{ssc} выше места справа от плана")
        kS = f"{free[0]}-{free[0]}"
        free = free[1:]
        vkey[vsec["id"]] = kS
        # лист 2: разрез и выносной симметрично относительно середины поля (колонкой не входят по высоте)
        xs_c, ys_c = (140.0, 152.0) if sheet2 else (345.0, 213.0)
        views.append({"key": kS, "type": "section", "from": kM, "axes": ["h"],
                      "line": [[cut_x, round(y0 - 8.0 / s, 2)], [cut_x, round(y1 + 8.0 / s, 2)]],
                      **({"scale": ssc} if ssc != sc else {}), **({"sheet": 2} if sheet2 else {}),
                      "x": round(xs_c - (z_lo + z_hi) / 2 * ss, 1), "y": round(ys_c - (y0 + y1) / 2 * ss, 1)})
    wg = next((f["geom"] for f in cbs if _rect(f["geom"])), None)
    win = _rect(wg) if wg else None
    cb = next((f["geom"] for f in cbs if "d" in f["geom"]), None)
    ys_h = sorted({c[1] for c in hc})
    if vdet and vsec:
        kD = free[0]
        vkey[vdet["id"]] = kD
        cy = ys_h[-1] if ys_h else y1           # верхняя цековка (таблица: Y = +55); база А — внизу
        zc = _z_overlap(feats[vdet["shows"][0]]["geom"], wg)
        yc = (win[3] + cy) / 2 if win else cy
        dx, dy = (290.0, 160.0) if sheet2 else (125.0, 100.0)
        views.append({"key": kD, "type": "detail", "from": vkey[vsec["id"]], "center": [round(zc, 2), round(yc, 2)],
                      "r": 6.0, "scale": vdet.get("scale", "5:1"), "x": dx, "y": dy,
                      "shelf": [round(z_hi + 14.0 / ss, 2), round(y1 - 8.0 / ss, 2)],   # ниже заголовка разреза
                      **({"sheet": 2} if sheet2 else {})})
    spec = _spec_head(t, cdw, model, intent_path, sc, views)
    # --- линейные размеры плана: вдоль длины — снизу, поперёк — справа; ряды — явно (pos)
    along_x, along_y = [], []
    for d in t["dims"]:
        if vkey.get(d["view"]) != kM or d["type"] != "lin":
            continue
        f = feats[d["features"][0]]
        g = f.get("geom") or {}
        v = float(d["value"])
        it = {"view": kM, "id": d["id"], "kind": "lin", **_tol_fields(d)}
        rc = _rect(g)
        if rc:
            a0, a1, b0, b1 = rc
            if abs(v - (a1 - a0)) <= 0.06:
                along_x.append((v, it, (a0, a1, b0)))
            elif abs(v - (b1 - b0)) <= 0.06:
                along_y.append((v, it, (a1, b0, b1, False)))
            else:
                errors.append(f"{d['id']}: {num(v)} не совпадает с размерами элемента {f['id']}")
        elif g.get("centers"):
            cs = [tuple(map(float, c)) for c in g["centers"]]
            xs_ = sorted({c[0] for c in cs})
            fr = f["id"] in gdt_pos
            if abs(v - (xs_[-1] - xs_[0])) <= 0.06:
                steps = {round(b - a, 3) for a, b in zip(xs_, xs_[1:])}
                if len(steps) == 1 and len(xs_) > 2:
                    it["prefix"] = f"{len(xs_) - 1}×{num(steps.pop())}="
                along_x.append((v, it, (xs_[0], xs_[-1], min(c[1] for c in cs), fr)))
            else:
                hit = [(x, ya, yb_) for x in xs_ for ya in sorted(c[1] for c in cs if abs(c[0] - x) < 1e-6)
                       for yb_ in sorted(c[1] for c in cs if abs(c[0] - x) < 1e-6) if abs(yb_ - ya - v) <= 0.06]
                if not hit:
                    errors.append(f"{d['id']}: {num(v)} — нет пары отверстий на таком расстоянии")
                    continue
                x, ya, yb_ = max(hit, key=lambda h: h[0])
                along_y.append((v, it, (x, ya, yb_, fr)))
    pos_of = {}
    for k, (v, it, src) in enumerate(sorted(along_x, key=lambda q: q[0])):
        a, b, yb = src[0], src[1], src[2]
        y_dim = y0 - (ROW0 - 2.0 + PLATE_ROW * (ROW_CUT + k)) / s
        it.update({"orient": "h", "side": "bottom", "pos": round(y_dim, 2),
                   "ref": {"type": "points", "p1": [a, yb, z_hi], "p2": [b, yb, z_hi]}})
        if len(src) > 3 and src[3]:
            it["frame"] = True
        if crosses(a, b) and "text" not in it:
            it["text"] = num(v)                       # через разрыв: номинал — по модели (сборка сверяет)
        elif abs(v - round(v)) > 1e-6:
            it["text"] = num(v)
        pos_of[it["id"]] = (y_dim, a, b)
        spec["dims"].append(it)
    # нижний ряд плана — над ТТ (сборка .008, круг 3: пункт «Радиусы …» поднял ТТ на строку, и его
    # первая строка легла на линию 970) — план выше на недостающее; paper() берёт oy при вызове
    if pos_of:
        y_low = oy + min(q[0] for q in pos_of.values()) * s
        tt_top = STAMP_TOP + 5.0 + 7.0 * tt_lines(spec["tt"]) + 6.0
        if y_low < tt_top:
            oy += tt_top - y_low
            views[0]["y"] = round(oy, 2)
            notes.append(f"план выше на {num(round(tt_top - y_low, 1))} мм: нижний ряд размеров — над ТТ")
    for k, (v, it, src) in enumerate(sorted(along_y, key=lambda q: q[0])):
        x, ya, yb_, fr = src
        x_dim = x1 + (ROW0 - 2.0 + PLATE_ROW_V * k) / s    # сырые координаты: правее разрыва
        it.update({"orient": "v", "side": "right", "pos": round(x_dim, 2),
                   "ref": {"type": "points", "p1": [x, ya, z_hi], "p2": [x, yb_, z_hi]}})
        if fr:
            it["frame"] = True
        if abs(v - round(v)) > 1e-6:
            it["text"] = num(v)
        pos_of[it["id"]] = (x_dim, ya, yb_)
        spec["dims"].append(it)
    # --- знаки баз плоскостей симметрии — на продолжении размерных линий ширины (Б) и длины (В)
    for bid in pos_bases[1:]:
        tgt = next((d for d in t["dims"] if d.get("scheme") == "габарит" and vkey.get(d["view"]) == kM
                    and str(d.get("from", "")).endswith(bid)), None)
        if tgt is None or tgt["id"] not in pos_of:
            errors.append(f"база {bid}: нет габарита плана от неё — знак ставить некуда")
            continue
        q, a, b2 = pos_of[tgt["id"]]
        it_t = next(x for x in spec["dims"] if x["id"] == tgt["id"])
        if it_t["orient"] == "v":                     # ширина: треугольник под нижней стрелкой
            spec["bases"].append({"view": kM, "at": [round(q, 2), round(a - 3.0 / s, 2)],
                                  "frame": [round(q, 2), round(a - 14.0 / s, 2)], "letter": bid})
        else:                                         # длина: треугольник правее правой стрелки
            # левее левой стрелки, рамка под треугольником: справа внизу — ТТ, а смещение ветви вбок
            # КОМПАС на виде с разрывом не переносит (сборка .008)
            spec["bases"].append({"view": kM, "at": [round(a - 4.0 / s, 2), round(q, 2)],
                                  "frame": [round(a - 4.0 / s, 2), round(q - 11.0 / s, 2)], "letter": bid})
    # --- выноски сверху: радиусы углов, фаски, группа отверстий; колонки по порядку анкеров
    tops = []
    for d in t["dims"]:
        if vkey.get(d["view"]) != kM or d["type"] not in ("rad", "chamfer", "dia"):
            continue
        f = feats[d["features"][0]]
        g = f.get("geom") or {}
        if d["type"] == "rad":
            rc = _rect(g)
            r = float(d["value"])
            n = 4 if rc else 1
            # углы разведены по концам: R окна (больший прямоугольник) — слева сверху, R губки — справа
            big = rc and (rc[1] - rc[0]) >= 0.97 * Lx
            cx_, cy_ = (rc[0] + r * 0.29, rc[3] - r * 0.29) if big else (rc[1] - r * 0.29, rc[3] - r * 0.29)
            tops.append({"at": (cx_, cy_), "text": "R" + num(r), "under": f"{n} места" if n > 1 else None,
                         "w": 4.0 + text_w(f"{n} места"), "kind": "leader"})
        elif d["type"] == "chamfer":
            c = float(d["value"])
            n = int(g.get("n", 1))
            txt = f"{n} фаски {num(c)}×45°" if n > 1 else f"{num(c)}×45°"
            tops.append({"at": (x0 + c / 2, y1 - c / 2), "text": txt, "under": None, "w": 4.0 + text_w(txt),
                         "kind": "leader"})
        else:
            cs = [tuple(map(float, c)) for c in g.get("centers") or []]
            if not cs:
                errors.append(f"{d['id']}: у {f['id']} нет centers — выноску группы ставить некуда")
                continue
            ymax = max(q[1] for q in cs)
            top_row = [c for c in cs if abs(c[1] - ymax) < 1e-6
                       and (zone is None or not zone[0] - 1e-6 <= c[0] <= zone[1] + 1e-6)]
            target = (zone[0] - 0.5 * (zone[0] - x0)) if zone else x0 + 0.42 * Lx
            h = min(top_row, key=lambda c: abs(c[0] - target))
            n = len(cs)
            tf = _tol_fields(d)
            label = f"{n} отв. Ø{num(d['value'])}{tf.get('dev', '')}"
            gp = gdt_pos.get(f["id"])
            tops.append({"at": h, "d": float(d["value"]), "text": label, "w": 6.0 + text_w(label), "kind": "dia",
                         "prefix": f"{n} отв. " if n > 1 else None, "tf": tf, "id": d["id"],
                         "tol": str(gp["value"]).replace("Ø", "").strip() if gp else None,
                         "tol_base": "|".join(pos_bases) if pos_bases else None})
    tops.sort(key=lambda c: mx(c["at"][0]))
    y_sh0 = oy + y1 * s + 14.0
    anchors = [paper(*c["at"])[0] for c in tops]
    NEAR = 30.0       # соседние анкеры ближе NEAR мм — ярусами: левый выше (ревьюер .008)
    tier = [1 if i + 1 < len(tops) and anchors[i + 1] - anchors[i] < NEAR else 0 for i in range(len(tops))]
    x_max = 300.0 if vsec and not sheet2 else FRAME[2] - 5.0
    xs_sh = [0.0] * len(tops)
    for k in (0, 1):
        idx = [i for i in range(len(tops)) if tier[i] == k]
        for i, x in zip(idx, columns([anchors[i] for i in idx], [tops[i]["w"] for i in idx],
                                     FRAME[0] + 5.0, x_max, gap=8.0)):
            xs_sh[i] = x
    lines = []
    for c, sx, k in zip(tops, xs_sh, tier):
        y_sh = y_sh0 + PLATE_TIER * k
        ax, ay = paper(*c["at"])
        if c["kind"] == "dia":                        # ножка вправо-вверх; полка с текстом и рамкой ⊕
            dx, dy = max(sx - ax, 3.0), y_sh - ay
            ang = round(math.degrees(math.atan2(dy, dx)), 1)
            leg = round(math.hypot(dx, dy) - c["d"] / 2 * s, 1)
            it = {"view": kM, "id": c["id"], "kind": "dia", "angle": ang, "leg": leg,
                  "ref": {"type": "circle", "center": [c["at"][0], c["at"][1], z_hi], "d": c["d"]}, **c["tf"]}
            if c["prefix"]:
                it["prefix"] = c["prefix"]
            if c["tol"]:
                it["tol"] = {"value": c["tol"], "branch": False,     # под полкой, без своей стрелки
                             **({"base": c["tol_base"]} if c["tol_base"] else {})}
            spec["dims"].append(it)
            lines.append(((ax, ay, ax + dx, y_sh), c["text"]))
        else:
            spec["leaders"].append({"view": kM, "at": [round(c["at"][0], 3), round(c["at"][1], 3), z_hi],
                                    "shelf": [round(raw((sx - ox) / s), 2), round((y_sh - oy) / s, 2)],
                                    "text": c["text"], **({"under": c["under"]} if c.get("under") else {})})
            lines.append(((ax, ay, sx, y_sh), c["text"]))
    for i, (ln, n1) in enumerate(lines):
        for ln2, n2 in lines[i + 1:]:
            if seg_cross(ln, ln2, eps=0.0):
                errors.append(f"ножки {n1} и {n2} пересекаются")
    # секущая — через то же отверстие ряда, вне зоны разрыва и в стороне от выносок сверху (штрих
    # и буква занимают ~16 мм по X листа над плитой; сборка .008: при x = 69 буква резала «4 фаски»)
    if vsec and hc:
        busy = [(min(ln[0], ln[2]) - 3.0, max(ln[0], ln[2]) + c["w"] + 3.0) for (ln, _), c in zip(lines, tops)]
        busy += [(paper((a + b) / 2, 0)[0] - 22.0, paper((a + b) / 2, 0)[0] + 22.0)    # тексты рядов снизу
                 for _, a, b in [pos_of[i] for i in pos_of if any(x["id"] == i and x["orient"] == "h"
                                                                   for x in spec["dims"])]]
        ys_at = {}
        for x, y in hc:
            ys_at.setdefault(x, set()).add(y)
        same = [x for x, ys_ in ys_at.items() if ys_ == ys_at.get(cut_x, ys_)
                and (zone is None or not zone[0] - 1e-6 <= x <= zone[1] + 1e-6)]
        ok = [x for x in same if not any(a < paper(x, 0)[0] + 16.0 and paper(x, 0)[0] - 16.0 < b for a, b in busy)]
        if not ok:
            errors.append("секущая: нет отверстия ряда вне разрыва и в стороне от выносок сверху")
        else:
            x_new = min(ok, key=lambda x: abs(x - cut_x))
            if abs(x_new - cut_x) > 1e-6:
                notes.append(f"секущая x = {num(x_new)} вместо {num(cut_x)} таблицы — то же отверстие ряда")
                cut_x = x_new
            for v in spec["views"]:
                if v.get("type") == "section" and v["from"] == kM:
                    v["line"] = [[round(cut_x, 3), p[1]] for p in v["line"]]
    y_top = y_sh0 + PLATE_TIER * max(tier or [0])
    if y_top + 8.0 > FRAME[3]:
        errors.append(f"выноски сверху: полки на {y_top:.0f} мм, под рамкой нет места")
    # --- разрез (СК (z, y)) и выносной
    if vsec:
        kS = vkey[vsec["id"]]
        lip = next((f["geom"] for f in t["features"] if f["kind"] == "буртик"), None)
        yc_lo = ys_h[0] if ys_h else y0
        for d in t["dims"]:
            if vkey.get(d["view"]) != kS:
                continue
            f = feats[d["features"][0]]
            g = f.get("geom") or {}
            v = float(d["value"])
            it = {"view": kS, "id": d["id"], "kind": "lin", **_tol_fields(d)}
            if abs(v - round(v)) > 1e-6:
                it["text"] = num(v)
            rc = _rect(g)
            if f is outer and abs(v - (z_hi - z_lo)) <= 0.06:            # толщина — у нижней кромки
                it.update({"orient": "h", "side": "bottom",
                           "ref": {"type": "points", "p1": [cut_x, y0, z_lo], "p2": [cut_x, y0, z_hi]}})
            elif rc and f["kind"] == "расточка" and abs(v - float(g.get("depth", -1))) <= 0.06:
                zz = [float(q) for q in g["z"]]       # глубина окна — в пустоте окна над губкой: выносная
                lip_y = float(lip["opening"]["y"][0]) if lip and lip.get("opening") else rc[2] + 2.0
                yq = round((rc[2] + lip_y) / 2, 2)    # от кромки шла через цековку и её размер (пилот .008)
                it.update({"orient": "h", "pos": yq,
                           "ref": {"type": "points", "p1": [cut_x, yq, min(zz)], "p2": [cut_x, yq, max(zz)]}})
            elif rc and abs(v - (rc[3] - rc[2])) <= 0.06:
                zz = [float(q) for q in (g.get("z") or g.get("opening", {}).get("z") or [z_hi])]
                zq = max(zz) if f["kind"] == "расточка" else min(zz)
                it.update({"orient": "v", "side": "right" if zq >= (z_lo + z_hi) / 2 else "left",
                           "ref": {"type": "points", "p1": [cut_x, rc[2], zq], "p2": [cut_x, rc[3], zq]}})
            elif f["kind"] == "расточка" and g is cb and d["type"] == "dia":    # цековка у −y: Ø поперёк
                zz = [float(q) for q in g["z"]]
                face = z_lo if abs(min(zz) - z_lo) < 1e-6 else z_hi
                n_cb = int(g.get("n", 1))           # «36 отв. Ø10» — на полке слева (не влезает в 20 мм)
                it.update({"orient": "v", "side": "left" if face == z_lo else "right",
                           "prefix": (f"{n_cb} отв. " if n_cb > 1 else "") + "Ø",
                           "shelf": [round(z_lo - 40.0 / ss, 2), round(yc_lo + 14.0 / ss, 2)],
                           "ref": {"type": "points", "p1": [cut_x, yc_lo - v / 2, face], "p2": [cut_x, yc_lo + v / 2, face]}})
            elif f["kind"] == "расточка" and g is cb and abs(v - float(g.get("depth", -1))) <= 0.06:
                zz = [float(q) for q in g["z"]]       # глубина цековки — внутри неё (пусто, не на металле);
                # текст — у торца цековки: посередине его режет выносная глубины окна (Ч19, сборка .008)
                z_txt = min(zz) + 0.4 * (max(-15.0, min(zz)) - min(zz) if wg is None else
                                         max(min(float(q) for q in wg["z"]), min(zz)) - min(zz))
                it.update({"orient": "h", "pos": round(yc_lo, 2), "text_at": round(z_txt, 2),
                           "ref": {"type": "points", "p1": [cut_x, yc_lo, min(zz)], "p2": [cut_x, yc_lo, max(zz)]}})
            else:
                errors.append(f"{d['id']}: {num(v)} в разрезе — схема плиты не знает")
                continue
            spec["dims"].append(it)
        if pos_bases:                                 # база А (плоскость прилегания) — на разрезе вверху
            ba = bases_i.get(pos_bases[0])
            zb = float(feats[ba["feature"]]["geom"]["z"]) if ba and "z" in feats[ba["feature"]]["geom"] else z_hi
            yb = (win[2] + y0) / 2 if win else y0 + 2.0
            spec["bases"].append({"view": kS, "at": [cut_x, round(yb, 2), zb],
                                  "frame": [round(zb + 24.0 / ss, 2), round(yb, 2)], "letter": pos_bases[0]})
        if vdet:
            kD = vkey[vdet["id"]]
            for d in t["dims"]:
                if vkey.get(d["view"]) != kD:
                    continue
                v = float(d["value"])
                if not (win and cb):
                    errors.append(f"{d['id']}: выносной без окна/цековки — схема плиты не знает")
                    continue
                y_edge = (ys_h[-1] if ys_h else y1) - float(cb["d"]) / 2   # кромка верхней цековки у стенки окна
                zq = _z_overlap(cb, wg)
                if abs(abs(win[3] - y_edge) - v) > 0.06:
                    errors.append(f"{d['id']}: {num(v)} ≠ стенка окна {num(win[3])} − кромка цековки {num(y_edge)}")
                    continue
                spec["dims"].append({"view": kD, "id": d["id"], "kind": "lin", "orient": "v", "side": "right",
                                     "text": num(v), **_tol_fields(d),
                                     "ref": {"type": "points", "p1": [cut_x, min(y_edge, win[3]), round(zq, 2)],
                                             "p2": [cut_x, max(y_edge, win[3]), round(zq, 2)]}})
        if lip is None:
            notes.append("буртика нет — размеры губки не ставятся")
    for r in t.get("roughness") or []:
        if str(r["ra"]) != str(t.get("ra_general")):
            errors.append(f"Ra {r['ra']} на {r['feature']}: схема плиты ставит только общий знак")
    return _spec_tail(spec, notes), errors


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__)
        sys.exit(2)
    with open(a[0], encoding="utf-8") as f:
        intent = json.load(f)
    spec, errs = compose(intent, os.path.abspath(a[1]), intent_path=os.path.abspath(a[0]))
    if "--stamp" in a:                   # графы штампа сверх таблицы: first_use (25), litera (Лит.)
        spec["stamp"].update(json.loads(a[a.index("--stamp") + 1]))
    out = a[a.index("--out") + 1] if "--out" in a else os.path.splitext(a[1])[0] + ".spec.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    print(f"SPEC: {out}  размеров {len(spec['dims'])}, видов {len(spec['views'])}, "
          f"ошибок компоновки {len(errs)}")
    for n in spec.get("composer_notes") or []:
        print(" ~", n)
    for e in errs:
        print(" -", e)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
