# -*- coding: utf-8 -*-
"""Метрика «как у инженера» — дефекты читаемости и образмеривания по геометрическому дампу
чертежа (tools/drawing_geom.py). Без эталона и без КОМПАС: чистый Python, бумажные мм.

    python -X utf8 tools/drawing_metric.py <a.geom.json> [<b.geom.json> ...] [--md]

Что считается (на лист и на чертёж):
  cross_ann    точки пересечения линий РАЗНЫХ обозначений (размерные/выносные линии, ножки
               выносок, рамки) — у инженера на чистом листе их почти нет;
  cross_geom   линии обозначений, пересекающие основную линию контура (стиль 1) не у своего
               конца (выносная от контура — не дефект, ножка через чужие отверстия — дефект);
  text_hits    тексты, налезающие на контур, на чужие линии или чужие тексты;
  leader_long  выноски и рамки допусков с ножкой длиннее LEADER_LONG мм листа;
  stack_excess ряды линейных размеров с одной стороны вида сверх STACK_MAX (лесенка всех
               длин от одного торца вместо цепочки + габарита, как ставит инженер);
  tight_rows   пары параллельных линейных размеров с перекрытием, размерные линии которых
               ближе ROW_GAP мм (ГОСТ 2.307: не менее 7 мм);
  nonround     номиналы ≥ 10 мм не кратные 0,5 (сырые числа модели вместо конструкторских);
  frame_no_tol размеры в рамке в виде без единого допуска расположения.
Итог — дефектов на 10 обозначений (defects_per10): ниже — лучше. Стрелки (отрезки короче
ARROW мм) в пересечениях не участвуют. Проверка метрики — эталоны инженера должны получать
заметно меньше, чем чертежи конвейера (docs/drawings-rework-2026-10-19.md, этап 2а).
"""
import json
import math
import os
import re
import sys

ARROW = 4.0          # мм: отрезки короче — стрелки, засечки
EPS_END = 0.6        # мм: пересечение у конца отрезка обозначения — касание, не пересечение
LEADER_LONG = 40.0   # мм листа
ROW_GAP = 6.0        # мм между параллельными размерными линиями
TEXT_SHRINK = 0.4    # мм: габарит текста КОМПАС с запасом
CONTOUR_STYLES = {1}
STACK_MAX = 3        # рядов параллельных размеров с одной стороны вида без замечания
KEYS = ["cross_ann", "cross_geom", "text_hits", "leader_long", "tight_rows", "stack_excess",
        "nonround", "frame_no_tol"]


def seg_len(s):
    return math.hypot(s[2] - s[0], s[3] - s[1])


def bbox(s):
    return (min(s[0], s[2]), min(s[1], s[3]), max(s[0], s[2]), max(s[1], s[3]))


def bb_hit(a, b, tol=0.0):
    return a[0] <= b[2] + tol and b[0] <= a[2] + tol and a[1] <= b[3] + tol and b[1] <= a[3] + tol


def intersect(p, q):
    """Точка пересечения отрезков p, q (x1,y1,x2,y2) или None (параллельные — None)."""
    x1, y1, x2, y2 = p[:4]
    x3, y3, x4, y4 = q[:4]
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-9:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def near_end(pt, s, eps=EPS_END):
    return math.hypot(pt[0] - s[0], pt[1] - s[1]) < eps or math.hypot(pt[0] - s[2], pt[1] - s[3]) < eps


def seg_in_box(s, b):
    """Отрезок s задевает прямоугольник b (x1,y1,x2,y2) — отсечение Лианга-Барски."""
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


def shrink(b, d=TEXT_SHRINK):
    return (b[0] + d, b[1] + d, b[2] - d, b[3] - d)


def same_box(a, b, tol=0.6):
    """Один и тот же текст двумя объектами (заголовок вида дублируется текстом вида)."""
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def parse_num(s):
    s = (s or "").replace(",", ".")
    m = re.search(r"\d+(\.\d+)?", s)
    return float(m.group(0)) if m else None


def is_nonround(v):
    return v is not None and v >= 10 and abs(v * 2 - round(v * 2)) > 1e-6


def sheet_of(x, sheets):
    for s in sheets:
        if s["x0"] <= x < s["x1"]:
            return s["i"]
    return sheets[0]["i"] if sheets and x < sheets[0]["x0"] else (sheets[-1]["i"] if sheets else 1)


