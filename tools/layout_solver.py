# -*- coding: utf-8 -*-
"""Решатель «растр занятости» для второго прохода по чертежу (план v5, П2): по геометрическому
дампу листа (tools/drawing_geom.py) находит обозначения с дефектами метрики
(tools/drawing_metric.py: cross_geom, cross_ann, text_hits) и подбирает сдвиги, после которых
evaluate() даёт строго меньше дефектов и не добавляет cross_ann / text_hits. Чистый Python,
без КОМПАС; правки в документ вносит jobs/relayout.py.

    python -X utf8 tools/layout_solver.py <geom.json> [--out moves.json] [--md]

solve(geom, options=None) -> [{"sheet", "view", "ann_id", "kind": lin|dia|rad|led|rem|thread,
    "move": {"dx", "dy"} | {"shelf": [x, y], "arrows_out"?: true} | {"arrows_out": true},
    "reason": "cross_geom|cross_ann|text_hits", "before": n, "after": n}, ...]
    dx/dy — сдвиг текста размера в мм листа: вдоль размерной линии — только текст (X3/Y3
    «вдоль»), поперёк — вся размерная линия с выносными (Y3/X3 «поперёк»); shelf — новая
    бумажная точка начала полки (ножка от стрелки/окружности к ней); arrows_out — стрелки
    Ø-размера наружу (ksDimArrowOutside), когда они длиннее отверстия. before/after — дефекты
    листа по evaluate() до и после правки.

Ход: растр 1 мм занятости (видимая геометрия всех стилей, отрезки и тексты всех обозначений)
отсеивает кандидатов, чей текст ложится на чужие клетки; выжившие проверяются evaluate() на
копии дампа. Кандидаты: lin — текст вдоль линии ±MAX_SHIFT шагом STEP, линия поперёк на те же
величины и перенос на другую сторону контура вида (ROW_GAPS от габарита); полки (dia/rad/led/rem)
— полярная сетка вокруг стрелки, ножка ≤ MAX_LEG, начало полки за контуром вида. Размерная линия,
стоящая вне контура, внутрь не заводится (правило «вылет вне контура»). Всё детерминировано.
Штриховки в дампе нет (контейнер вида отдаёт только отрезки/дуги/окружности) — она не учитывается.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import drawing_metric as M  # noqa: E402

OPTIONS = {"step": 2.0, "max_shift": 40.0, "row_gaps": (10.0, 18.0, 26.0, 34.0),
           "leg_steps": (10.0, 20.0, 30.0, 40.0, 50.0, 60.0), "angle_step": 15,
           "max_eval": 40, "passes": 2}
MOVABLE = ("lin", "dia", "rad", "led", "rem")
DEFECT_KEYS = ("cross_geom", "cross_ann", "text_hits")
TOUCH = 0.05       # мм: общая точка двух отрезков обозначения
ANCHOR = 3.0       # мм: конец выносной у точки привязки (как drawing_metric.lin_dim_line)
MARGIN = 5.0       # мм: поле листа
PAD = 0.5          # мм: запас вокруг текста при проверке растра (габарит текста КОМПАС неточен)
STAMP = (185.0, 55.0)   # основная надпись справа внизу — занято


# ------------------------------------------------------------------ геометрия обозначений
def _close(p, q, tol=TOUCH):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def _ends(s):
    return (s[0], s[1]), (s[2], s[3])


def translate_ann(a, dx, dy):
    """Копия обозначения, сдвинутого целиком на (dx, dy) мм листа."""
    b = dict(a)
    b["segs"] = [[s[0] + dx, s[1] + dy, s[2] + dx, s[3] + dy] for s in a.get("segs", [])]
    b["texts"] = [[t[0] + dx, t[1] + dy, t[2] + dx, t[3] + dy] for t in a.get("texts", [])]
    if a.get("anchors"):
        b["anchors"] = [[p[0] + dx, p[1] + dy] for p in a["anchors"]]
    return b


def ann_kind(a):
    lab = a.get("label") or {}
    if a["kind"] == "dia" and (int(lab.get("sign") or 0) == 4 or
                               (lab.get("nominal") or "").lstrip().upper()[:1] in ("М", "M")):
        return "thread"
    return a["kind"]


def lin_parts(a):
    """(размерная линия, 'h'|'v', [индексы выносных]) или None (наклонный/без линии)."""
    dim, o = M.lin_dim_line(a)
    if not o:
        return None
    anchors = a.get("anchors") or []
    ext = [k for k, s in enumerate(a["segs"]) if M.seg_len(s) >= M.ARROW and
           any(math.hypot(s[0] - p[0], s[1] - p[1]) < ANCHOR or
               math.hypot(s[2] - p[0], s[3] - p[1]) < ANCHOR for p in anchors)]
    return dim, o, ext


def lin_perp(a, d, o, ext):
    """Размерная линия поперёк на d: всё сдвигается, выносные остаются у точек привязки,
    вылет за линию — с той же величиной на новой стороне."""
    b = translate_ann(a, 0.0, d) if o == "h" else translate_ann(a, d, 0.0)
    b["anchors"] = [list(p) for p in a.get("anchors") or []]
    c = 1 if o == "h" else 0        # координата поперёк
    dim = M.lin_dim_line(a)[0]
    line_c, new_c = dim[c], dim[c] + d
    for k in ext:
        s = list(a["segs"][k])
        p0, p1 = _ends(s)
        near0 = min(math.hypot(p0[0] - q[0], p0[1] - q[1]) for q in a["anchors"])
        near1 = min(math.hypot(p1[0] - q[0], p1[1] - q[1]) for q in a["anchors"])
        anc, far = (0, 2) if near0 <= near1 else (2, 0)      # индекс x конца у привязки / дальнего
        over = abs(s[far + c] - line_c)
        s[far + c] = new_c + (over if new_c >= s[anc + c] else -over)
        b["segs"][k] = s
    return b


def lin_along(a, t, o):
    """Текст вдоль размерной линии на t; линия продлевается под текст, вышедший за пролёт."""
    b = dict(a)
    b["segs"] = [list(s) for s in a["segs"]]
    b["texts"] = ([[x[0] + t, x[1], x[2] + t, x[3]] for x in a["texts"]] if o == "h"
                  else [[x[0], x[1] + t, x[2], x[3] + t] for x in a["texts"]])
    dim = M.lin_dim_line(a)[0]
    k = next(i for i, s in enumerate(a["segs"]) if s == dim or list(s) == list(dim))
    i = 0 if o == "h" else 1
    lo, hi = min(dim[i], dim[i + 2]), max(dim[i], dim[i + 2])
    for x in b["texts"]:
        lo, hi = min(lo, x[i]), max(hi, x[i + 2])
    s = list(dim)
    s[i], s[i + 2] = (lo, hi) if dim[i] <= dim[i + 2] else (hi, lo)
    b["segs"][k] = s
    return b


def shelf_parts(a):
    """Полка с текстом: {"shelf": k, "leg": k, "S": начало полки, "T": стрелка, "dir": ±1,
    "C", "r", "dim": k, "wings": [k]} (C/r/dim/wings — у Ø-размера) или None."""
    segs = a.get("segs") or []
    texts = a.get("texts") or []
    best = None
    for k, s in enumerate(segs):
        if abs(s[1] - s[3]) > TOUCH or M.seg_len(s) < M.ARROW:
            continue
        lo, hi = min(s[0], s[2]), max(s[0], s[2])
        if not any(t[2] > lo and t[0] < hi and min(abs(t[1] - s[1]), abs(t[3] - s[1])) < 1.5
                   for t in texts):
            continue
        if best is None or M.seg_len(s) > M.seg_len(segs[best]):
            best = k
    if best is None:
        return None
    sh = segs[best]
    for k, s in enumerate(segs):
        if k == best:
            continue
        for e_s in _ends(sh):
            for j, e in enumerate(_ends(s)):
                if _close(e_s, e) and not _close(_ends(s)[1 - j], e_s):
                    S, T = e_s, _ends(s)[1 - j]
                    other = (sh[2], sh[3]) if _close(S, (sh[0], sh[1])) else (sh[0], sh[1])
                    p = {"shelf": best, "leg": k, "S": S, "T": T,
                         "dir": 1 if other[0] >= S[0] else -1, "len": M.seg_len(sh)}
                    if a["kind"] == "dia":
                        _dia_parts(a, p)
                    return p
    return None


def _dia_parts(a, p):
    """Линия Ø (от стрелки T через центр: отрезок, антипараллельный ножке), центр, радиус
    и крылья стрелок (отрезки у концов линии Ø)."""
    T = p["T"]
    ld = (p["S"][0] - T[0], p["S"][1] - T[1])
    ln = math.hypot(*ld) or 1.0
    best = None
    for k, s in enumerate(a["segs"]):
        if k == p["leg"] or M.seg_len(s) < 0.5:
            continue
        for j, e in enumerate(_ends(s)):
            if _close(e, T):
                q = _ends(s)[1 - j]
                cs = ((q[0] - T[0]) * ld[0] + (q[1] - T[1]) * ld[1]) / (M.seg_len(s) * ln)
                if cs < -0.9 and (best is None or cs < best[0]):
                    best = (cs, k, q)
    if best is None:
        return
    _, k, q = best
    p["dim"], p["P"] = k, q
    p["C"] = ((T[0] + q[0]) / 2, (T[1] + q[1]) / 2)
    p["r"] = M.seg_len(a["segs"][k]) / 2
    tips = (T, q)
    p["wings"] = [k for k, s in enumerate(a["segs"]) if k not in (p["leg"], p["dim"], p["shelf"])
                  and any(_close(e, t) for e in _ends(s) for t in tips)]


def _mirror_x(t, S, Snew, d_old, d_new):
    u1, u2 = (t[0] - S[0]) * d_old, (t[2] - S[0]) * d_old
    if d_old == -1:
        u1, u2 = u2, u1
    return ([Snew[0] + u1, t[1], Snew[0] + u2, t[3]] if d_new == 1
            else [Snew[0] - u2, t[1], Snew[0] - u1, t[3]])


def shelf_move(a, p, Snew):
    """Обозначение с новым началом полки Snew: ножка от стрелки (у Ø — от окружности под новым
    углом, линия и крылья повёрнуты вокруг центра), полка той же длины, текст за полкой."""
    b = dict(a)
    segs = [list(s) for s in a["segs"]]
    T = p["T"]
    if "C" in p:
        C, r = p["C"], p["r"]
        ang = math.atan2(Snew[1] - C[1], Snew[0] - C[0])
        T = (C[0] + r * math.cos(ang), C[1] + r * math.sin(ang))
        rot = ang - math.atan2(p["T"][1] - C[1], p["T"][0] - C[0])
        cs, sn = math.cos(rot), math.sin(rot)

        def R(x, y):
            x, y = x - C[0], y - C[1]
            return [C[0] + x * cs - y * sn, C[1] + x * sn + y * cs]
        for k in [p["dim"]] + p.get("wings", []):
            s = segs[k]
            segs[k] = R(s[0], s[1]) + R(s[2], s[3])
    segs[p["leg"]] = [T[0], T[1], Snew[0], Snew[1]]
    d_new = 1 if Snew[0] - T[0] >= -1e-9 else -1
    segs[p["shelf"]] = [Snew[0], Snew[1], Snew[0] + d_new * p["len"], Snew[1]]
    b["segs"] = segs
    dy = Snew[1] - p["S"][1]
    b["texts"] = [[q[0], q[1] + dy, q[2], q[3] + dy]
                  for q in (_mirror_x(t, p["S"], Snew, p["dir"], d_new) for t in a["texts"])]
    return b


def arrows_out(a, p):
    """Крылья стрелок Ø-размера зеркально наружу (ksDimArrowOutside); None — уже снаружи."""
    tips = (p["T"], p["P"])
    C = p["C"]
    segs = [list(s) for s in a["segs"]]
    flipped = False
    for k in p.get("wings", []):
        s = segs[k]
        tip = next(t for t in tips if _close((s[0], s[1]), t) or _close((s[2], s[3]), t))
        far = (s[2], s[3]) if _close((s[0], s[1]), tip) else (s[0], s[1])
        if (far[0] - tip[0]) * (C[0] - tip[0]) + (far[1] - tip[1]) * (C[1] - tip[1]) <= 0:
            continue                                   # крыло уже наружу
        segs[k] = [tip[0], tip[1], 2 * tip[0] - far[0], 2 * tip[1] - far[1]]
        flipped = True
    if not flipped:
        return None
    b = dict(a)
    b["segs"] = segs
    return b


# ------------------------------------------------------------------ растр занятости
class Raster:
    """Клетки 1 мм листа: сколько объектов их занимает; own — клетки каждого обозначения."""

    def __init__(self, geom):
        self.cells, self.own = {}, {}
        for v in geom["views"]:
            for g in v.get("geom", []):
                if len(g) < 6 or g[5]:
                    self._seg(g, None)
            for a in v.get("ann", []):
                if "err" in a:
                    continue
                for s in a.get("segs", []):
                    self._seg(s, a["id"])
                for t in a.get("texts", []):
                    self._box(M.shrink(t), a["id"])

    def _mark(self, c, owner):
        self.cells[c] = self.cells.get(c, 0) + 1
        if owner is not None:
            o = self.own.setdefault(owner, {})
            o[c] = o.get(c, 0) + 1

    def _seg(self, s, owner):
        n = max(1, int(math.ceil(M.seg_len(s) / 0.5)))
        last = None
        for k in range(n + 1):
            c = (math.floor(s[0] + (s[2] - s[0]) * k / n), math.floor(s[1] + (s[3] - s[1]) * k / n))
            if c != last:
                self._mark(c, owner)
                last = c

    def _box(self, b, owner):
        for ix in range(math.floor(b[0]), math.floor(b[2]) + 1):
            for iy in range(math.floor(b[1]), math.floor(b[3]) + 1):
                self._mark((ix, iy), owner)

    def free_seg(self, s, owner):
        own = self.own.get(owner, {})
        n = max(1, int(math.ceil(M.seg_len(s) / 0.5)))
        for k in range(n + 1):
            c = (math.floor(s[0] + (s[2] - s[0]) * k / n), math.floor(s[1] + (s[3] - s[1]) * k / n))
            if self.cells.get(c, 0) - own.get(c, 0) > 0:
                return False
        return True

    def free_box(self, b, owner):
        own = self.own.get(owner, {})
        for ix in range(math.floor(b[0]), math.floor(b[2]) + 1):
            for iy in range(math.floor(b[1]), math.floor(b[3]) + 1):
                if self.cells.get((ix, iy), 0) - own.get((ix, iy), 0) > 0:
                    return False
        return True


# ------------------------------------------------------------------ решатель
def _work(geom):
    return {"doc": geom.get("doc", ""), "sheets": geom.get("sheets"),
            "views": [dict(v, ann=list(v.get("ann", []))) for v in geom["views"]]}


def _sheet(geom, a):
    pts = [(s[0] + s[2]) / 2 for s in a.get("segs", [])] + [(t[0] + t[2]) / 2 for t in a.get("texts", [])]
    sheets = geom.get("sheets") or [{"i": 1, "x0": 0.0, "x1": 420.0, "h": 297.0}]
    i = M.sheet_of(sum(pts) / len(pts), sheets)
    return next(s for s in sheets if s["i"] == i)


def _in_sheet(sh, b):
    x0, x1, h = sh["x0"], sh["x1"], sh.get("h", 297.0)
    if b[0] < x0 + 20 or b[2] > x1 - MARGIN or b[1] < MARGIN or b[3] > h - MARGIN:
        return False
    return not (b[2] > x1 - MARGIN - STAMP[0] and b[1] < MARGIN + STAMP[1])


def _vbox(view):
    b = None
    for g in view.get("geom", []):
        if int(g[4]) in M.CONTOUR_STYLES and (len(g) < 6 or g[5]):
            q = M.bbox(g)
            b = q if b is None else (min(b[0], q[0]), min(b[1], q[1]), max(b[2], q[2]), max(b[3], q[3]))
    return b


def _bb_texts(a):
    ts = a.get("texts") or []
    return (min(t[0] for t in ts), min(t[1] for t in ts), max(t[2] for t in ts), max(t[3] for t in ts)) if ts else None


def _counts(ev):
    t = ev["total"]
    return t["defects"], t["cross_ann"], t["text_hits"]


def _candidates(a, view, sh, opt):
    """[(стоимость, порядок, move, обозначение)] — без проверки растра и метрики."""
    out = []
    kind = a["kind"]
    if kind == "lin":
        lp = lin_parts(a)
        if not lp:
            return out
        dim, o, ext = lp
        c = 1 if o == "h" else 0
        vb = _vbox(view)
        lo, hi = (vb[c], vb[c + 2]) if vb else (None, None)
        line = dim[c]
        inside = vb is not None and lo < line < hi
        ks = range(1, int(opt["max_shift"] / opt["step"]) + 1)
        for k in ks:
            for sign in (1, -1):
                d = sign * k * opt["step"]
                if vb and not inside and lo < line + d < hi:
                    continue
                mv = {"dx": 0.0, "dy": round(d, 2)} if o == "h" else {"dx": round(d, 2), "dy": 0.0}
                out.append((abs(d), 0, mv, lin_perp(a, d, o, ext)))
        anc = [q[c] for q in a.get("anchors") or []]
        if vb and not inside and anc:            # на другую сторону контура — только если привязки к ней ближе
            am = sum(anc) / len(anc)
            for gap in opt["row_gaps"]:
                d = (lo - gap - line) if line > hi else (hi + gap - line)
                if abs(line + d - am) >= abs(line - am):
                    continue
                mv = {"dx": 0.0, "dy": round(d, 2)} if o == "h" else {"dx": round(d, 2), "dy": 0.0}
                out.append((gap, 1, mv, lin_perp(a, d, o, ext)))
        if a.get("texts"):
            for k in ks:
                for sign in (1, -1):
                    t = sign * k * opt["step"]
                    mv = {"dx": t, "dy": 0.0} if o == "h" else {"dx": 0.0, "dy": t}
                    out.append((abs(t), 2, mv, lin_along(a, t, o)))
        return out
    p = shelf_parts(a)
    if p is None:
        return out
    if "C" in p:
        b = arrows_out(a, p)
        if b is not None:
            out.append((0.0, 0, {"arrows_out": True}, b))
    vb = _vbox(view)
    base = p["C"] if "C" in p else p["T"]
    r0 = p["r"] if "C" in p else 0.0
    a_old = math.degrees(math.atan2(p["S"][1] - base[1], p["S"][0] - base[0]))
    for L in opt["leg_steps"]:
        for n in range(0, 360, int(opt["angle_step"])):
            ang = math.radians(n)
            S = (base[0] + (r0 + L) * math.cos(ang), base[1] + (r0 + L) * math.sin(ang))
            if vb and vb[0] - 2 < S[0] < vb[2] + 2 and vb[1] - 2 < S[1] < vb[3] + 2:
                continue
            da = abs((n - a_old + 180) % 360 - 180)
            b = shelf_move(a, p, S)
            mv = {"shelf": [round(S[0], 2), round(S[1], 2)]}
            out.append((L + da / 90.0, 3, mv, b))
            if "C" in p:                    # Ø: та же полка + стрелки наружу
                b2 = arrows_out(b, shelf_parts(b) or p)
                if b2 is not None:
                    out.append((L + da / 90.0 + 0.1, 3, dict(mv, arrows_out=True), b2))
    return out


def _attribution(work, ev0):
    """Вклад каждого подвижного обозначения в дефекты: evaluate без него."""
    base = sum(ev0["total"][k] for k in DEFECT_KEYS)
    res = []
    for vi, v in enumerate(work["views"]):
        for j, a in enumerate(v["ann"]):
            if "err" in a or a["kind"] not in MOVABLE:
                continue
            anns = v["ann"][:j] + v["ann"][j + 1:]
            w = dict(work, views=[dict(x, ann=anns) if x is v else x for x in work["views"]])
            ev = M.evaluate(w)
            gain = base - sum(ev["total"][k] for k in DEFECT_KEYS)
            if gain > 0:
                res.append((-gain, a["id"], vi, j))      # по убыванию вклада, затем по id
    return sorted(res)


def _swap(work, view, a, b):
    return dict(work, views=[dict(x, ann=[b if y is a else y for y in x["ann"]]) if x is view else x
                             for x in work["views"]])


def _best_for(work, view, a, ev0, opt, gain=0):
    """Лучший принятый кандидат (ключ, move, обозначение, evaluate) и лучший «заблокированный» —
    снижающий дефекты, но с новым cross_ann (для парного хода с соседом). gain — вклад a в
    дефекты (_attribution): кандидат, снявший его целиком, лучше не станет — перебор прерывается."""
    sh = _sheet(work, a)
    ras = Raster(work)
    d0, ca0, th0 = _counts(ev0)
    best = blocked = None
    tried = 0
    for cost, order, mv, b in sorted(_candidates(a, view, sh, opt), key=lambda c: (c[0], c[1], json.dumps(c[2]))):
        tb = _bb_texts(b)
        if tb is not None and (not _in_sheet(sh, tb) or
                               not ras.free_box((tb[0] - PAD, tb[1] - PAD, tb[2] + PAD, tb[3] + PAD), a["id"])):
            continue                                 # текст на чужих клетках (с запасом PAD)
        if any(not _in_sheet(sh, M.bbox(s)) for s in b["segs"]):
            continue
        if order in (0, 1):
            dim = M.lin_dim_line(b)[0]
            if dim is None or not ras.free_seg(dim, a["id"]):
                continue                             # размерная линия поперёк чужого
        tried += 1
        if tried > int(opt["max_eval"]):
            break
        ev = M.evaluate(_swap(work, view, a, b))
        d1, ca1, th1 = _counts(ev)
        if d1 >= d0 or th1 > th0:
            continue
        key = (d1, ca1 + th1, cost, order)
        if ca1 <= ca0:
            if best is None or key < best[0]:
                best = (key, mv, b, ev)
            if d1 <= d0 - gain:
                break
        elif blocked is None or key < blocked[0]:
            blocked = (key, mv, b, ev)
    return best, blocked


def _partners(view, a, b):
    """Подвижные обозначения вида, чьи линии пересекает обозначение b (кроме a)."""
    segs = [s for s in b["segs"] if M.seg_len(s) >= M.ARROW]
    out = []
    for o in view["ann"]:
        if o is a or "err" in o or o["kind"] not in MOVABLE:
            continue
        for s in segs:
            if any(M.intersect(s, t) for t in o.get("segs", []) if M.seg_len(t) >= M.ARROW):
                out.append(o)
                break
    return out


def _record(sh, view, a, mv, ev0, ev):
    reason = "|".join(k for k in DEFECT_KEYS if ev["total"][k] < ev0["total"][k]) or         "|".join(k for k in M.KEYS if ev["total"][k] < ev0["total"][k])
    return {"sheet": sh["i"], "view": view["i"], "ann_id": a["id"], "kind": ann_kind(a),
            "move": mv, "reason": reason, "before": ev0["total"]["defects"],
            "after": ev["total"]["defects"]}


def simulate(geom, options=None):
    """(правки, дамп после правок)."""
    opt = dict(OPTIONS, **(options or {}))
    work = _work(geom)
    moves = []
    for _ in range(int(opt["passes"])):
        ev0 = M.evaluate(work)
        changed = False
        for neg_gain, aid, vi, j in _attribution(work, ev0):
            view = work["views"][vi]
            a = view["ann"][j]
            sh = _sheet(work, a)
            best, blocked = _best_for(work, view, a, ev0, opt, -neg_gain)
            if best is None and blocked is not None:
                # ход a даёт новый cross_ann с соседом — попробовать сдвинуть и соседа
                _, mv_a, b_a, ev_a = blocked
                tmp = _swap(work, view, a, b_a)
                tview = next(x for x in tmp["views"] if x["i"] == view["i"])
                for o in _partners(tview, b_a, b_a)[:2]:
                    bo, _ = _best_for(tmp, tview, o, ev0, opt)
                    if bo is not None:
                        view["ann"][j] = b_a
                        moves.append(_record(sh, view, a, mv_a, ev0, ev_a))
                        view["ann"][view["ann"].index(o)] = bo[2]
                        moves.append(_record(sh, view, o, bo[1], ev_a, bo[3]))
                        ev0, changed = bo[3], True
                        break
                continue
            if best is None:
                continue
            _, mv, b, ev = best
            view["ann"][j] = b
            moves.append(_record(sh, view, a, mv, ev0, ev))
            ev0 = ev
            changed = True
        if not changed:
            break
    return moves, work


def solve(geom, options=None):
    return simulate(geom, options)[0]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    with open(args[0], encoding="utf-8") as f:
        geom = json.load(f)
    moves, after = simulate(geom)
    if "--out" in sys.argv:
        with open(sys.argv[sys.argv.index("--out") + 1], "w", encoding="utf-8") as f:
            json.dump(moves, f, ensure_ascii=False, indent=1)
    r0, r1 = M.evaluate(geom), M.evaluate(after)
    r0["doc"], r1["doc"] = "было", "стало"
    if "--md" in sys.argv:
        print(M.md_table([r0, r1]))
        for m in moves:
            print(f"- {m['ann_id']} ({m['kind']}): {json.dumps(m['move'], ensure_ascii=False)} "
                  f"— {m['reason']}, {m['before']}→{m['after']}")
    else:
        print(json.dumps({"before": r0["total"], "after": r1["total"], "moves": moves},
                         ensure_ascii=False, indent=1))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
