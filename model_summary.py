# -*- coding: utf-8 -*-
"""Сводка геометрии детали для планировщика чертежа — чистая часть kompas_model_summary.

На входе — список граней, снятых server.py через API7 (IFace / IMathSurface3D), в СК детали:
  {"t": "cyl", "r": R, "span": угол дуги (рад), "ring0": [p, p, p], "ring1": [p, p, p],
   "pt": точка в середине грани, "nrm": нормаль грани в pt (наружу из материала), "gab": [6]}
     ring0/ring1 — по 3 точки окружности на краях v0/v1 (ось цилиндра — по v);
  {"t": "plane", "gab": [x1,y1,z1,x2,y2,z2], "nrm": [nx,ny,nz], "area": мм²}
  {"t": "cone", "gab": [6]}   {"t": "other", "gab": [6]}
На выходе — dict для JSON: отверстия/бобышки группами, дуги, уровни плоскостей, заметки.
COM здесь нет — всё проверяется синтетическими гранями (tests/test_model_summary.py).
"""
import json
import math
import os

TOL = 0.01                      # мм: совпадение уровней/границ
FULL = 2 * math.pi - 1e-3       # дуга не меньше — полный цилиндр
SMALL_PLANE_AREA = 5000.0       # мм²: плоскость меньше — стенка паза/выреза, отдаём её габарит
SMALL_PLANES_MAX = 60           # предел числа малых плоскостей в ответе
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_rules.json")


def r3(v):
    """Округление до 0,001 мм без «-0.0»."""
    v = round(float(v), 3)
    return 0.0 if v == 0 else v


def _sub(a, b): return [x - y for x, y in zip(a, b)]
def _add(a, b): return [x + y for x, y in zip(a, b)]
def _mul(a, k): return [x * k for x in a]
def _dot(a, b): return sum(x * y for x, y in zip(a, b))
def _cross(a, b): return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
def _norm(a): return math.sqrt(_dot(a, a))


def circle_center(p1, p2, p3):
    """Центр окружности через три точки в пространстве."""
    a, b = _sub(p1, p3), _sub(p2, p3)
    axb = _cross(a, b)
    den = 2 * _dot(axb, axb)
    num = _cross(_sub(_mul(b, _dot(a, a)), _mul(a, _dot(b, b))), axb)
    return _add(p3, _mul(num, 1.0 / den))


def bbox_of(faces):
    """Габарит детали — объединение габаритов граней (у кого он есть)."""
    gs = [f["gab"] for f in faces if f.get("gab")]
    if not gs:
        return None
    return [r3(min(g[i] for g in gs)) for i in range(3)] + [r3(max(g[i + 3] for g in gs)) for i in range(3)]


def _aligned(v):
    """Индекс координатной оси, вдоль которой направлен единичный v, или None."""
    for k in range(3):
        if abs(abs(v[k]) - 1) < 1e-6:
            return k
    return None


def _canon(u):
    """Направление оси без знака: главная компонента положительна."""
    k = max(range(3), key=lambda i: abs(u[i]))
    return _mul(u, -1.0) if u[k] < 0 else u


def _round_axis(u):
    k = _aligned(u)
    if k is not None:
        out = [0.0, 0.0, 0.0]
        out[k] = 1.0 if u[k] > 0 else -1.0
        return out
    return [r3(x) for x in u]


def _cylinder(f):
    """Ось, концы и сторона материала цилиндрической грани."""
    c0, c1 = circle_center(*f["ring0"]), circle_center(*f["ring1"])
    ax = _sub(c1, c0)
    length = _norm(ax)
    u = _mul(ax, 1.0 / length)
    foot = _add(c0, _mul(u, _dot(_sub(f["pt"], c0), u)))
    inner = _dot(f["nrm"], _sub(f["pt"], foot)) < 0     # нормаль к оси — материал снаружи
    return {"c0": c0, "c1": c1, "u": u, "len": length, "d": 2 * f["r"], "inner": inner,
            "full": f.get("span", 2 * math.pi) >= FULL}


