# -*- coding: utf-8 -*-
"""Шаблон чертежа класса «плита» (задача 16): плоская деталь с массивом отверстий, схема
инженера из эталона ACME.100.04.00.001 Плита основания.

    plate_spec(summary, cdw_path, model_path, stamp=None, tt=None, ra=None) -> dict
    python -X utf8 templates/plate.py <model.m3d> <out.cdw> [--spec-only] [--stamp '{...}']

summary — ответ kompas_model_summary (JSON → dict, с small_planes). Класс (иначе ValueError —
деталь уходит общему планировщику): ось толщины — Z (главный вид «#Сверху»), толщина ≤ 0,05·min(L, W),
есть хотя бы одна группа отверстий вдоль толщины.

Схема (эталон): лист 1 А3 — ОДИН вид на плоскость плиты, длинная сторона горизонтально (rotate 90
при L по X), габариты L и W (округлённые), толщина выноской «s8» с точкой; координаты элементов —
от баз (кромки, оси симметрии) в РАМКАХ (теоретически точные) + позиционный допуск на группу под
текстом «N отв.Ø…» (значение по Ø: project_rules.json:pos_tol_by_d); полки Ø-размеров выведены
ножками ЗА контур вида в свободную сторону (ножка — поле leg); □ у квадратной сетки; узлы PCD —
выносной А (прямоугольная зона, крупнее) с Ø окружности центров, углами и «n отв.» на узел, заголовок
«k места»; центральная плотная зона — выносной Б (масштаб листа) на листе 2; пазы у одного угла —
выносной В на листе 2 («k места»). ТТ — два пункта эталона; Ra — не ставится (ra null).
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import drawing_rules   # noqa: E402
import model_summary   # noqa: E402

TT = ["1 Общие допуски по ГОСТ 30893.1-m",
      "2 Острые кромки притупить фаской не более 0,5 мм."]

MAIN_CENTER = (190.0, 158.0)     # мм листа: центр главного вида (эталон: 177/155, слева полоса под размеры)
MAIN_MAX = (255.0, 180.0)        # мм листа: главный вид не шире/выше (полосы лесенок, ТТ, штамп)
DETAIL_MAX = 95.0                # мм листа: выносной узла (А) не больше (сторона зоны)
DETAIL_V_MAX = 70.0              # мм листа: выносной пазов (В)
EDGE_IN_DETAIL = 3.0             # узел ближе EDGE_IN_DETAIL·Ø окружности центров к кромке — кромка в зоне
ZONE_B_FRAC = (0.30, 0.22)       # доля L/W: отверстия ближе к центру — в выносной Б
ZONE_MARGIN = 60.0               # мм модели: запас зоны Б вокруг отверстий
DETAIL_A_MARGIN = 90.0           # мм модели: запас зоны узла вокруг PCD
SHEET2 = {"А": (115.0, 212.0), "Б": (300.0, 220.0), "В": (255.0, 100.0)}   # центры выносных на листе 2
THIN = 0.05                      # толщина ≤ THIN·min(L, W) — плита
LADDER0, LADDER_STEP = 10.0, 8.0 # мм листа: лесенка линейных размеров (layout.ladder)
SHELF_GAP = 5.0                  # мм листа: полка Ø-размера за последним рядом лесенки
LEG_MAX = 90.0                   # мм листа: длиннее ножку не делать (выноска через всю плиту)
LEG_MIN, LEG_STEP = 12.0, 6.0    # мм листа: сетка длин ножки (эталон: ножки 30–60 мм)
# направления ножки (ГОСТ 2.316: не параллельно размерным и выносным линиям — не 0/90°)
CALLOUT_ANGLES = (30, 45, 60, 120, 135, 150, 210, 225, 240, 300, 315, 330)
# лист 1 А3: куда полки не ставить — штамп и ТТ (x ≥ 230, y ≤ 110), поля рамки
FORBIDDEN_SHEET1 = [(228.0, 0.0, 420.0, 112.0), (0.0, 0.0, 26.0, 297.0), (0.0, 272.0, 420.0, 297.0),
                    (410.0, 0.0, 420.0, 297.0), (0.0, 225.0, 95.0, 297.0)]   # + угловые графы слева-сверху
# лист 2 (общая СК чертежа, лист 2 начинается с x = 420): поля рамки, штамп формы 2а
SHEET2_X = 420.0
FORBIDDEN_SHEET2 = [(SHEET2_X, 0.0, SHEET2_X + 26.0, 297.0), (SHEET2_X + 408.0, 0.0, SHEET2_X + 420.0, 297.0),
                    (SHEET2_X, 272.0, SHEET2_X + 420.0, 297.0), (SHEET2_X + 226.0, 0.0, SHEET2_X + 420.0, 34.0),
                    (SHEET2_X, 225.0, SHEET2_X + 95.0, 297.0)]
# поле чертежа, где блок полки (текст + рамка) должен лежать ЦЕЛИКОМ (внутри рамки листа с запасом)
ALLOWED_SHEET1 = (28.0, 12.0, 408.0, 270.0)
ALLOWED_SHEET2 = (SHEET2_X + 28.0, 12.0, SHEET2_X + 408.0, 270.0)
A_POS_ROW = 3                    # ряд лесенки размера «узел от кромки» (279 эталона): под зоной — место полкам
EXT_MARGIN = 2.0                 # мм листа: блок между выносными линиями — не ближе этого к ним
DIAGONALS = (45, 135, 225, 315)


def _num(v):
    return f"{v:g}".replace(".", ",")


def _crossing_count(view, p1, p2, side, holes, clear=30.0):
    """Число отверстий, через которые (или вплотную к которым) пройдут выносные линии размера
    между p1 и p2 на стороне side (holes — (x, y, d)); отверстия у самих точек не считаются."""
    x0, y0, x1, y1 = view.rect
    line = {"top": y1, "bottom": y0, "left": x0, "right": x1}[side]
    n = 0
    for p in (p1, p2):
        q = (p[0], line) if side in ("top", "bottom") else (line, p[1])
        for hx, hy, d in holes:
            if math.hypot(hx - p1[0], hy - p1[1]) <= d / 2 + 1 or math.hypot(hx - p2[0], hy - p2[1]) <= d / 2 + 1:
                continue
            if _seg_point_dist(p, q, (hx, hy)) <= d / 2 + clear:
                n += 1
    return n


def _seg_point_dist(a, b, p):
    """Расстояние от точки p до отрезка ab."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])