def lin_dim_line(a):
    """Размерная линия линейного размера: самый длинный отрезок, не являющийся выносной
    (выносные начинаются у точек привязки). Возвращает (сегмент, 'h'|'v'|None)."""
    anchors = a.get("anchors") or []
    best = None
    for s in a["segs"]:
        if seg_len(s) < ARROW:
            continue
        if any(math.hypot(s[0] - p[0], s[1] - p[1]) < 3 or math.hypot(s[2] - p[0], s[3] - p[1]) < 3
               for p in anchors):
            continue
        if best is None or seg_len(s) > seg_len(best):
            best = s
    if best is None:
        return None, None
    if abs(best[1] - best[3]) < 0.05:
        return best, "h"
    if abs(best[0] - best[2]) < 0.05:
        return best, "v"
    return best, None


def stack_levels(lins, vbox, tol=1.0):
    """Число рядов (различных уровней размерных линий) линейных размеров по сторонам вида:
    {(лист, 'v<i>:<сторона>'): n}. Сторона — сверху/снизу (горизонтальные) или слева/справа
    (вертикальные) от габарита видимого контура вида."""
    levels = {}
    for A, s, o in lins:
        b = vbox.get(A["view"])
        if b is None:
            continue
        if o == "h":
            c = s[1]
            side = "сверху" if c > b[3] else ("снизу" if c < b[1] else None)
        else:
            c = s[0]
            side = "справа" if c > b[2] else ("слева" if c < b[0] else None)
        if side:
            levels.setdefault((A["sheet"], f"v{A['view']}:{side}"), []).append(c)
    out = {}
    for k, cs in levels.items():
        cs.sort()
        n = 1
        for a, b in zip(cs, cs[1:]):
            if b - a > tol:
                n += 1
        out[k] = n
    return out


