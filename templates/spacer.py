# -*- coding: utf-8 -*-
"""Шаблон чертежа класса «проставки» (задача 12): брусок L×W×H с рядом отверстий вдоль длины.

    spacer_spec(summary, cdw_path, model_path) -> dict   — drawing_spec из сводки модели
    python templates/spacer.py <model.m3d> <out.cdw> [--spec-only]

summary — ответ kompas_model_summary (JSON → dict). Всё берётся из модели, ничего не вводится
руками. Класс (иначе ValueError — деталь уходит общему планировщику drawing-planner):
  длина — ось X, ширина — Y, высота — Z; ровно две плоскости на каждой оси (прямоугольный брусок),
  нет бобышек, дуг, наклонных граней; одна группа отверстий, ось Z, вход сверху (z = max),
  центры в один ряд вдоль X; Ø сверления → резьба по project_rules.json:drill_to_thread.
Чертёж: главный вид «#Слева» (длина × высота) + вид сверху под ним (длина × ширина, отверстия);
размеры L, H (главный), W, привязка первого отверстия от левого торца (база) и межосевые
цепочкой (вид сверху); резьба «N отв. М4-6H» на полке линии-выноски над видом (только сквозные
отверстия: глубины ГОСТ 2.311 полка не несёт — глухие уходят общему планировщику); ТТ — TT; Ra —
по материалу (project_rules.json:ra_default). Формат и масштаб: первый масштаб ряда ГОСТ 2.302
(1:1, затем уменьшения), при котором блок видов с полосами размеров влезает в А4 верт., иначе в А3.
"""
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import drawing_rules   # noqa: E402
import model_summary   # noqa: E402

# Старые чертежи PRJ.02 (Проставка 210х10/30х20/торцевая.cdw) ТТ не имеют — пункты по решению
# задачи 12; пользователь может их поправить здесь.
TT = ["1. Острые кромки притупить.",
      "2. Неуказанные предельные отклонения размеров по ГОСТ 30893.1-m (H14, h14, ±IT14/2).",
      "3. Маркировать обозначение."]

TOL = 0.01
GAP = 40.0            # мм листа между главным видом и видом сверху (rules: 30–40)
BAND_TOP = 18.0       # полоса над главным видом: L (вылет 10 + текст)
BAND_SIDE = 18.0      # полоса сбоку: одна ступень вертикального размера
STEP = 8.0            # ступень лесенки (layout.ladder)
MIN_FEATURE = 2.0     # мм листа: меньше — контур/стрелки сливаются, нужен обрыв или выносной
TT_CHARS = 50         # знаков в строке ТТ шириной 175 мм шрифтом 5 (с запасом)
SHEETS = {4: (210.0, 297.0, True), 3: (420.0, 297.0, False)}   # формат: ширина, высота, вертикальный


def frame(fmt):
    w, h, _ = SHEETS[fmt]
    return (20.0, 5.0, w - 5.0, h - 5.0)


def tt_top(fmt):
    """Верх блока ТТ над штампом (D.tt_area: низ 65, 7 мм на физическую строку + 4)."""
    k = sum(math.ceil(len(s) / TT_CHARS) for s in TT)
    return 65.0 + 7.0 * k + 4.0


def _regions(fmt):
    """Где может стоять блок видов (x1, y1, x2, y2): над ТТ и под знаком Ra; на А3 ещё —
    левее колонки штампа/ТТ во всю высоту."""
    w, h, _ = SHEETS[fmt]
    out = [(25.0, tt_top(fmt) + 8.0, w - 10.0, h - 32.0)]
    if w - 185.0 - 10.0 > 25.0:
        out.append((25.0, 10.0, w - 185.0 - 10.0, h - 10.0))
    return out


def _scale_value(s):
    a, b = s.split(":")
    return float(a.replace(",", ".")) / float(b.replace(",", "."))


def _num(v):
    return f"{v:g}".replace(".", ",")