def _capped(end, k, r, faces, bbox):
    """Есть ли у конца отверстия «дно»: плоская/коническая грань в пределах круга Ø
    на уровне конца. Конец на габарите детали — всегда открыт."""
    if abs(end[k] - bbox[k]) < TOL or abs(end[k] - bbox[k + 3]) < TOL:
        return False
    i, j = [m for m in range(3) if m != k]
    for f in faces:
        if f["t"] not in ("plane", "cone") or not f.get("gab"):
            continue
        g = f["gab"]
        if not (g[k] - TOL <= end[k] <= g[k + 3] + TOL):
            continue
        if all(g[m] >= end[m] - r - TOL and g[m + 3] <= end[m] + r + TOL for m in (i, j)):
            return True
    return False


def _hole(c, faces, bbox):
    """Отверстие: вход (top), глубина, сквозное/глухое. axis — от дна к входу."""
    k = _aligned(c["u"])
    if k is None:                                    # наклонная ось: дно не ищем
        u = _canon(c["u"])
        top = c["c1"] if _dot(c["c1"], u) >= _dot(c["c0"], u) else c["c0"]
        return {"axis": u, "top": top, "through": None, "entry": None}
    cap0 = _capped(c["c0"], k, c["d"] / 2, faces, bbox)
    cap1 = _capped(c["c1"], k, c["d"] / 2, faces, bbox)
    if cap0 != cap1:                                 # глухое: вход — открытый конец
        top, bot = (c["c1"], c["c0"]) if cap0 else (c["c0"], c["c1"])
        u = _mul(_sub(top, bot), 1.0 / c["len"])
        through = False
    else:
        u = _canon(c["u"])
        top = c["c1"] if _dot(c["c1"], u) >= _dot(c["c0"], u) else c["c0"]
        through = not cap0
    entry = None if (cap0 and cap1) else f"{'xyz'[k]}={r3(top[k])}"
    return {"axis": u, "top": top, "through": through, "entry": entry}


def load_drill_to_thread(path=None):
    """Таблица «диаметр сверления → резьба» из project_rules.json (ключ drill_to_thread)."""
    with open(path or RULES_PATH, encoding="utf-8") as fh:
        return json.load(fh).get("drill_to_thread", {})


def _thread_for(d, table):
    for key, val in (table or {}).items():
        if abs(float(key) - d) < TOL:
            return val
    return None


def _num(v):
    return f"{v:g}".replace(".", ",")


# крупный шаг метрической резьбы, ГОСТ 24705-2004 (d → P)
COARSE_PITCH = {3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2.0, 16: 2.0,
                18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0, 27: 3.0, 30: 3.5, 33: 3.5, 36: 4.0, 39: 4.0,
                42: 4.5, 45: 4.5, 48: 5.0, 52: 5.0, 56: 5.5, 60: 5.5, 64: 6.0, 68: 6.0}


def threads(raw):
    """Условные изображения резьбы модели (ISymbols3DContainer.Threads) → записи сводки.
    raw: [{d, pitch, internal, left, length, gab (x1,y1,z1,x2,y2,z2 грани-основания), std}].
    Обозначение по ГОСТ 8724/24705: «M76×2» (мелкий шаг пишется, крупный — нет), «LH» — левая.
    Поле допуска в модели не хранится: по умолчанию 6g наружная / 6H внутренняя (ГОСТ 16093) —
    помечено tol_default. Ось и участок — по габариту грани-основания: у цилиндра два размера
    габарита ≈ d, ось — третий (самый далёкий от d)."""
    out = []
    for t in raw or []:
        d, p = float(t["d"]), float(t["pitch"])
        coarse = COARSE_PITCH.get(int(round(d)))
        des = f"M{_num(d)}" + ("" if coarse is not None and abs(coarse - p) < 1e-6 else f"×{_num(p)}")
        if t.get("left"):
            des += "LH"
        rec = {"designation": des, "d": r3(d), "pitch": r3(p), "internal": bool(t.get("internal")),
               "length": r3(t.get("length", 0.0)), "tol_default": "6H" if t.get("internal") else "6g",
               "std": t.get("std", "")}
        g = t.get("gab")
        if g:
            ext = [g[3] - g[0], g[4] - g[1], g[5] - g[2]]
            ax = max(range(3), key=lambda k: abs(ext[k] - d))
            if sum(1 for e in ext if abs(e - d) <= 0.02 * d) < 2:
                ax = None                                  # не цилиндр вдоль оси СК
            rec["face_bbox"] = [r3(x) for x in g]
            if ax is not None:
                rec["axis"] = "xyz"[ax]
                rec["span"] = [r3(g[ax]), r3(g[ax + 3])]
        out.append(rec)
    return out