def evaluate(geom):
    sheets = geom.get("sheets") or [{"i": 1, "x0": 0.0, "x1": 1e9}]
    per = {s["i"]: {k: 0 for k in KEYS} | {"n_ann": 0, "examples": []} for s in sheets}

    def bump(sh, key, ex=None):
        per[sh][key] += 1
        if ex and len(per[sh]["examples"]) < 12:
            per[sh]["examples"].append(f"{key}: {ex}")

    anns, contour, vbox = [], [], {}
    for v in geom["views"]:
        for g in v.get("geom", []):
            if int(g[4]) in CONTOUR_STYLES and (len(g) < 6 or g[5]):   # g[5] — видим по растру
                contour.append((tuple(g[:4]), bbox(g)))
                b = vbox.get(v["i"])
                vbox[v["i"]] = bbox(g) if b is None else (min(b[0], g[0], g[2]), min(b[1], g[1], g[3]),
                                                          max(b[2], g[0], g[2]), max(b[3], g[1], g[3]))
        has_tol = any(a.get("kind") == "tol" for a in v.get("ann", []))
        for a in v.get("ann", []):
            if "err" in a:
                continue
            segs = [tuple(s) for s in a.get("segs", [])]
            pts = [(s[0] + s[2]) / 2 for s in segs] + [(t[0] + t[2]) / 2 for t in a.get("texts", [])]
            if not pts:
                continue
            sh = sheet_of(sum(pts) / len(pts), sheets)
            long_segs = [s for s in segs if seg_len(s) >= ARROW]
            anns.append({"a": a, "sheet": sh, "view": v["i"], "segs": long_segs,
                         "sbb": [bbox(s) for s in long_segs],
                         "texts": [shrink(t) for t in a.get("texts", [])]})
            per[sh]["n_ann"] += 1
            lab = a.get("label") or {}
            if a["kind"] in ("lin", "dia", "rad"):
                val = parse_num(lab.get("nominal"))
                if is_nonround(val):
                    bump(sh, "nonround", f"{a['id']} {lab.get('nominal')}")
                if lab.get("frame") and not has_tol:
                    bump(sh, "frame_no_tol", f"{a['id']} {lab.get('nominal')}")
            if a["kind"] in ("led", "tol"):
                legs = [seg_len(s) for s in long_segs if abs(s[1] - s[3]) > 0.05]   # не полки
                if legs and max(legs) > LEADER_LONG:
                    bump(sh, "leader_long", f"{a['id']} {round(max(legs))} мм")

    # пересечения обозначений между собой
    seen = set()
    for i in range(len(anns)):
        A = anns[i]
        for j in range(i + 1, len(anns)):
            B = anns[j]
            if A["sheet"] != B["sheet"]:
                continue
            for sa, ba in zip(A["segs"], A["sbb"]):
                for sb, bb in zip(B["segs"], B["sbb"]):
                    if not bb_hit(ba, bb):
                        continue
                    pt = intersect(sa, sb)
                    if pt is None or near_end(pt, sa) or near_end(pt, sb):
                        continue
                    key = (A["sheet"], round(pt[0], 1), round(pt[1], 1))
                    if key not in seen:
                        seen.add(key)
                        bump(A["sheet"], "cross_ann", f"{A['a']['id']}×{B['a']['id']}")
    # линии обозначений через контур
    for A in anns:
        hits = set()
        for sa, ba in zip(A["segs"], A["sbb"]):
            for sg, bg in contour:
                if not bb_hit(ba, bg):
                    continue
                pt = intersect(sa, sg)
                if pt is None or near_end(pt, sa):
                    continue
                hits.add((round(pt[0], 1), round(pt[1], 1)))
        for _ in hits:
            bump(A["sheet"], "cross_geom", A["a"]["id"])
    # тексты на контуре, чужих линиях и текстах
    for A in anns:
        for t in A["texts"]:
            if t[2] <= t[0] or t[3] <= t[1]:
                continue
            why = None
            if any(bb_hit(t, bg) and seg_in_box(sg, t) for sg, bg in contour):
                why = "контур"
            else:
                for B in anns:
                    if B is A or B["sheet"] != A["sheet"]:
                        continue
                    if any(bb_hit(t, bb) and seg_in_box(sb, t) for sb, bb in zip(B["segs"], B["sbb"])):
                        why = "линия " + B["a"]["id"]
                        break
                    if any(bb_hit(t, u) and not same_box(t, u) for u in B["texts"]):
                        why = "текст " + B["a"]["id"]
                        break
            if why:
                bump(A["sheet"], "text_hits", f"{A['a']['id']} на {why}")
    # близкие ряды параллельных линейных размеров (в пределах вида)
    lins = []
    for A in anns:
        if A["a"]["kind"] == "lin":
            s, o = lin_dim_line(A["a"])
            if o:
                lins.append((A, s, o))
    for i in range(len(lins)):
        A, s, o = lins[i]
        for j in range(i + 1, len(lins)):
            B, t, p = lins[j]
            if p != o or A["view"] != B["view"]:
                continue
            if o == "h":
                gap, ov = abs(s[1] - t[1]), min(max(s[0], s[2]), max(t[0], t[2])) - max(min(s[0], s[2]), min(t[0], t[2]))
            else:
                gap, ov = abs(s[0] - t[0]), min(max(s[1], s[3]), max(t[1], t[3])) - max(min(s[1], s[3]), min(t[1], t[3]))
            if ov > 1.0 and 0.05 < gap < ROW_GAP:
                bump(A["sheet"], "tight_rows", f"{A['a']['id']}↔{B['a']['id']} {gap:.1f} мм")
    for (sh, side), n in stack_levels(lins, vbox).items():
        if n > STACK_MAX:
            per[sh]["stack_excess"] += n - STACK_MAX
            if len(per[sh]["examples"]) < 12:
                per[sh]["examples"].append(f"stack_excess: {side} {n} рядов")
    total = {k: sum(p[k] for p in per.values()) for k in KEYS + ["n_ann"]}
    n = max(total["n_ann"], 1)
    total["defects"] = sum(total[k] for k in KEYS)
    total["defects_per10"] = round(10.0 * total["defects"] / n, 2)
    return {"doc": os.path.basename(geom.get("doc", "")), "total": total, "sheets": per}


def md_table(results):
    head = "| Показатель | " + " | ".join(r["doc"] for r in results) + " |"
    sep = "|---|" + "---|" * len(results)
    rows = [head, sep]
    for k in ["n_ann"] + KEYS + ["defects", "defects_per10"]:
        rows.append(f"| {k} | " + " | ".join(str(r["total"][k]) for r in results) + " |")
    return "\n".join(rows)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    res = []
    for p in args:
        with open(p, encoding="utf-8") as f:
            res.append(evaluate(json.load(f)))
    if "--md" in sys.argv:
        print(md_table(res))
        for r in res:
            ex = [e for s in r["sheets"].values() for e in s["examples"]]
            print(f"\n{r['doc']}: " + "; ".join(ex[:12]))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