def _needs_round(v):
    return abs(round(v, 1) - v) > 1e-6


def _scale_fit(w_model, h_model, w_max, h_max, rules):
    """Наибольший масштаб ряда ГОСТ 2.302, при котором w×h модели помещаются в w_max×h_max листа."""
    best = None
    for s in rules["gost_2302_scales"]:
        a, b = s.split(":")
        val = float(a.replace(",", ".")) / float(b.replace(",", "."))
        if w_model * val <= w_max and h_model * val <= h_max and (best is None or val > best[1]):
            best = (s, val)
    if best is None:
        raise ValueError(f"деталь {w_model:g}×{h_model:g} не помещается в {w_max:g}×{h_max:g} мм ни в одном масштабе")
    return best


def _places(n):
    """«2 места», «5 мест» (ГОСТ 2.305 — повторяющиеся элементы)."""
    if n <= 1:
        return None
    return f"{n} {'места' if n in (2, 3, 4) else 'мест'}"


def _holes_word(n):
    return f"{n} отв."


TOL_FRAME_H = 10.0               # мм листа: рамка допуска под полкой (Р2 plate_leap)


class _View:
    """Вид шаблона: прямоугольник области (мм модели), масштаб, занятость сторон лесенками,
    линейные размеры (их тексты — препятствия) и поставленные полки Ø-размеров (для разнесения).
    no_sides — стороны, куда полки не выводить (у выносных сверху — заголовок); title_w — ширина
    заголовка, мм листа (полоса запрета сверху/снизу только под ним; None — вся ширина области)."""
    def __init__(self, key, rect, scale, no_sides=(), paper=None, title_w=None):
        self.key, self.rect, self.s = key, rect, scale
        self.sides = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        self.title_w = title_w
        self.allowed = None       # поле чертежа (мм листа), где блок полки должен лежать целиком
        self.lins = []            # (side, span_paper, mid_model, p1, p2, row)
        self.anchors = []         # (x, y[, ширина мм листа]) — левый край текстов полок, буквы зон (мм модели)
        self.legs = []            # ножки поставленных полок: ((x, y) отверстия, (x, y) конца ножки)
        self.no_sides = set(no_sides)
        self.paper = paper        # (vx, vy, rotate): перевод мм модели → мм листа (главный вид)
        self.forbidden = []       # прямоугольники листа, куда полки не ставить (штамп, ТТ, рамка)
        self.holes = []           # все отверстия (x, y, d) — ножка сквозь чужое отверстие штрафуется
        self.zones = []           # прямоугольники зон выносных (мм модели) — их стороны ножке лучше не резать

    def to_paper(self, p):
        if not self.paper:
            return None
        vx, vy, rot = self.paper
        if rot:
            return vx + self.s * p[0], vy + self.s * p[1]
        return vx + self.s * p[1], vy - self.s * p[0]

    def add_lin(self, side, p1, p2, row=0):
        span = max(abs(p2[0] - p1[0]), abs(p2[1] - p1[1])) * self.s
        self.lins.append((side, span, ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2), p1, p2, row))
        self.sides[side] += 1

    def _rows(self):
        """(side, ряд k, mid, p1, p2) для каждого линейного размера: лесенка layout.ladder —
        по возрастанию пролёта, ряд k на 10 + 8·k мм от контура; row размера — не ближе него."""
        out = []
        for side in ("top", "bottom", "left", "right"):
            items = sorted((l for l in self.lins if l[0] == side), key=lambda l: l[1])
            k = 0
            for (_, _, mid, p1, p2, row) in items:
                k = max(k, row)
                out.append((side, (LADDER0 + LADDER_STEP * k) / self.s, mid, p1, p2))
                k += 1
        return out

    def _row_line(self, side, off):
        x0, y0, x1, y1 = self.rect
        return {"top": y1 + off, "bottom": y0 - off, "left": x0 - off, "right": x1 + off}[side]

    def lin_anchors(self):
        """Приблизительные точки текстов линейных размеров (середина пролёта на своём ряду)."""
        out = []
        for side, off, mid, _, _ in self._rows():
            line = self._row_line(side, off)
            out.append((mid[0], line) if side in ("top", "bottom") else (line, mid[1]))
        return out

    def lin_ext_segments(self):
        """Выносные линии линейных размеров: от точек привязки до ряда размера (мм модели)."""
        segs = []
        for side, off, _, p1, p2 in self._rows():
            line = self._row_line(side, off)
            for p in (p1, p2):
                segs.append((p, (p[0], line) if side in ("top", "bottom") else (line, p[1])))
        return segs

    def _dim_lines(self):
        """Размерные линии лесенки (мм модели): отрезок между проекциями p1, p2 на свой ряд."""
        segs = []
        for side, off, _, p1, p2 in self._rows():
            line = self._row_line(side, off)
            if side in ("top", "bottom"):
                segs.append(((p1[0], line), (p2[0], line)))
            else:
                segs.append(((line, p1[1]), (line, p2[1])))
        return segs

    def fits_rows(self, side, lo, hi, inner, outer):
        """Блок полки в полосе lo..hi вдоль стороны side (мм модели), занимающий от inner до
        outer мм листа от контура, не задевает ряды лесенки этой стороны: с каждым рядом,
        чей размер (выносные ±EXT_MARGIN, текст ±10 мм) попадает в полосу, блок либо дальше
        размерной линии на SHELF_GAP, либо ближе неё на SHELF_GAP и целиком МЕЖДУ выносными
        линиями (эталон: «6 отв.Ø11» между зоной А и размером 279). Где размеров нет —
        полка выходит сразу за контур («4 отв.Ø100» у кромки)."""
        i = 0 if side in ("top", "bottom") else 1
        m = EXT_MARGIN / self.s
        for sd, off, mid, p1, p2 in self._rows():
            if sd != side:
                continue
            a, b = sorted((p1[i], p2[i]))
            ta, tb = min(a, mid[i] - 10.0 / self.s), max(b, mid[i] + 10.0 / self.s)
            if tb + m < lo or ta - m > hi:
                continue
            line = off * self.s
            if inner >= line + SHELF_GAP:
                continue
            if outer <= line - SHELF_GAP and a + m <= lo and hi <= b - m:
                continue
            return False
        return True

    def _parallel_near(self, p1, p2, q1, q2):
        """Ножка p1→p2 почти параллельна (≤20°) и рядом (середины ≤30 мм листа) с q1→q2 —
        две такие ножки читаются как «пучок» (выносной А первого прогона)."""
        a = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        b = math.degrees(math.atan2(q2[1] - q1[1], q2[0] - q1[0]))
        if abs((a - b + 180.0) % 360.0 - 180.0) > 30.0:
            return False
        m1 = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        m2 = ((q1[0] + q2[0]) / 2, (q1[1] + q2[1]) / 2)
        return math.hypot(m1[0] - m2[0], m1[1] - m2[1]) * self.s <= 35.0

    def side_for(self, p1, p2, candidates, holes):
        """Сторона линейного размера, чьи выносные линии проходят через меньше отверстий
        (первый кандидат — предпочтительный при равенстве)."""
        best = None
        for side in candidates:
            n = _crossing_count(self, p1, p2, side, holes)
            if best is None or n < best[0]:
                best = (n, side)
        return best[1]

    def exit_dist(self, p, dx, dy):
        """Путь из точки p по направлению (dx, dy) до границы области (мм модели)."""
        x0, y0, x1, y1 = self.rect
        t = float("inf")
        if dx > 1e-9:
            t = min(t, (x1 - p[0]) / dx)
        elif dx < -1e-9:
            t = min(t, (x0 - p[0]) / dx)
        if dy > 1e-9:
            t = min(t, (y1 - p[1]) / dy)
        elif dy < -1e-9:
            t = min(t, (y0 - p[1]) / dy)
        return max(t, 0.0)

    def side_of(self, p, dx, dy):
        x0, y0, x1, y1 = self.rect
        tx = (x1 - p[0]) / dx if dx > 1e-9 else (x0 - p[0]) / dx if dx < -1e-9 else float("inf")
        ty = (y1 - p[1]) / dy if dy > 1e-9 else (y0 - p[1]) / dy if dy < -1e-9 else float("inf")
        if tx <= ty:
            return "right" if dx > 0 else "left"
        return "top" if dy > 0 else "bottom"

    def _rects(self):
        """Препятствия в мм листа (относительно начала вида — общий масштаб): прямоугольники
        текстов полок с рамками, текстов линейных размеров, букв зон."""
        out = []
        for a in self.anchors:
            x, y, w = (a[0] * self.s, a[1] * self.s, a[2] if len(a) > 2 else 12.0)
            out.append((x - 2.0, y - TOL_FRAME_H - 1.0, x + w + 2.0, y + 8.0))
        for x, y in self.lin_anchors():
            out.append((x * self.s - 10.0, y * self.s - 4.0, x * self.s + 10.0, y * self.s + 4.0))
        return out

    @staticmethod
    def _clearance(r, rects):
        best = 1e9
        for q in rects:
            gx = max(q[0] - r[2], r[0] - q[2], 0.0)
            gy = max(q[1] - r[3], r[1] - q[3], 0.0)
            if gx == 0.0 and gy == 0.0:
                return -1.0
            best = min(best, math.hypot(gx, gy))
        return best

    @staticmethod
    def _cross(p1, p2, q1, q2):
        def d(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (d(p1, p2, q1) * d(p1, p2, q2) < 0) and (d(q1, q2, p1) * d(q1, q2, p2) < 0)

    def callout_plan(self, holes, r, prefer=None, with_tol=True, label=""):
        """Лучшие (отверстие, угол, ножка мм листа) для полки Ø-размера — схема инженера: ножка
        короткая, из того отверстия группы, откуда до свободного края ближе; блок полки (текст +
        рамка допуска) — за контуром и за рядами лесенки ТОЛЬКО в своей полосе (depth_at), с зазором
        SHELF_GAP. Перебор: отверстия × CALLOUT_ANGLES × сетка длин (первые три допустимые).
        Оценка: зазор до текстов/полок/букв (до 60), бонус prefer (±15°), штраф длины сверх 35 мм,
        штрафы пересечений — ножки (1), размерные линии (1), выносные линии (0,8), стороны зон (0,5),
        чужие отверстия (1,5), почти параллельная соседняя ножка (пучок). Стороны no_sides и
        прямоугольники forbidden (лист) не используются."""
        rects = self._rects()
        ext = self.lin_ext_segments()
        dls = self._dim_lines()
        x0, y0, x1, y1 = self.rect
        zone_edges = [e for zx0, zy0, zx1, zy1 in self.zones
                      for e in (((zx0, zy0), (zx1, zy0)), ((zx1, zy0), (zx1, zy1)),
                                ((zx1, zy1), (zx0, zy1)), ((zx0, zy1), (zx0, zy0)))]
        inside = [hh for hh in self.holes if x0 <= hh[0] <= x1 and y0 <= hh[1] <= y1]
        w = max(len(label), 1) * 3.1 + 2.0
        fh = TOL_FRAME_H + 1.0 if with_tol else 1.0        # рамка допуска под полкой
        best = None
        for h in holes:
            for a in CALLOUT_ANGLES:
                dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
                side = self.side_of(h, dx, dy)
                tried, leg = 0, LEG_MIN
                while leg <= LEG_MAX and tried < 3:
                    cur, leg = leg, leg + LEG_STEP
                    ex, ey = h[0] + dx * (r + cur / self.s), h[1] + dy * (r + cur / self.s)   # конец ножки
                    tx = ex if dx > 0 else ex - w / self.s                                   # левый край текста
                    ty = ey
                    bx0, bx1 = tx - 2.0 / self.s, tx + (w + 2.0) / self.s                    # блок (мм модели)
                    by0, by1 = ty - fh / self.s, ty + 8.0 / self.s
                    if side == "top":
                        inner, outer, lo, hi = (by0 - y1) * self.s, (by1 - y1) * self.s, bx0, bx1
                    elif side == "bottom":
                        inner, outer, lo, hi = (y0 - by1) * self.s, (y0 - by0) * self.s, bx0, bx1
                    elif side == "right":
                        inner, outer, lo, hi = (bx0 - x1) * self.s, (bx1 - x1) * self.s, by0, by1
                    else:
                        inner, outer, lo, hi = (x0 - bx1) * self.s, (x0 - bx0) * self.s, by0, by1
                    if inner < SHELF_GAP or not self.fits_rows(side, lo, hi, inner, outer):
                        continue
                    # no_sides: полоса заголовка «k места А» над зоной — шириной title_w (None —
                    # вся область); блок в углу вне полосы заголовку не мешает (эталон: Ø85 у зоны А)
                    if self.title_w is None:           # широкий заголовок: вся полоса над/под областью
                        over_x = True
                    else:
                        hw, cxm = self.title_w / 2 / self.s + 2.0 / self.s, (x0 + x1) / 2
                        over_x = bx1 > cxm - hw and bx0 < cxm + hw
                    over_y = by1 > y0 and by0 < y1
                    if ("top" in self.no_sides and by1 > y1 and over_x) \
                            or ("bottom" in self.no_sides and by0 < y0 and over_x) \
                            or ("left" in self.no_sides and bx0 < x0 and over_y) \
                            or ("right" in self.no_sides and bx1 > x1 and over_y):
                        continue
                    tried += 1
                    if self.paper:                     # блок целиком в поле листа и вне запретных зон
                        cs = [self.to_paper(p) for p in ((bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1))]
                        px0, px1 = min(c[0] for c in cs), max(c[0] for c in cs)
                        py0, py1 = min(c[1] for c in cs), max(c[1] for c in cs)
                        if self.allowed and not (self.allowed[0] <= px0 and px1 <= self.allowed[2]
                                                 and self.allowed[1] <= py0 and py1 <= self.allowed[3]):
                            continue
                        if any(px1 > fx0 and px0 < fx1 and py1 > fy0 and py0 < fy1
                               for fx0, fy0, fx1, fy1 in self.forbidden):
                            continue
                    rect = (bx0 * self.s, by0 * self.s, bx1 * self.s, by1 * self.s)
                    clear = self._clearance(rect, rects)
                    end = (ex, ey)
                    cr = sum(1 for l1, l2 in self.legs if self._cross(h, end, l1, l2))
                    cr += sum(1 for l1, l2 in dls if self._cross(h, end, l1, l2))
                    cr += 0.8 * sum(1 for l1, l2 in ext if self._cross(h, end, l1, l2))
                    cr += 0.5 * sum(1 for l1, l2 in zone_edges if self._cross(h, end, l1, l2))
                    cr += 1.5 * sum(1 for hx, hy, d in inside
                                    if math.hypot(hx - h[0], hy - h[1]) > 0.5
                                    and _seg_point_dist(h, end, (hx, hy)) <= d / 2 + 2.0 / self.s)
                    par = sum(1 for l1, l2 in self.legs if self._parallel_near(h, end, l1, l2))
                    near_pref = prefer is not None and min(abs(a - prefer), 360 - abs(a - prefer)) <= 15
                    score = (min(clear, 60.0) if clear >= 0 else -120.0) + (8.0 if near_pref else 0.0) \
                        - 1.0 * max(0.0, cur - 35.0) - 35.0 * cr - 20.0 * par
                    if best is None or score > best[0]:
                        best = (score, h, a, round(cur, 1), (tx, ty, w), end)
        if best is None:
            raise ValueError(f"вид {self.key}: нет стороны для полки Ø-размера")
        _, h, a, leg, anchor, end = best
        self.anchors.append(anchor)
        self.legs.append((h, end))
        return h, a, leg


class _Spec:
    def __init__(self, top):
        self.top = top
        self.dims, self.ids = [], set()
        self.views = {}

    def view(self, key, rect, scale, no_sides=(), paper=None, title_w=None):
        self.views[key] = _View(key, rect, scale, no_sides, paper, title_w)
        return self.views[key]

    def uid(self, base):
        k, i = base, 1
        while k in self.ids:
            i += 1
            k = f"{base}_{i}"
        self.ids.add(k)
        return k

    def lin(self, view, p1, p2, side, base_id, frame=False, square=False, text=None, prefix=None, row=0):
        d = {"view": view, "id": self.uid(base_id), "kind": "lin", "side": side,
             "ref": {"type": "points", "p1": [p1[0], p1[1], self.top], "p2": [p2[0], p2[1], self.top]}}
        if row:
            d["row"] = row
        if frame:
            d["frame"] = True
        if square:
            d["square"] = True
        if text is not None:
            d["text"] = text
        else:
            span = max(abs(p2[0] - p1[0]), abs(p2[1] - p1[1]))
            if _needs_round(span):
                d["round"] = True
        if prefix:
            d["prefix"] = prefix
        self.views[view].add_lin(side, p1, p2, row)
        self.dims.append(d)
        return d

    def dia(self, view, center, dval, angle, base_id, prefix=None, tol=None, leg=None):
        d = {"view": view, "id": self.uid(base_id), "kind": "dia", "angle": angle,
             "ref": {"type": "circle", "center": [center[0], center[1], self.top], "d": dval}}
        if prefix:
            d["prefix"] = prefix
        if tol:
            d["tol"] = {"value": tol}
        if leg:
            d["leg"] = leg
        self.dims.append(d)
        return d


def plate_spec(summary, cdw_path, model_path, stamp=None, tt=None, ra=None, rules=None):
    rules = rules or drawing_rules.load_rules()
    pat = model_summary.patterns(summary)
    L, W, T = pat["L"], pat["W"], pat["thickness"]
    if pat["axis"] != "z":
        raise ValueError(f"ось толщины {pat['axis']}, шаблон «плита» умеет только Z (вид #Сверху)")
    if T > THIN * min(L, W):
        raise ValueError(f"толщина {T:g} больше {THIN:g}·min(L, W) — не плита")
    if not pat["groups"]:
        raise ValueError("нет групп отверстий вдоль толщины — шаблону «плита» нечего образмеривать")
    bb = summary["bbox"]
    x0, y0, x1, y1 = bb[0], bb[1], bb[3], bb[4]
    cx, cy = pat["center"]
    top = pat["top"]
    S = _Spec(top)

    # ---------------------------------------------------------------- главный вид
    rotate = 90 if L >= W else 0
    pw, ph = (L, W) if rotate else (W, L)       # без поворота #Сверху: бумага X = +Y модели, Y = −X
    scale_txt, s = _scale_fit(pw, ph, MAIN_MAX[0], MAIN_MAX[1], rules)
    if rotate:
        vx, vy = MAIN_CENTER[0] - cx * s, MAIN_CENTER[1] - cy * s
    else:
        vx, vy = MAIN_CENTER[0] - cy * s, MAIN_CENTER[1] + cx * s
    main = {"key": "main", "orient": "#Сверху", "x": round(vx, 2), "y": round(vy, 2), "axes": ["h", "v"]}
    if rotate:
        main["rotate"] = rotate
    views = [main]
    vm = S.view("main", (x0, y0, x1, y1), s, paper=(vx, vy, rotate))
    vm.forbidden = list(FORBIDDEN_SHEET1)
    vm.allowed = ALLOWED_SHEET1
    all_holes = [(h[0], h[1], g["d"]) for g in pat["groups"] for h in g["holes"]]
    vm.holes = all_holes

    def side_for(view, p1, p2, cands):
        return S.views[view].side_for(p1, p2, cands, all_holes)

    S.lin("main", (x0, y1), (x1, y1), "top", "L")
    S.lin("main", (x0, y0), (x0, y1), "left", "W")
    # толщина: точка на плите у левого верхнего угла, полка — слева-сверху ВНЕ плиты, левее
    # рядов лесенки левой стороны (в spec явная shelf; шаблон знает её как препятствие)
    s_shelf = (x0 - 48.0 / s, y1 + 14.0 / s)
    leaders = [{"view": "main", "at": [x0 + 0.06 * L, y1 - 0.06 * W, top], "text": f"s{_num(T)}",
                "arrow": "dot", "shelf": [round(s_shelf[0], 3), round(s_shelf[1], 3)]}]
    vm.anchors.append((s_shelf[0], s_shelf[1], 12.0))
    vm.legs.append(((x0 + 0.06 * L, y1 - 0.06 * W), s_shelf))     # линия-выноска «s» — препятствие ножкам

    # ---------------------------------------------------------------- распределение групп
    def in_zone_b(g):
        return all(abs(h[0] - cx) <= ZONE_B_FRAC[0] * L and abs(h[1] - cy) <= ZONE_B_FRAC[1] * W
                   for h in g["holes"])
    pcds = [g for g in pat["groups"] if g["kind"] == "pcd"]
    pcd_centers = [c for g in pcds for c in g["centers"]]

    def concentric(g):
        return any(math.hypot(h[0] - c[0], h[1] - c[1]) <= 0.5 for h in g["holes"] for c in pcd_centers)
    to_a = [g for g in pat["groups"] if g["kind"] != "pcd" and pcd_centers and concentric(g)]
    rest = [g for g in pat["groups"] if g["kind"] != "pcd" and g not in to_a]
    to_b = [g for g in rest if in_zone_b(g)]
    if len(to_b) < 2:
        to_b = []
    to_main = [g for g in rest if g not in to_b]

    def outward(hole, origin):
        a = math.degrees(math.atan2(hole[1] - origin[1], hole[0] - origin[0])) % 360.0
        if math.hypot(hole[0] - origin[0], hole[1] - origin[1]) <= 0.5:
            return None
        return min(DIAGONALS, key=lambda q: min(abs(a - q), 360 - abs(a - q)))

    def callout(view, g, holes, per_element=None, origin=(cx, cy)):
        """Полка «N отв.Ø…» с допуском: отверстие и направление — по свободному месту."""
        n = per_element if per_element is not None else g["n"] * g["places"]
        pref = outward(holes[0], origin) if len(holes) == 1 else None
        label = (_holes_word(n) if n > 1 else "") + "Ø" + _num(g["d"])
        h, a, leg = S.views[view].callout_plan(holes, g["d"] / 2, prefer=pref, label=label)
        S.dia(view, h, g["d"], a, f"D{_num(g['d'])}", prefix=_holes_word(n) if n > 1 else None,
              tol=drawing_rules.pos_tol_for_d(g["d"], rules), leg=leg)

    def group_dims(view, g):
        """Координаты узора от баз (рамки) + выноска группы с допуском."""
        k, holes = g["kind"], g["holes"]
        if k == "single" and g["places"] == 1:
            hx, hy = holes[0]
            if not (abs(hx - cx) <= 0.5 and abs(hy - cy) <= 0.5):
                ex = x1 if hx >= cx else x0
                ey = y1 if hy >= cy else y0
                S.lin(view, (hx, hy), (ex, hy), side_for(view, (hx, hy), (ex, hy), ("top", "bottom") if hy >= cy else ("bottom", "top")),
                      f"X{_num(g['d'])}", frame=True)
                S.lin(view, (hx, hy), (hx, ey), side_for(view, (hx, hy), (hx, ey), ("right", "left") if hx >= cx else ("left", "right")),
                      f"Y{_num(g['d'])}", frame=True)
        elif k == "pair":
            c = g["center"]
            # половина пары — та, чьи выносные линии (к выбранной стороне) проходят мимо отверстий
            best = None
            for h in holes:
                if g["axis"] == "u":
                    p1, p2, cands = (c[0], h[1]), (h[0], h[1]), ("bottom", "top")
                else:
                    p1, p2, cands = (h[0], c[1]), (h[0], h[1]), ("right", "left")
                for sd in cands:
                    n = _crossing_count(S.views[view], p1, p2, sd, all_holes)
                    if best is None or n < best[0]:
                        best = (n, sd, p1, p2)
            _, sd, p1, p2 = best
            S.lin(view, p1, p2, sd, f"P{_num(g['d'])}", frame=True)
        elif k in ("rect", "square"):
            c = g["center"]
            h = max(holes, key=lambda p: p[0] + p[1])
            if k == "square":
                # □ — сверху, под габаритом (эталон: 2960 и □1127 сверху, низ плиты — выноскам);
                # низ — только если выносные линии верхней пары режут отверстия
                up = [p for p in holes if p[1] > c[1]]
                lo = [p for p in holes if p[1] < c[1]]
                pu = (min(up, key=lambda p: p[0]), max(up, key=lambda p: p[0]))
                pl = (min(lo, key=lambda p: p[0]), max(lo, key=lambda p: p[0]))
                vv = S.views[view]
                if _crossing_count(vv, pu[0], pu[1], "top", all_holes) <= _crossing_count(vv, pl[0], pl[1], "bottom", all_holes):
                    p1, p2, sd = pu[0], pu[1], "top"
                else:
                    p1, p2, sd = pl[0], pl[1], "bottom"
                S.lin(view, p1, p2, sd, f"SQ{_num(g['d'])}", frame=True, square=True,
                      text=_num(round(g["side"], 1)))
            else:
                S.lin(view, (c[0], h[1]), (h[0], h[1]), side_for(view, (c[0], h[1]), (h[0], h[1]), ("bottom", "top")),
                      f"A{_num(g['d'])}", frame=True)
                S.lin(view, (h[0], c[1]), (h[0], h[1]), side_for(view, (h[0], c[1]), (h[0], h[1]), ("right", "left")),
                      f"B{_num(g['d'])}", frame=True)
        elif k == "row":
            first = min(holes, key=lambda p: p[0] if g["axis"] == "u" else p[1])
            if g["axis"] == "u":
                nxt = sorted(holes, key=lambda p: p[0])[1]
                S.lin(view, (x0, first[1]), first, "bottom", f"R0{_num(g['d'])}", frame=True)
                S.lin(view, first, nxt, "bottom", f"R{_num(g['d'])}", frame=True, prefix=f"{g['n'] - 1}х")
                S.lin(view, (first[0], y0), first, "left", f"RY{_num(g['d'])}", frame=True)
            else:
                nxt = sorted(holes, key=lambda p: p[1])[1]
                S.lin(view, (first[0], y0), first, "left", f"R0{_num(g['d'])}", frame=True)
                S.lin(view, first, nxt, "left", f"R{_num(g['d'])}", frame=True, prefix=f"{g['n'] - 1}х")
                S.lin(view, (x0, first[1]), first, "bottom", f"RX{_num(g['d'])}", frame=True)
        else:                     # single с places>1 или cluster: каждое (до двух) от кромок
            for hx, hy in holes[:2]:
                ex = x1 if hx >= cx else x0
                ey = y1 if hy >= cy else y0
                S.lin(view, (hx, hy), (ex, hy), "top" if hy >= cy else "bottom", f"X{_num(g['d'])}", frame=True)
                S.lin(view, (hx, hy), (hx, ey), "right" if hx >= cx else "left", f"Y{_num(g['d'])}", frame=True)

    # вырезы у кромок (один из симметричных): от кромки до дальней стенки, ширина, глубина
    for nt in pat["notches"][:1]:
        along_u = nt["edge"].endswith("u")
        lo_w, hi_w = min(nt["from"], nt["to"]), max(nt["from"], nt["to"])
        if along_u:
            ex = x1 if nt["edge"].startswith("+") else x0
            edge_v = y1 if (lo_w + hi_w) / 2 >= cy else y0
            far = lo_w if edge_v == y1 else hi_w
            side = "right" if ex == x1 else "left"
            S.lin("main", (ex, edge_v), (ex, far), side, "NOTCH_POS", frame=True)
            S.lin("main", (ex, lo_w), (ex, hi_w), side, "NOTCH_W", frame=True)
            inner = ex - nt["depth"] if ex == x1 else ex + nt["depth"]
            S.lin("main", (inner, hi_w), (ex, hi_w), "top" if hi_w >= cy else "bottom", "NOTCH_D")
        else:
            ey = y1 if nt["edge"].startswith("+") else y0
            edge_u = x1 if (lo_w + hi_w) / 2 >= cx else x0
            far = lo_w if edge_u == x1 else hi_w
            side = "top" if ey == y1 else "bottom"
            S.lin("main", (edge_u, ey), (far, ey), side, "NOTCH_POS", frame=True)
            S.lin("main", (lo_w, ey), (hi_w, ey), side, "NOTCH_W", frame=True)
            inner = ey - nt["depth"] if ey == y1 else ey + nt["depth"]
            S.lin("main", (hi_w, inner), (hi_w, ey), "right" if hi_w >= cx else "left", "NOTCH_D")

    # линейные размеры групп главного вида — до выносок (они учитывают занятость сторон)
    for g in to_main:
        group_dims("main", g)

    # ---------------------------------------------------------------- выносной А: узел PCD
    if pcds:
        g = pcds[0]
        c = max(g["centers"], key=lambda p: p[0])
        r = g["pcd_d"] / 2
        near_edge = x1 if (x1 - c[0]) < (c[0] - x0) else x0
        gap = abs(near_edge - c[0])
        edge_in = gap < EDGE_IN_DETAIL * g["pcd_d"]
        if edge_in:                            # узел у кромки — кромка в зоне (эталон: 279 от кромки)
            lo, hi = (c[0] - r - DETAIL_A_MARGIN, near_edge + 30.0) if near_edge == x1 \
                else (near_edge - 30.0, c[0] + r + DETAIL_A_MARGIN)
        else:
            lo, hi = c[0] - r - DETAIL_A_MARGIN, c[0] + r + DETAIL_A_MARGIN
        w, h = hi - lo, 2 * (r + DETAIL_A_MARGIN)
        sA_txt, sA = _scale_fit(w, h, DETAIL_MAX, DETAIL_MAX, rules)
        rectA = (lo, c[1] - h / 2, hi, c[1] + h / 2)
        # бумага выносного: центр зоны — в (x, y) листа 2; СК вида = СК родителя (модель)
        paperA = (SHEET2["А"][0] + SHEET2_X - (lo + hi) / 2 * sA, SHEET2["А"][1] - c[1] * sA, 1)
        vA = S.view("А", rectA, sA, no_sides=("top",), paper=paperA)    # сверху — заголовок «k места А»
        vA.forbidden = list(FORBIDDEN_SHEET2)
        vA.allowed = ALLOWED_SHEET2
        vA.holes = all_holes
        shelf_a = (round(lo + 0.12 * w, 3), round(c[1] + h / 2 + 12.0 / s, 3))   # буква над зоной, слева
        S.views["main"].anchors.append((shelf_a[0], shelf_a[1], 10.0))           # буква А на главном виде
        S.views["main"].zones.append(rectA)
        S.views["main"].legs.append(((lo, c[1] + h / 2), shelf_a))               # линия-выноска буквы
        views.append({"key": "А", "type": "detail", "from": "main", "center": [round((lo + hi) / 2, 3), c[1]],
                      "form": "rect", "w": round(w, 3), "h": round(h, 3), "scale": sA_txt, "shelf": list(shelf_a),
                      "x": SHEET2["А"][0], "y": SHEET2["А"][1], "sheet": 2,
                      **({"title_prefix": _places(g["places"])} if _places(g["places"]) else {})})
        if edge_in:                            # дальний ряд: под зоной остаётся место полкам (эталон: 279)
            S.lin("А", (near_edge, c[1]), (c[0], c[1]), "bottom", "A_POS", frame=True, row=A_POS_ROW)
        angs = g["angles"]
        on_circle = sorted(zip(angs, [(c[0] + r * math.cos(math.radians(a)), c[1] + r * math.sin(math.radians(a)))
                                      for a in angs]))
        a1, h1 = on_circle[0]
        a2, h2 = on_circle[1] if len(on_circle) > 1 else on_circle[0]
        rays = [[c[0] + r, c[1], top], [h1[0], h1[1], top], [h2[0], h2[1], top]]
        # углы занимают сектор от оси до второго отверстия — полки Ø уводим в другие стороны
        vA.anchors.append((c[0] + r * 1.6 * math.cos(math.radians(a2 / 2)),
                           c[1] + r * 1.6 * math.sin(math.radians(a2 / 2)), 16.0))
        hp, ap, legp = vA.callout_plan([c], r, prefer=135, label="Ø" + _num(g["pcd_d"]))
        S.dims.append({"view": "А", "id": S.uid("PCD"), "kind": "dia", "angle": ap, "leg": legp,
                       "ref": {"type": "pcd", "center": [c[0], c[1], top], "d": g["pcd_d"], "rays": rays},
                       "tol": {"value": drawing_rules.pos_tol_for_d(g["pcd_d"], rules)}})
        if a1 > 0.5:
            S.dims.append({"view": "А", "id": S.uid("ANG1"), "kind": "ang", "frame": True, "radius": 20,
                           "ref": {"type": "rays", "pcd": "PCD", "i1": 0, "i2": 1}})
        if len(on_circle) > 1 and abs(a2 - a1) > 0.5:
            S.dims.append({"view": "А", "id": S.uid("ANG2"), "kind": "ang", "frame": True, "radius": 33,
                           "ref": {"type": "rays", "pcd": "PCD", "i1": 1, "i2": 2}})
        hole_pts = [p for _, p in on_circle]
        hh, ah, legh = vA.callout_plan(hole_pts, g["d"] / 2, prefer=315,
                                       label=_holes_word(g["n"]) + "Ø" + _num(g["d"]))
        S.dia("А", hh, g["d"], ah, f"D{_num(g['d'])}", prefix=_holes_word(g["n"]),
              tol=drawing_rules.pos_tol_for_d(g["d"], rules), leg=legh)
        for gg in to_a:
            hole = min(gg["holes"], key=lambda p: math.hypot(p[0] - c[0], p[1] - c[1]))
            hc, ac, legc = vA.callout_plan([hole], gg["d"] / 2, prefer=225, label="Ø" + _num(gg["d"]))
            S.dia("А", hc, gg["d"], ac, f"D{_num(gg['d'])}", prefix=None,
                  tol=drawing_rules.pos_tol_for_d(gg["d"], rules), leg=legc)

    # ---------------------------------------------------------------- выносной Б: центральная зона
    if to_b:
        hx = max(abs(h[0] - cx) + g["d"] / 2 for g in to_b for h in g["holes"])
        hy = max(abs(h[1] - cy) + g["d"] / 2 for g in to_b for h in g["holes"])
        w, h = 2 * (hx + ZONE_MARGIN), 2 * (hy + ZONE_MARGIN)
        vB = S.view("Б", (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), s, no_sides=("top",),
                    paper=(SHEET2["Б"][0] + SHEET2_X - cx * s, SHEET2["Б"][1] - cy * s, 1), title_w=14.0)
        vB.forbidden = list(FORBIDDEN_SHEET2)
        vB.allowed = ALLOWED_SHEET2
        vB.holes = all_holes
        vB.anchors.append((cx, cy + h / 2 + 12.0 / s))          # заголовок «Б» над зоной
        # буква Б над зоной — где над верхней кромкой зоны нет отверстий (не на Ø150)
        ytxt = cy + h / 2 + 12.0 / s
        cand = [cx + f * w for f in (-0.35, -0.2, 0.2, 0.35, -0.45, 0.45)]
        xb = max(cand, key=lambda xx: min((math.hypot(xx - hx, ytxt - hy) - d / 2 for hx, hy, d in all_holes),
                                          default=1e9))
        shelf_b = (round(xb, 3), round(ytxt, 3))
        views.append({"key": "Б", "type": "detail", "from": "main", "center": [cx, cy], "form": "rect",
                      "w": round(w, 3), "h": round(h, 3), "scale": scale_txt, "shelf": list(shelf_b),
                      "x": SHEET2["Б"][0], "y": SHEET2["Б"][1], "sheet": 2})
        S.views["main"].anchors.append((shelf_b[0], shelf_b[1], 10.0))
        S.views["main"].zones.append(vB.rect)
        S.views["main"].legs.append(((shelf_b[0], cy + h / 2), shelf_b))
        for g in sorted(to_b, key=lambda g: -g["d"]):
            group_dims("Б", g)
        for g in sorted(to_b, key=lambda g: -g["d"]):
            callout("Б", g, g["holes"])

    # ---------------------------------------------------------------- выносной В: пазы у угла
    slots = pat["slots"]
    if slots:
        corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        all_holes = [h for g in pat["groups"] for h in g["holes"]]

        def crowd(cn):
            return sum(1 for h in all_holes if abs(h[0] - cn[0]) <= 0.25 * L and abs(h[1] - cn[1]) <= 0.25 * W)
        corner = min(corners, key=crowd)
        near = [sl for sl in slots if abs(sl["center"][0] - corner[0]) <= 0.15 * L
                and abs(sl["center"][1] - corner[1]) <= 0.15 * W]
        if near:
            xs, ys = [corner[0]], [corner[1]]
            for sl in near:
                ex = sl["flat"] / 2 + sl["w"] / 2 if sl["along"] == "u" else sl["w"] / 2
                ey = sl["flat"] / 2 + sl["w"] / 2 if sl["along"] == "v" else sl["w"] / 2
                xs += [sl["center"][0] - ex, sl["center"][0] + ex]
                ys += [sl["center"][1] - ey, sl["center"][1] + ey]
            sx_dir, sy_dir = (1 if corner[0] == x1 else -1), (1 if corner[1] == y1 else -1)
            inner_x = min(xs) if sx_dir > 0 else max(xs)
            inner_y = min(ys) if sy_dir > 0 else max(ys)
            lo_x, hi_x = sorted((inner_x - sx_dir * 25.0, corner[0] + sx_dir * 15.0))
            lo_y, hi_y = sorted((inner_y - sy_dir * 25.0, corner[1] + sy_dir * 15.0))
            w, h = hi_x - lo_x, hi_y - lo_y
            sV_txt, sV = _scale_fit(w, h, DETAIL_V_MAX, DETAIL_V_MAX, rules)
            S.view("В", (lo_x, lo_y, hi_x, hi_y), sV, no_sides=("top",),
                   paper=(SHEET2["В"][0] + SHEET2_X - (lo_x + hi_x) / 2 * sV, SHEET2["В"][1] - (lo_y + hi_y) / 2 * sV, 1))
            S.views["В"].forbidden = list(FORBIDDEN_SHEET2)
            S.views["В"].allowed = ALLOWED_SHEET2
            # буква В на главном виде — за углом плиты снаружи (полка влево-вниз от угла)
            shelf_v = (round(corner[0] + sx_dir * 34.0 / s, 3), round(corner[1] + sy_dir * 24.0 / s, 3))
            S.views["main"].anchors.append((shelf_v[0], shelf_v[1], 10.0))
            S.views["main"].zones.append((lo_x, lo_y, hi_x, hi_y))
            S.views["main"].legs.append((corner, shelf_v))
            k = len(slots) // len(near)
            views.append({"key": "В", "type": "detail", "from": "main",
                          "center": [round((lo_x + hi_x) / 2, 3), round((lo_y + hi_y) / 2, 3)], "form": "rect",
                          "w": round(w, 3), "h": round(h, 3), "scale": sV_txt,
                          "x": SHEET2["В"][0], "y": SHEET2["В"][1], "sheet": 2, "shelf": list(shelf_v),
                          **({"title_prefix": _places(k)} if _places(k) else {})})
            word = (f"{len(near)} {'паза' if len(near) in (2, 3, 4) else 'пазов'} " if len(near) > 1 else None)
            # размеры — за кромками плиты (стороны угла): горизонтальные — у кромки по v, вертикальные — по u
            h_side = "top" if sy_dir > 0 else "bottom"
            v_side = "right" if sx_dir > 0 else "left"
            first = True
            same_w = all(abs(sl["w"] - near[0]["w"]) <= 0.05 for sl in near)   # одинаковая ширина —
            for sl in near:                                                     # один размер «k паза b» (ГОСТ 2.307)
                sx, sy = sl["center"]
                if sl["along"] == "v":                      # паз вдоль Y
                    if first or not same_w:
                        S.lin("В", (sx - sl["w"] / 2, sy), (sx + sl["w"] / 2, sy), h_side, "SLOT_W",
                              prefix=word if first else None)
                    S.lin("В", (sx, sl["from"]), (sx, sl["to"]), v_side, "SLOT_L")
                    end = sl["to"] if sy_dir > 0 else sl["from"]
                    S.lin("В", (sx, end), (sx, corner[1]), v_side, "SLOT_Y", frame=True)
                    S.lin("В", (sx, sy), (corner[0], sy), h_side, "SLOT_X", frame=True)
                else:                                       # паз вдоль X
                    if first or not same_w:
                        S.lin("В", (sx, sy - sl["w"] / 2), (sx, sy + sl["w"] / 2), v_side, "SLOT_W",
                              prefix=word if first else None)
                    S.lin("В", (sl["from"], sy), (sl["to"], sy), h_side, "SLOT_L")
                    end = sl["to"] if sx_dir > 0 else sl["from"]
                    S.lin("В", (end, sy), (corner[0], sy), h_side, "SLOT_X", frame=True)
                    S.lin("В", (sx, sy), (sx, corner[1]), v_side, "SLOT_Y", frame=True)
                first = False

    # выноски групп главного вида — последними: учитывают лесенки и буквы всех зон
    for g in to_main:
        callout("main", g, g["holes"])

    spec = {"model": model_path, "cdw": cdw_path, "format": 3, "vertical": False, "scale": scale_txt,
            "views": views, "dims": S.dims, "threads": [], "leaders": leaders,
            "tt": list(tt or TT), "ra": ra, "stamp": dict(stamp or {})}
    return spec


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    model, cdw = os.path.abspath(args[0]), os.path.abspath(args[1])
    stamp = None
    if "--stamp" in sys.argv:
        stamp = json.loads(sys.argv[sys.argv.index("--stamp") + 1])
    sys.path.insert(0, os.path.dirname(HERE))
    import server
    raw = getattr(server.kompas_model_summary, "__wrapped__", server.kompas_model_summary)(path=model)
    summary = json.loads(raw)
    spec = plate_spec(summary, cdw.replace("\\", "/"), model.replace("\\", "/"), stamp=stamp)
    out = os.path.splitext(cdw)[0] + ".spec.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    errs = server.validate_spec(spec)
    print("SPEC:", out, "validate_spec:", "OK" if not errs else "; ".join(errs))
    if "--spec-only" not in sys.argv and not errs:
        print(getattr(server.kompas_build_drawing, "__wrapped__", server.kompas_build_drawing)(spec_path=out))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