def _class_check(summary):
    """Группа отверстий и габарит, если деталь — проставка; иначе ValueError с причиной."""
    bb = summary.get("bbox")
    if not bb:
        raise ValueError("нет bbox в сводке модели")
    if summary.get("bodies") not in (None, 1):
        raise ValueError(f"тел {summary['bodies']}, нужно 1")
    if summary.get("bosses") or summary.get("arcs"):
        raise ValueError("есть бобышки или скругления — не прямоугольный брусок")
    pl = summary.get("planes") or {}
    if pl.get("other") or pl.get("capped"):
        raise ValueError("есть наклонные грани (фаски?) — не прямоугольный брусок")
    for k, ax in enumerate("xyz"):
        lv = sorted(p[0] for p in pl.get(ax, []))
        if len(lv) != 2 or abs(lv[0] - bb[k]) > TOL or abs(lv[1] - bb[k + 3]) > TOL:
            raise ValueError(f"плоскости по {ax}: {lv} — у бруска ровно две, по габариту")
    L, W, H = (bb[k + 3] - bb[k] for k in range(3))
    if not L >= max(W, H):
        raise ValueError(f"длина {L:g} по X не наибольшая (W {W:g}, H {H:g}) — модель ориентирована иначе")
    holes = summary.get("holes") or []
    if len(holes) != 1:
        raise ValueError(f"групп отверстий {len(holes)}, шаблон ведёт ровно одну")
    g = holes[0]
    if g["axis"] != [0.0, 0.0, 1.0]:
        raise ValueError(f"ось отверстий {g['axis']} — нужен вход сверху по Z [0,0,1]")
    if not g["through"]:
        raise ValueError("отверстия глухие: глубины резьбы и сверления (ГОСТ 2.311) полка выноски "
                         "не несёт, а Ø-размер кладёт надпись на контур (Р13) — общий планировщик")
    if summary.get("truncated"):
        raise ValueError(summary["truncated"])
    cs = g["centers_top"]
    if any(abs(c[1] - cs[0][1]) > TOL or abs(c[2] - bb[5]) > TOL for c in cs):
        raise ValueError("центры отверстий не в один ряд вдоль X на верхней грани")
    return bb, g


def _thread(g, table):
    """Текст резьбы по Ø сверления; не резьба (вставка и т. п.) — ValueError."""
    t = model_summary._thread_for(g["d"], table)
    m = re.fullmatch(r"резьба [МM](\d+)", t or "")
    if not m:
        raise ValueError(f"Ø{_num(g['d'])}: «{t}» — шаблон ставит только метрическую резьбу")
    nom = int(m.group(1))
    return f"М{nom}-6H" if g["n"] == 1 else f"{g['n']} отв. М{nom}-6H"


def _layout(L, W2, H, nb, nl, min_feature):
    """Формат, масштаб и угол блока: первый масштаб (1:1, уменьшения ряда ГОСТ 2.302), при котором
    блок видов влезает в А4, иначе в А3. W2 — ширина вида сверху с запасом на знак оси Y."""
    for sc in drawing_rules.load_rules()["gost_2302_scales"]:
        if not sc.startswith("1:"):
            continue
        s = _scale_value(sc)
        bw = BAND_SIDE + STEP * (nl - 1) + L * s + BAND_SIDE
        bh = BAND_TOP + H * s + GAP + W2 * s + (BAND_SIDE + STEP * (nb - 1))
        for fmt in (4, 3):
            for rx1, ry1, rx2, ry2 in _regions(fmt):
                if bw <= rx2 - rx1 and bh <= ry2 - ry1:
                    if min_feature * s < MIN_FEATURE:
                        raise ValueError(
                            f"при масштабе {sc} элемент {min_feature:g} мм выйдет {min_feature * s:.2g} мм "
                            f"на листе (< {MIN_FEATURE:g}) — нужен обрыв/выносной, общий планировщик")
                    return fmt, sc, s, rx1 + (rx2 - rx1 - bw) / 2, ry1 + (ry2 - ry1 - bh) / 2, bw, bh
    raise ValueError(f"брусок {L:g} не влезает в А3 ни при одном масштабе")


def _r(v):
    return round(v * 2) / 2


