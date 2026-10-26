# -*- coding: utf-8 -*-
"""Job второго прохода (план v5, П2): растр занятости → сдвиги обозначений в ОТКРЫТОМ чертеже.
Дамп геометрии листа (tools/drawing_geom.collect, без import server) → решатель
tools/layout_solver.solve → правки примитивами API (X3/Y3, ShelfX/ShelfY, Angle+ShelfLength,
ArrowPos) → RebuildDocument → повторный дамп и метрика. Документ НЕ сохраняется.

Вызов чертёжником — после kompas_build_drawing (документ открыт), до проверки и сохранения:
    kompas_run_job("relayout", '{"document": "ACME.100.01.01.003.cdw"}')
    kompas_run_job("relayout", '{"document": "ACME.100.01.01.003.cdw", "dry_run": true}')

PARAMS:
  document    путь или хвост имени ОТКРЫТОГО .cdw (пусто — активный документ)
  max_passes  проходов «дамп → решатель → правки → перестроение» (умолч. 2)
  dry_run     только посчитать: правки и прогноз метрики, документ не трогать
  out         путь JSON для отчёта (по умолчанию не пишется)

Печатает JSON: {"passes": n, "before": {…total evaluate…}, "after": {…}, "applied": k,
  "skipped": [{"ann_id", "move", "why"}], "moves": [правки по проходам]}.

Ловушки (RC8 docs/drawings-research-2026-10-26.md): положение текста задаётся АБСОЛЮТНО
точкой из дампа + сдвиг (readback X3/Y3/TextPos — не ориентир); у горизонтального размера X3
КОМПАС может не принять — проверка только повторным дампом: правка, которую второй проход
предлагает снова в том же виде, считается не принятой и попадает в skipped; на виде с разрывом
полки не двигаются (GetProjectionPoint/габариты там врут). Ошибка одного объекта не валит job.
"""
import json
import math
import os
import re
import sys

from win32com.client import CastTo

sys.path.insert(0, JOBS_DIR)                                         # noqa: F821 (даёт сервер)
sys.path.insert(0, os.path.join(os.path.dirname(JOBS_DIR), "tools"))  # noqa: F821
import _draw as D               # noqa: E402
import drawing_geom as G        # noqa: E402
import drawing_metric as M      # noqa: E402
import layout_solver as LS      # noqa: E402

P = PARAMS if isinstance(PARAMS, dict) else {}                       # noqa: F821
MAX_PASSES = int(P.get("max_passes") or 2)
DRY = bool(P.get("dry_run"))
COLLS = {tag: (cname, getter) for tag, cname, getter in G.ANN_COLLS}

app = app7                                                           # noqa: F821
name = P.get("document") or ""
doc = D.find_doc(app, os.path.basename(name)) if name else app.ActiveDocument
if doc is None:
    raise RuntimeError("документ не открыт: " + (name or "(активный)"))
if int(doc.DocumentType) != 1:
    raise RuntimeError("не чертёж: " + (doc.PathName or doc.Name))


def find_ann(geom, ann_id):
    for v in geom["views"]:
        for a in v.get("ann", []):
            if a["id"] == ann_id:
                return v, a
    raise KeyError(ann_id)


def obj_of(views, ann_id):
    m = re.fullmatch(r"v(\d+)\.([a-z]+)(\d+)", ann_id)
    if not m or m.group(2) not in COLLS:
        raise ValueError("нет объекта для " + ann_id)
    view = views.View(int(m.group(1)))
    cname, getter = COLLS[m.group(2)]
    coll = getattr(CastTo(view, "ISymbols2DContainer"), cname)
    return view, getattr(coll, getter)(int(m.group(3)))


def apply_lin(view, o, a, mv):
    """Текст линейного размера: абсолютная точка X3/Y3 = (центр текста из дампа, координата
    размерной линии) + сдвиг; у текста на полке (TextType 2) сдвиг вдоль — полкой."""
    dim, orient = M.lin_dim_line(a)
    if not orient:
        raise ValueError("наклонный размер")
    t = a["texts"][0] if a.get("texts") else dim
    tc = ((t[0] + t[2]) / 2, (t[1] + t[3]) / 2)
    px = (tc[0] if orient == "h" else dim[0]) + float(mv.get("dx", 0))
    py = (dim[1] if orient == "h" else tc[1]) + float(mv.get("dy", 0))
    x3, y3 = D.paper_to_view(view, px, py)
    d = CastTo(o, "ILineDimension")
    dp = CastTo(o, "IDimensionParams")
    along = (mv.get("dx", 0) if orient == "h" else mv.get("dy", 0)) != 0
    if along and int(dp.TextType) == 2:
        sx, sy = D.paper_to_view(view, float(mv.get("dx", 0)), float(mv.get("dy", 0)))
        ox, oy = D.paper_to_view(view, 0.0, 0.0)
        d.ShelfX, d.ShelfY = d.ShelfX + (sx - ox), d.ShelfY + (sy - oy)
    else:
        d.X3, d.Y3 = float(x3), float(y3)
    CastTo(o, "IDrawingObject").Update()