def summarize(faces, max_items=200, drill_to_thread=None, max_levels=12):
    """Сводка: bbox, holes, bosses, arcs, planes, notes (+ truncated). Группы отверстий —
    одинаковые d/ось/глубина/сквозное/вход; centers_top — центры на входе,
    центр дна = centers_top − axis·depth."""
    bbox = bbox_of(faces)
    holes, bosses, arcs = {}, {}, {}
    for f in faces:
        if f["t"] != "cyl":
            continue
        c = _cylinder(f)
        if not c["full"]:
            key = (r3(f["r"]), c["inner"])
            arcs[key] = arcs.get(key, 0) + 1
            continue
        if c["inner"]:
            h = _hole(c, faces, bbox)
            key = (r3(c["d"]), tuple(_round_axis(h["axis"])), r3(c["len"]), h["through"], h["entry"])
            holes.setdefault(key, []).append([r3(x) for x in h["top"]])
        else:
            u = _canon(c["u"])
            top = c["c1"] if _dot(c["c1"], u) >= _dot(c["c0"], u) else c["c0"]
            key = (r3(c["d"]), tuple(_round_axis(u)), r3(c["len"]))
            bosses.setdefault(key, []).append([r3(x) for x in top])

    budget = [max_items, 0, 0]                      # остаток, показано, всего

    def take(pts):
        pts = sorted(pts)
        shown = pts[:max(0, budget[0])]
        budget[0] -= len(shown)
        budget[1] += len(shown)
        budget[2] += len(pts)
        return shown

    out_h = []
    for key in sorted(holes, key=lambda k: (k[0], str(k[4]), k[2], k[1])):
        d, axis, depth, through, entry = key
        out_h.append({"d": d, "n": len(holes[key]), "axis": list(axis), "depth": depth,
                      "through": through, "entry": entry, "centers_top": take(holes[key])})
    out_b = []
    for key in sorted(bosses):
        d, axis, length = key
        out_b.append({"d": d, "n": len(bosses[key]), "axis": list(axis), "length": length,
                      "centers_top": take(bosses[key])})

    levels, other, small = {}, 0, []
    for f in faces:
        if f["t"] != "plane":
            continue
        k = _aligned(f["nrm"])
        if k is None:
            other += 1
            continue
        lv = r3((f["gab"][k] + f["gab"][k + 3]) / 2)
        slot = levels.setdefault("xyz"[k], {}).setdefault(lv, [0, 0.0])
        slot[0] += 1
        slot[1] += f.get("area", 0.0)
        # малые плоскости с габаритом — стенки пазов/вырезов для шаблонов (задача 16)
        if f.get("area", 0.0) <= SMALL_PLANE_AREA and f.get("gab"):
            small.append({"axis": "xyz"[k], "level": lv, "nrm": int(round(f["nrm"][k])),
                          "gab": [r3(x) for x in f["gab"]], "area": round(f.get("area", 0.0), 1)})
    planes, capped = {}, {}
    for ax in sorted(levels):
        items = sorted(levels[ax].items(), key=lambda kv: -kv[1][1])
        if len(items) > max_levels:
            capped[ax] = len(items)
            items = items[:max_levels]
        planes[ax] = sorted([lv, n, round(a, 1)] for lv, (n, a) in items)
    if other:
        planes["other"] = other
    if capped:
        planes["capped"] = capped

    notes = []
    for g in out_h:
        t = _thread_for(g["d"], drill_to_thread)
        if t:
            notes.append(f"Ø{_num(g['d'])} ×{g['n']} ({g['entry'] or 'вход ?'}) → вероятно {t} "
                         "(правило PRJ.02: отверстия без резьбы; сверить по крепежу сборки)")
    res = {"bbox": bbox, "holes": out_h, "bosses": out_b,
           "arcs": [{"r": r, "n": n, "inner": inner} for (r, inner), n in sorted(arcs.items())],
           "planes": planes, "notes": notes}
    if budget[1] < budget[2]:
        res["truncated"] = f"показано {budget[1]} из {budget[2]} центров (max_items={max_items})"
    if small:
        small.sort(key=lambda p: (p["axis"], p["level"], p["gab"]))
        res["small_planes"] = small[:SMALL_PLANES_MAX]
        if len(small) > SMALL_PLANES_MAX:
            res["small_planes_truncated"] = len(small)
    return res


