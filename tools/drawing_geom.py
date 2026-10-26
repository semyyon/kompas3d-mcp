# -*- coding: utf-8 -*-
"""Геометрический дамп чертежа .cdw → JSON (только чтение) для метрики «как у инженера»
(tools/drawing_metric.py). В отличие от cdw_structure.py здесь не свойства объектов, а то,
что видно на листе: каждый размер, выноска, рамка допуска, обозначение выносного/разреза/базы,
знак шероховатости и текст вида разбивается КОМПАС на отрезки и тексты (ksDecomposeObj,
level 0); геометрия вида — отрезки, дуги и окружности со стилем линии. Всё в бумажных мм
общей СК чертежа (лист N+1 правее листа N). Геометрия: [x1,y1,x2,y2,стиль,видим]
(видимость — по растру листа, visible_mask).

    python -X utf8 tools/drawing_geom.py <путь.cdw> --out <json>

Из процесса MCP (job, `import server` там недопустим — сервер уже запущен как __main__):
    collect(doc, D) — тот же дамп по уже открытому документу и модулю jobs/_draw.py.

Факты по замеру 2026-10-19 (вал ACME.107.002.004): отрезки разбиения — в СК вида (как X1/Y1
размера), габариты текстов разбиения (ksGetObjGabaritRect) — в бумажных мм.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

ANN_COLLS = [("lin", "LineDimensions", "LineDimension"),
             ("dia", "DiametralDimensions", "DiametralDimension"),
             ("rad", "RadialDimensions", "RadialDimension"),
             ("ang", "AngleDimensions", "AngleDimension"),
             ("led", "Leaders", "Leader"),
             ("tol", "Tolerances", "Tolerance"),
             ("rem", "RemoteElements", "RemoteElement"),
             ("cut", "CutLines", "CutLine"),
             ("base", "Bases", "Base"),
             ("rough", "Roughs", "Rough")]
GEOM_COLLS = [("LineSegments", "LineSegment"), ("Arcs", "Arc"), ("Circles", "Circle")]
SHEET_MM = {0: (841.0, 1189.0), 1: (594.0, 841.0), 2: (420.0, 594.0), 3: (297.0, 420.0),
            4: (210.0, 297.0), 5: (148.0, 210.0)}   # ksFormatA0…A5: (короткая, длинная)
RASTER_DPI = 300


def view_kind(i, name):
    """Тип вида по имени (как server._view_kind; копия — чтобы job не импортировал server)."""
    if "Разрез" in name:
        return "section"
    if "Выносной" in name:
        return "detail"
    if i == 0 or "Системный" in name:
        return "system"
    return "proj"


def preview_paths(png, n_sheets):
    """Файлы растрового экспорта (как server._preview_paths): один лист — png как есть,
    N листов — «<имя>(1).png» … «<имя>(N).png»."""
    if n_sheets <= 1:
        return [png]
    stem, ext = os.path.splitext(png)
    return [f"{stem}({k}){ext}" for k in range(1, n_sheets + 1)]


def sheet_ranges(formats, verticals):
    """Полосы X листов и высоты (лист N+1 вплотную правее N; A0…A5, не только A3/A4)."""
    out, x = [], 0.0
    for f, vert in zip(formats, verticals):
        short, long_ = SHEET_MM.get(int(f), SHEET_MM[3])
        w, h = (short, long_) if vert else (long_, short)
        out.append((x, x + w, h))
        x += w
    return out


def visible_mask(images, sheets, segs, samples=(0.2, 0.35, 0.5, 0.65, 0.8)):
    """Флаг видимости каждого отрезка геометрии по растру листа: контейнер вида отдаёт
    геометрию выносного и местного вида НЕ обрезанной (у выносного — вся деталь родителя),
    поэтому видимым считается отрезок, у которого ≥ 3 из 5 точек лежат на тёмных пикселях
    (окрестность 3×3, яркость < 160)."""
    out = []
    for s in segs:
        xm = (s[0] + s[2]) / 2
        k = next((i for i, sh in enumerate(sheets) if sh["x0"] <= xm < sh["x1"]), None)
        if k is None:
            out.append(False)
            continue
        im, sh = images[k], sheets[k]
        W, H = im.size
        kx, ky = W / (sh["x1"] - sh["x0"]), H / sh["h"]
        dark = 0
        for t in samples:
            x = s[0] + t * (s[2] - s[0]) - sh["x0"]
            y = s[1] + t * (s[3] - s[1])
            px, py = int(round(x * kx)), int(round((sh["h"] - y) * ky))
            if 1 <= px < W - 1 and 1 <= py < H - 1:
                if min(im.getpixel((px + dx, py + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)) < 160:
                    dark += 1
        out.append(dark >= 3)
    return out


def r2(v):
    return round(float(v), 2)


def arc_pts(xc, yc, r, a1, a2, n):
    if a2 < a1:
        a2 += 2 * math.pi
    return [(xc + r * math.cos(a1 + (a2 - a1) * k / n), yc + r * math.sin(a1 + (a2 - a1) * k / n))
            for k in range(n + 1)]


def poly(pts):
    return [(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]) for k in range(len(pts) - 1)]


def label_of(o, kind):
    from win32com.client import CastTo
    try:
        if kind in ("lin", "dia", "rad", "ang"):
            dt = CastTo(o, "IDimensionText")
            return {"nominal": dt.NominalText.Str or "", "prefix": dt.Prefix.Str or "",
                    "suffix": dt.Suffix.Str or "", "sign": int(dt.Sign or 0),
                    "frame": bool(getattr(dt, "Rectangle", False)),
                    "tolerance_on": bool(getattr(dt, "DeviationOn", False))}
        if kind == "led":
            ld = CastTo(o, "ILeader")
            return {"text": " ".join((ld.TextOnShelf.Str or "", ld.TextUnderShelf.Str or "")).strip()}
    except Exception:
        pass
    return {}


class Decomposer:
    def __init__(self, k5, d5):
        self.k5, self.d5 = k5, d5

    def gab(self, ref):
        par = self.k5.GetParamStruct(15)
        if not self.d5.ksGetObjGabaritRect(ref, par):
            return None
        b, t = par.GetpBot(), par.GetpTop()
        return [r2(b.x), r2(b.y), r2(t.x), r2(t.y)]

    def parts(self, ref):
        """(отрезки СК вида [(x1,y1,x2,y2)], тексты [[x1,y1,x2,y2] бумага])."""
        segs, texts = [], []
        grp = self.d5.ksDecomposeObj(ref, 0, 0.1, 0)
        if not grp:
            return segs, texts
        it = self.k5.GetIterator()
        it.ksCreateIterator(0, grp)
        e = it.ksMoveIterator("F")
        while e:
            typ = self.d5.ksGetObjParam(e, None, 0)
            if typ == 1:
                p = self.k5.GetParamStruct(11)
                self.d5.ksGetObjParam(e, p, -1)
                segs.append((p.x1, p.y1, p.x2, p.y2))
            elif typ == 3:
                p = self.k5.GetParamStruct(12)          # ko_ArcByAngleParam
                self.d5.ksGetObjParam(e, p, -1)
                a1, a2 = math.radians(p.ang1), math.radians(p.ang2)
                if p.dir < 0:
                    a1, a2 = a2, a1
                segs += poly(arc_pts(p.xc, p.yc, p.rad, a1, a2, 8))
            elif typ == 2:
                p = self.k5.GetParamStruct(20)
                self.d5.ksGetObjParam(e, p, -1)
                segs += poly(arc_pts(p.xc, p.yc, p.rad, 0.0, 2 * math.pi, 16))
            elif typ == 4:
                g = self.gab(e)
                if g:
                    texts.append(g)
            e = it.ksMoveIterator("N")
        try:
            self.d5.ksDeleteObj(grp)
        except Exception:
            pass
        return segs, texts


def dump_view(i, v, dec, D, doc=None):
    from win32com.client import CastTo
    name = v.Name or ""
    res = {"i": i, "name": name, "kind": view_kind(i, name), "scale": r2(v.Scale),
           "x": r2(v.X), "y": r2(v.Y)}

    def paper(seg):
        a, b = D.view_to_paper(v, seg[0], seg[1]), D.view_to_paper(v, seg[2], seg[3])
        return [r2(a[0]), r2(a[1]), r2(b[0]), r2(b[1])]

    geom = []
    zones = D.break_zones(v)       # вид с разрывом: геометрия контейнера сырая, на листе — перенесена
    try:
        dc = CastTo(v, "IDrawingContainer")
        for cname, getter in GEOM_COLLS:
            c = getattr(dc, cname)
            for j in range(c.Count):
                o = getattr(c, getter)(j)
                st = int(getattr(o, "Style", 1) or 1)
                if cname == "LineSegments":
                    segs = [(o.X1, o.Y1, o.X2, o.Y2)]
                elif cname == "Circles":
                    segs = poly(arc_pts(o.Xc, o.Yc, o.Radius, 0.0, 2 * math.pi, 24))
                else:
                    a1 = math.atan2(o.Y1 - o.Yc, o.X1 - o.Xc)
                    a2 = math.atan2(o.Y2 - o.Yc, o.X2 - o.Xc)
                    if not bool(getattr(o, "Direction", True)):
                        a1, a2 = a2, a1
                    segs = poly(arc_pts(o.Xc, o.Yc, o.Radius, a1, a2, 12))
                if zones:
                    segs = [q for s_ in segs for q in D._bk_seg(zones, *s_)]
                geom += [paper(s) + [st] for s in segs]
        texts = []
        for j in range(dc.DrawingTexts.Count):
            o = dc.DrawingTexts.DrawingText(j)
            g = dec.gab(CastTo(o, "IDrawingObject").Reference)
            if g:
                texts.append({"id": f"v{i}.txt{j}", "kind": "txt", "segs": [], "texts": [g],
                              "label": {"text": CastTo(o, "IText").Str or ""}})
    except Exception as e:
        res["geom_err"] = str(e)[:80]
        texts = []
    res["geom"] = geom
    anns = []
    try:
        sc = CastTo(v, "ISymbols2DContainer")
    except Exception:
        sc = None
    for tag, cname, getter in (ANN_COLLS if sc is not None else []):
        try:
            c = getattr(sc, cname)
            n = c.Count
        except Exception:
            continue
        for j in range(n):
            try:
                o = getattr(c, getter)(j)
                ref = CastTo(o, "IDrawingObject").Reference
                segs, tx = dec.parts(ref)
                if zones and tx and doc is not None:    # вид с разрывом: тексты разбиения — не на месте
                    tx = [list(t) for t in D._break_texts(doc, v, o, [tuple(t) for t in tx])]
                a = {"id": f"v{i}.{tag}{j}", "kind": tag, "segs": [paper(s) for s in segs],
                     "texts": tx, "label": label_of(o, tag)}
                if tag == "lin":
                    a["anchors"] = [list(map(r2, D.view_to_paper(v, o.X1, o.Y1))),
                                    list(map(r2, D.view_to_paper(v, o.X2, o.Y2)))]
                anns.append(a)
            except Exception as e:
                anns.append({"id": f"v{i}.{tag}{j}", "kind": tag, "err": str(e)[:80]})
    if i > 0:
        try:                                              # заголовок вида («А-А», «Б (2:1)»)
            dt = CastTo(v, "IViewDesignation").DrawingText
            g = dec.gab(CastTo(dt, "IDrawingObject").Reference) if dt is not None else None
            if g and g[2] - g[0] > 0.1:
                anns.append({"id": f"v{i}.title", "kind": "title", "segs": [], "texts": [g], "label": {}})
        except Exception:
            pass
    res["ann"] = anns + texts
    return res


def raster(doc, D, n_sheets):
    """Растр листов (PNG RASTER_DPI, оттенки серого) во временную папку; [] — не вышло."""
    import tempfile
    from PIL import Image
    png = os.path.join(tempfile.gettempdir(), "drawing_geom_" + str(os.getpid()) + ".png")
    paths = preview_paths(png, n_sheets)
    for f in paths:
        if os.path.exists(f):
            os.remove(f)
    try:
        D.uncur(doc)
        d5 = D.api5().ActiveDocument2D()
        p = d5.RasterFormatParam()
        p.Init()
        p.format = 3
        p.colorBPP = 8
        p.extResolution = RASTER_DPI
        if not d5.SaveAsToRasterFormat(png, p):
            return []
        imgs = []
        for f in paths:
            with Image.open(f) as im:
                imgs.append(im.convert("L"))
            os.remove(f)
        return imgs
    except Exception as e:
        print("растр не снят:", str(e)[:80])
        return []


def collect(doc, D):
    """Дамп открытого документа doc (IKompasDocument) модулем D = jobs/_draw.py: листы, виды,
    геометрия (с флагом видимости по растру), обозначения. Документ делается активным."""
    out = {"doc": doc.PathName or doc.Name}
    doc.Active = True
    k5 = D.api5()
    dec = Decomposer(k5, k5.ActiveDocument2D())
    ls = doc.LayoutSheets
    fm = [int(ls.Item(s).Format.Format) for s in range(ls.Count)]
    vert = [bool(ls.Item(s).Format.VerticalOrientation) for s in range(ls.Count)]
    out["sheets"] = [{"i": k + 1, "x0": a, "x1": b, "h": h, "format": fm[k], "vertical": vert[k]}
                     for k, (a, b, h) in enumerate(sheet_ranges(fm, vert))]
    views = D.views_of(doc)
    out["views"] = [dump_view(i, views.View(i), dec, D, doc) for i in range(views.Count)]
    images = raster(doc, D, len(fm))
    if images:
        for v in out["views"]:
            vis = visible_mask(images, out["sheets"], v["geom"])
            v["geom"] = [g + [int(f)] for g, f in zip(v["geom"], vis)]
    return out


def dump(path):
    """CLI: открыть (если не открыт) через server и снять collect()."""
    sys.path.insert(0, os.path.dirname(HERE))
    import server
    D = server._load_draw()
    with server.com_context():
        app = server.get_app7()
        doc = D.find_doc(app, os.path.basename(path))
        opened = False
        with D.quiet(app):
            if doc is None:
                doc = app.Documents.Open(path, False, True)
                opened = True
            if doc is None:
                raise SystemExit("КОМПАС не открыл " + path)
            out = collect(doc, D)
            out["doc"] = path
            if opened:
                try:
                    doc.Close(0)
                except Exception as e:
                    out["close_err"] = str(e)[:80]
    return out


def main():
    args = sys.argv[1:]
    if not args or "--out" not in args:
        print(__doc__)
        sys.exit(2)
    res = dump(os.path.abspath(args[0]))
    outp = args[args.index("--out") + 1]
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    v = res["views"]
    print(f"OK {outp}: листов {len(res['sheets'])}, видов {len(v)}, "
          f"геометрии {sum(len(x['geom']) for x in v)} отр., "
          f"обозначений {sum(len(x['ann']) for x in v)}, "
          f"ошибок {sum(1 for x in v for a in x['ann'] if 'err' in a)}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