def apply_shelf(view, o, a, mv, tag):
    if D.break_zones(view):
        raise ValueError("вид с разрывом: полки не двигаем")
    dp = CastTo(o, "IDimensionParams") if tag in ("dia", "rad") else None
    if "shelf" in mv:
        sx, sy = D.paper_to_view(view, float(mv["shelf"][0]), float(mv["shelf"][1]))
        p = LS.shelf_parts(a) or {}
        tx = p["T"][0] if p else float(mv["shelf"][0])
        direction = 1 if float(mv["shelf"][0]) >= tx else -1
        if tag == "dia":
            d = CastTo(o, "IDiametralDimension")
            ang = math.degrees(math.atan2(sy - d.Yc, sx - d.Xc)) % 360.0
            leg = (math.hypot(sx - d.Xc, sy - d.Yc) - float(d.Radius)) * view.Scale
            dp.TextType = 2
            d.Angle = ang
            dp.ShelfDirection = D.shelf_dir(math.cos(math.radians(ang)))
            dp.ShelfLength = max(float(leg), 1.0)
            CastTo(o, "IDrawingObject").Update()
            if abs(float(d.Angle) - ang) > 0.5:          # память dia-angle-reset-on-create
                d.Angle = ang
                dp.ShelfDirection = D.shelf_dir(math.cos(math.radians(ang)))
        elif tag == "rad":
            d = CastTo(o, "IRadialDimension")
            dp.TextType = 2
            d.ShelfX, d.ShelfY = float(sx), float(sy)
            dp.ShelfDirection = direction
        elif tag == "led":
            br = CastTo(o, "IBranchs")
            br.X0, br.Y0 = float(sx), float(sy)
            CastTo(o, "ILeader").ShelfDirection = direction
        elif tag == "rem":
            r = CastTo(o, "IRemoteElement")
            r.ShelfX, r.ShelfY = float(sx), float(sy)
        else:
            raise ValueError("полка не поддержана для " + tag)
    if mv.get("arrows_out"):
        if dp is None:
            raise ValueError("arrows_out только у размеров")
        dp.ArrowPos = 1                                   # ksDimArrowOutside
    CastTo(o, "IDrawingObject").Update()


def apply_move(doc, geom, m):
    views = D.views_of(doc)
    view, o = obj_of(views, m["ann_id"])
    _, a = find_ann(geom, m["ann_id"])
    tag = m["ann_id"].split(".")[1].rstrip("0123456789")
    if tag == "lin":
        apply_lin(view, o, a, m["move"])
    else:
        apply_shelf(view, o, a, m["move"], tag)


report = {"document": doc.PathName or doc.Name, "passes": 0, "applied": 0, "skipped": [], "moves": []}
with D.quiet(app):
    geom = G.collect(doc, D)
    report["before"] = M.evaluate(geom)["total"]
    after = report["before"]
    seen = set()
    for _ in range(MAX_PASSES):
        moves, sim = LS.simulate(geom)
        if not moves:
            break
        report["passes"] += 1
        if DRY:
            report["moves"] += moves
            after = M.evaluate(sim)["total"]
            break
        for m in moves:
            key = (m["ann_id"], json.dumps(m["move"], sort_keys=True))
            if key in seen:
                report["skipped"].append({"ann_id": m["ann_id"], "move": m["move"],
                                          "why": "повтор: КОМПАС не принял правку прошлого прохода"})
                continue
            seen.add(key)
            try:
                apply_move(doc, geom, m)
                report["applied"] += 1
                report["moves"].append(m)
            except Exception as e:
                report["skipped"].append({"ann_id": m["ann_id"], "move": m["move"], "why": str(e)[:120]})
        try:
            D.rebuild(doc)
        except Exception as e:
            report["skipped"].append({"ann_id": "*", "move": {}, "why": "rebuild: " + str(e)[:100]})
        geom = G.collect(doc, D)
        after = M.evaluate(geom)["total"]
    report["after"] = after
    report["dry_run"] = DRY

if P.get("out"):
    with open(P["out"], "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
print(json.dumps(report, ensure_ascii=False, indent=1))