def spacer_spec(summary: dict, cdw_path: str, model_path: str) -> dict:
    """drawing_spec проставки по сводке модели (kompas_model_summary). ValueError — не класс."""
    bb, g = _class_check(summary)
    x1, y1, z1, x2, y2, z2 = bb
    L, H = x2 - x1, z2 - z1
    xs = sorted(c[0] for c in g["centers_top"])
    yh = g["centers_top"][0][1]
    off_center = abs(yh - (y1 + y2) / 2) > TOL
    text = _thread(g, model_summary.load_drill_to_thread())

    W2 = 2 * max(abs(y1), abs(y2))      # знак оси Y на листе не важен: берём симметричный запас
    nb = len(xs)                         # снизу вида сверху: привязка + межосевые
    nl = 2 if off_center else 1          # слева: W (+ привязка ряда по Y)
    fmt, sc, s, bx1, by1, bw, bh = _layout(L, W2, H, nb, nl, min(L, y2 - y1, H, g["d"]))
    fx = _r(bx1 + BAND_SIDE + STEP * (nl - 1) - x1 * s)
    fy = _r(by1 + bh - BAND_TOP - z2 * s)
    ty = _r(fy + z1 * s - GAP - W2 / 2 * s)

    def p(x, y, z=z2):
        return [float(x), float(y), float(z)]

    def lin(view, i, a, b, side):
        return {"view": view, "id": i, "kind": "lin", "side": side,
                "ref": {"type": "points", "p1": a, "p2": b}}

    dims = [lin("front", "L", p(x1, yh), p(x2, yh), "top"),
            lin("front", "H", p(x2, yh, z1), p(x2, yh, z2), "right"),
            lin("top", "W", p(x1, y1), p(x1, y2), "left"),
            lin("top", "X1", p(x1, yh), p(xs[0], yh), "bottom")]
    dims += [lin("top", f"P{i}", p(a, yh), p(b, yh), "bottom")
             for i, (a, b) in enumerate(zip(xs, xs[1:]), 1)]
    if off_center:
        dims.append(lin("top", "Y1", p(x1, y1), p(x1, yh), "left"))

    # Резьба — на полке выноски над видом, стрелка на окружность справа. Ø-размер (threads) не годится:
    # КОМПАС кладёт его надпись ~11 мм от центра вдоль полки — при 30° подъём ~5,5 мм, и у бруска
    # шириной 10 и шире надпись касается кромки вида (Р13; Ч10 диаметральные не судит).
    leaders = [{"view": "top", "at": p(round(xs[0] + g["d"] / 2, 3), yh), "text": text}]

    rules = drawing_rules.load_rules()
    mat = (summary.get("props") or {}).get("material") or ""
    nonmetal = any(m.lower() in mat.lower() for m in rules["nonmetal_marks"] + rules["nonmetal_materials"])
    return {"model": model_path, "cdw": cdw_path, "format": fmt, "vertical": SHEETS[fmt][2],
            "scale": sc,
            "views": [{"key": "front", "orient": "#Слева", "x": fx, "y": fy},
                      {"key": "top", "orient": "#Сверху", "x": fx, "y": ty}],
            "dims": dims, "leaders": leaders, "tt": list(TT),
            "ra": rules["ra_default"]["nonmetal" if nonmetal else "metal"], "stamp": {}}


def main(argv):
    args = [a for a in argv if a != "--spec-only"]
    if len(args) != 2:
        print(__doc__)
        return 2
    model, cdw = (os.path.abspath(a) for a in args)
    import server
    call = lambda name, **kw: getattr(getattr(server, name), "__wrapped__", getattr(server, name))(**kw)
    raw = call("kompas_model_summary", path=model)
    if not raw.lstrip().startswith("{"):
        print(raw)
        return 1
    try:
        spec = spacer_spec(json.loads(raw), cdw, model)
    except ValueError as e:
        print(f"NOT_IN_CLASS: {e}")
        return 3
    spec_path = os.path.splitext(cdw)[0] + ".spec.json"
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    if "--spec-only" in argv:
        print(json.dumps({"spec": spec_path, "format": spec["format"], "scale": spec["scale"],
                          "dims": len(spec["dims"])}, ensure_ascii=False))
        return 0
    print(call("kompas_build_drawing", spec_path=spec_path))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