# ------------------------------------------------------------------ паттерны (задача 16)
PAT_TOL = 0.05        # мм: равенство радиусов/координат в узоре
CLUSTER_FRAC = 0.15   # доля max(L, W): центры ближе — один узел (кластер)
SLOT_W_MAX = 30.0     # мм: расстояние между стенками паза


def _clusters(pts, thr):
    """Кластеры точек по связности (расстояние ≤ thr), в порядке первого появления."""
    left = list(range(len(pts)))
    out = []
    while left:
        seed = [left.pop(0)]
        grown = True
        while grown:
            grown = False
            for i in list(left):
                if any(math.hypot(pts[i][0] - pts[s][0], pts[i][1] - pts[s][1]) <= thr for s in seed):
                    seed.append(i)
                    left.remove(i)
                    grown = True
        out.append(sorted(seed))
    return out


def _classify(pts, tol=PAT_TOL):
    """Узор одной группы точек (u, v): single | pair | rect/square | pcd | row | cluster.
    Все координаты — абсолютные; center — центроид узора."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    rel = [(p[0] - cx, p[1] - cy) for p in pts]
    base = {"n": n, "center": [r3(cx), r3(cy)]}
    if n == 1:
        return dict(base, kind="single")
    if n == 2:
        (x1, y1), (x2, y2) = rel
        if abs(y1) <= tol and abs(y2) <= tol:
            return dict(base, kind="pair", axis="u", half=r3(abs(x1)))
        if abs(x1) <= tol and abs(x2) <= tol:
            return dict(base, kind="pair", axis="v", half=r3(abs(y1)))
        return dict(base, kind="cluster", pts=[[r3(p[0]), r3(p[1])] for p in pts])
    if n == 4:
        xs, ys = sorted(abs(p[0]) for p in rel), sorted(abs(p[1]) for p in rel)
        if (xs[-1] - xs[0] <= tol and ys[-1] - ys[0] <= tol and xs[0] > tol and ys[0] > tol
                and len({(x > 0, y > 0) for x, y in rel}) == 4):
            a, b = 2 * sum(xs) / 4, 2 * sum(ys) / 4
            return dict(base, kind="square" if abs(a - b) <= 2 * tol else "rect",
                        a=r3(a), b=r3(b), side=r3((a + b) / 2))
    rads = [math.hypot(*p) for p in rel]
    if n >= 3 and max(rads) - min(rads) <= tol and min(rads) > tol:
        angs = sorted(round(math.degrees(math.atan2(p[1], p[0])) % 360.0, 2) for p in rel)
        return dict(base, kind="pcd", pcd_d=r3(2 * sum(rads) / n), angles=angs)
    xs, ys = sorted(p[0] for p in rel), sorted(p[1] for p in rel)
    for axis, line, other in (("u", xs, [abs(p[1]) for p in rel]), ("v", ys, [abs(p[0]) for p in rel])):
        if max(other) <= tol and n >= 3:
            steps = [b - a for a, b in zip(line, line[1:])]
            if max(steps) - min(steps) <= tol:
                return dict(base, kind="row", axis=axis, pitch=r3(sum(steps) / len(steps)),
                            start=r3(line[0] + (cx if axis == "u" else cy)),
                            end=r3(line[-1] + (cx if axis == "u" else cy)))
    return dict(base, kind="cluster", pts=[[r3(p[0]), r3(p[1])] for p in pts])


def _signature(c):
    k = c["kind"]
    if k == "pcd":
        a = c["angles"]
        rel = tuple(round((x - a[0]) % 360.0, 1) for x in a)    # с точностью до поворота
        return (k, c["n"], c["pcd_d"], rel)
    if k in ("rect", "square"):
        return (k, c["a"], c["b"])
    if k == "pair":
        return (k, c["axis"], c["half"])
    if k == "row":
        return (k, c["axis"], c["pitch"], c["n"])
    return (k, c["n"])


def _slots_and_notches(planes, bbox, i, j, tol=PAT_TOL):
    """Пазы (пары параллельных стенок навстречу, зазор ≤ SLOT_W_MAX) и вырезы у кромок (стенка с
    нормалью к ближней кромке внутри габарита) из small_planes сводки. Координаты — (u, v)."""
    ax_u, ax_v = "xyz"[i], "xyz"[j]
    used, slots, notches = set(), [], []
    byaxis = {ax_u: i, ax_v: j}
    ps = [p for p in planes if p["axis"] in byaxis]
    for a, p in enumerate(ps):
        if a in used or p["nrm"] != 1:
            continue
        k = byaxis[p["axis"]]
        o = j if k == i else i                     # ось вдоль паза
        for b, q in enumerate(ps):
            if b in used or b == a or q["axis"] != p["axis"] or q["nrm"] != -1:
                continue
            w = q["level"] - p["level"]
            if not (0 < w <= SLOT_W_MAX):
                continue
            if abs(q["gab"][o] - p["gab"][o]) > tol or abs(q["gab"][o + 3] - p["gab"][o + 3]) > tol:
                continue
            used |= {a, b}
            c = [0.0, 0.0]
            c[0 if k == i else 1] = (p["level"] + q["level"]) / 2
            c[1 if k == i else 0] = (p["gab"][o] + p["gab"][o + 3]) / 2
            slots.append({"along": "u" if o == i else "v", "w": r3(w), "center": [r3(c[0]), r3(c[1])],
                          "flat": r3(p["gab"][o + 3] - p["gab"][o]),
                          "from": r3(p["gab"][o]), "to": r3(p["gab"][o + 3])})
            break
    for a, p in enumerate(ps):
        if a in used:
            continue
        k = byaxis[p["axis"]]
        lo, hi = bbox[k], bbox[k + 3]
        if abs(p["level"] - lo) <= tol or abs(p["level"] - hi) <= tol:
            continue                               # сама кромка
        edge = lo if p["nrm"] == -1 else hi
        depth = abs(edge - p["level"])
        if depth > 0.3 * (hi - lo):
            continue
        o = j if k == i else i
        notches.append({"edge": ("-" if p["nrm"] == -1 else "+") + ("u" if k == i else "v"),
                        "depth": r3(depth), "from": r3(p["gab"][o]), "to": r3(p["gab"][o + 3]),
                        "width": r3(p["gab"][o + 3] - p["gab"][o])})
    return slots, notches


def patterns(summary, tol=PAT_TOL):
    """Узоры плоской детали для шаблона «плита» (схема инженера, эталон ACME.100.04.00.001).
    Ось толщины — наименьший габарит; (u, v) — две другие оси модели. Группы отверстий
    (ось вдоль толщины) режутся на узлы (кластеры), узлы классифицируются (single, pair,
    rect/square, pcd, row, cluster), одинаковые узлы сводятся в один с places и centers.
    Пазы и вырезы — из small_planes. Координаты узлов — абсолютные (u, v) модели."""
    bb = summary["bbox"]
    ext = [bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]]
    k = min(range(3), key=lambda m: ext[m])
    i, j = [m for m in range(3) if m != k]
    L, W = ext[i], ext[j]
    res = {"axis": "xyz"[k], "u": "xyz"[i], "v": "xyz"[j], "L": r3(L), "W": r3(W),
           "thickness": r3(ext[k]), "center": [r3((bb[i] + bb[i + 3]) / 2), r3((bb[j] + bb[j + 3]) / 2)],
           "top": r3(bb[k + 3]), "groups": [], "slots": [], "notches": []}
    thr = CLUSTER_FRAC * max(L, W)
    for g in summary.get("holes") or []:
        if _aligned(g["axis"]) != k:
            continue
        pts = [(p[i], p[j]) for p in g["centers_top"]]
        found = {}
        # сначала вся группа как один узор (пары/сетки/PCD относительно центра плиты), и только
        # бесструктурная группа режется на узлы по близости
        whole = _classify(pts, tol)
        groups = [list(range(len(pts)))] if whole["kind"] != "cluster" or len(pts) == 1 \
            else _clusters(pts, thr)
        for cl in groups:
            c = _classify([pts[m] for m in cl], tol)
            c["holes"] = [[r3(pts[m][0]), r3(pts[m][1])] for m in cl]
            sig = _signature(c)
            if sig in found:
                found[sig]["places"] += 1
                found[sig]["centers"].append(c["center"])
            else:
                c.update(d=g["d"], through=g.get("through"), depth=g.get("depth"),
                         places=1, centers=[c["center"]])
                found[sig] = c
        res["groups"].extend(found.values())
    res["groups"].sort(key=lambda c: (-c["d"], c["kind"]))
    res["slots"], res["notches"] = _slots_and_notches(summary.get("small_planes") or [], bb, i, j, tol)
    return res
