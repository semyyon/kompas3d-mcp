# -*- coding: utf-8 -*-
"""Раскладка размеров чертежа «лесенкой» — чистая геометрия, без КОМПАС.

LLM называет, ЧТО образмерить (ссылка на геометрию модели), код считает, ГДЕ
поставить. COM-часть — server.kompas_place_dimensions; здесь только числа.

Формат элемента dims инструмента kompas_place_dimensions (его же потребляет
drawing_spec, задача 9):
    {"id": str,                      # необязательно, по умолчанию "d<номер>"
     "kind": "lin" | "dia" | "rad",
     "ref": <ссылка>,
     "side": "top"|"bottom"|"left"|"right"|null,   # только lin; null — по умолчанию
     "orient": "h"|"v"|null,         # только lin; null — по большей разнице координат
     "angle": float}                 # только dia/rad, град., по умолчанию 45
Ссылки (мм):
    {"type": "points", "p1": [x,y,z], "p2": [x,y,z]}  — точки МОДЕЛИ, проецируются
        на вид (IAssociationView.GetProjectionPoint); только lin;
    {"type": "circle", "center": [x,y,z], "d": float} — окружность МОДЕЛИ: центр
        проецируется, в геометрии вида ищется окружность с |2R − d| ≤ 0,05 и
        центром ближе 0,5 мм (match_circle); нет окружности — так же дуга вида,
        стрелка на дуге (arc_angle); только dia/rad;
    {"type": "view_points", "p1": [x,y], "p2": [x,y]} — точки в координатах ВИДА
        (запасной путь, без проекции); только lin.
Сторона по умолчанию: горизонтальный размер — снизу, вертикальный — слева.
"""
import math

SIDES = {"h": ("bottom", "top"), "v": ("left", "right")}


def _collides(p, pts, gap):
    return any(abs(p[0] - q[0]) < gap and abs(p[1] - q[1]) < gap for q in pts)


def ladder(contour, dims, first_offset=10.0, step=8.0, min_gap=7.0, max_offset=80.0,
           obstacles=()):
    """Вылеты размерных линий лесенкой вне контура.

    contour — (x1, y1, x2, y2) контур вида, мм листа. dims — [{"id", "orient": "h"|"v",
    "a", "b", "side"}], a/b — концы по оси размера (мм листа: x у "h", y у "v").
    На каждой стороне размеры идут по возрастанию длины пролёта: k-й получает
    offset = first_offset + k·step (короткий ближе к контуру). Текст — середина
    пролёта на размерной линии. Если текст ближе min_gap к уже поставленному
    тексту или к obstacles ([x, y] — тексты, что уже есть в виде), размер уходит
    на следующую ступень. offset > max_offset → {"id", "error": "нет места"}.
    Сдвига текста вдоль оси нет: инструмент ставит текст по центру (text_pos=0),
    у горизонтального размера КОМПАС X3 не принимает (Э0) — сдвиг не воплотить.
    Возвращает в порядке входа {"id", "side", "offset", "text_xy": [x, y]}."""
    x1, y1, x2, y2 = contour
    out, placed = {}, [list(p) for p in obstacles]
    groups = {}
    for i, d in enumerate(dims):
        o = d.get("orient")
        side = d.get("side") or (SIDES[o][0] if o in SIDES else None)
        if o not in SIDES or side not in SIDES[o]:
            out[i] = {"id": d.get("id"), "error": f"сторона {side!r} не подходит к orient {o!r}"}
            continue
        groups.setdefault(side, []).append(i)
    for side, idx in groups.items():
        idx.sort(key=lambda i: abs(dims[i]["b"] - dims[i]["a"]))
        k = 0
        for i in idx:
            d = dims[i]
            mid = (d["a"] + d["b"]) / 2.0
            k = max(k, int(d.get("row") or 0))    # "row": ряд не ближе заданного (место под полки выносок)
            while True:
                off = first_offset + k * step
                if off > max_offset + 1e-9:
                    out[i] = {"id": d.get("id"), "error": "нет места"}
                    break
                xy = {"bottom": (mid, y1 - off), "top": (mid, y2 + off),
                      "left": (x1 - off, mid), "right": (x2 + off, mid)}[side]
                k += 1
                if not _collides(xy, placed, min_gap):
                    placed.append(xy)
                    out[i] = {"id": d.get("id"), "side": side, "offset": off,
                              "text_xy": [round(xy[0], 3), round(xy[1], 3)]}
                    break
    return [out[i] for i in range(len(dims))]


def lin_axis(p1, p2, orient=None):
    """Ориентация и пролёт линейного размера по двум точкам листа:
    ("h"|"v", a, b). orient=None — по большей разнице координат."""
    if orient is None:
        orient = "h" if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]) else "v"
    if orient not in SIDES:
        raise ValueError(f"orient {orient!r}: нужно 'h' или 'v'")
    k = 0 if orient == "h" else 1
    return orient, p1[k], p2[k]


def match_circle(circles, xc, yc, d, tol_d=0.05, tol_c=0.5):
    """Окружность вида (xc, yc, R) под ссылку {"center", "d"}: |2R − d| ≤ tol_d,
    центр не дальше tol_c; из подходящих — ближайшая. None — нет такой."""
    best = None
    for c in circles:
        dist = ((c[0] - xc) ** 2 + (c[1] - yc) ** 2) ** 0.5
        if abs(2 * c[2] - d) <= tol_d and dist <= tol_c and (best is None or dist < best[0]):
            best = (dist, c)
    return None if best is None else tuple(best[1])


def arc_angle(arc, angle=None):
    """Угол (град., 0…360) точки НА дуге для стрелки dia/rad (правило: стрелка R —
    на дугу). arc — (xc, yc, R, x1, y1, x2, y2, x3, y3) из D.view_content
    ["arc_list"]: концы и средняя точка дуги. Дуга — путь от одного конца к другому
    через среднюю точку. angle внутри дуги — он и есть; None или вне дуги —
    середина дуги."""
    xc, yc = arc[0], arc[1]
    a1, a2, a3 = (math.degrees(math.atan2(y - yc, x - xc)) % 360.0
                  for x, y in ((arc[3], arc[4]), (arc[5], arc[6]), (arc[7], arc[8])))
    ext = (a2 - a1) % 360.0
    if (a3 - a1) % 360.0 <= ext:          # против часовой a1 → a2 через a3
        start = a1
    else:
        start, ext = a2, (a1 - a2) % 360.0
    if angle is not None and (float(angle) - start) % 360.0 <= ext:
        return float(angle) % 360.0
    return round((start + ext / 2.0) % 360.0, 6)
