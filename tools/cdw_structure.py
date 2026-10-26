# -*- coding: utf-8 -*-
"""Структурный дамп чертежа .cdw → JSON (только чтение). Для сравнения чертежа с эталоном
по объектам, а не по картинке: листы, все графы штампа, ТТ, виды (тип, масштаб, угол, позиция,
обозначение), по видам — линейные/диаметральные/радиальные/угловые размеры (точки, текст,
префикс/суффикс, знак Ø, скобки/рамка), выноски (тексты над/под полкой, спецсимволы, ветви),
допуски формы (знак, значение, базы, таблица, позиция), базы, выносные элементы (форма, радиус,
полка, тексты), центровые, тексты (шрифт, высота), штриховки, счётчики геометрии.

    python -X utf8 tools/cdw_structure.py <путь.cdw> [--out <json>]

Документ, если не открыт, открывается только для чтения под HideMessage и закрывается
без сохранения. Свойства, которых у объекта нет, молча пропускаются (probe).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

DIM_GEOM = ["X1", "Y1", "X2", "Y2", "X3", "Y3", "Xc", "Yc", "Angle", "Angle1", "Angle2", "Radius",
            "TextPos", "ArrowPos", "Direction", "DimensionType", "Orientation", "ShelfX", "ShelfY",
            "ShelfDirection"]
DIM_TEXT = ["Sign", "Brackets", "Rectangle", "AutoNominalValue", "NominalValue", "DeviationOn",
            "DeviationType", "Accuracy", "Style", "TextAlign", "TextFormat", "UnderLine", "Unit",
            "ToleranceOn", "HasTolerance", "SignFont"]
DIM_TEXTS = ["NominalText", "Prefix", "Suffix", "TextUnder", "Tolerance"]
LEADER_P = ["ShelfDirection", "SignType", "Arround", "AutoSorted", "ParallelBranch", "BranchBegin",
            "X", "Y", "Angle", "Style"]
LEADER_T = ["TextOnShelf", "TextUnderShelf", "TextAfterShelf", "TextOnBranch", "TextUnderBranch"]
TOL_P = ["Sign", "Value", "PrefixSign", "SuffixSign", "BaseSign1", "BaseSign2", "BasePointPos", "X", "Y",
         "Angle", "Style"]
TOL_T = ["BaseValue1", "BaseValue2"]
REM_P = ["X", "Y", "Xc", "Yc", "Form", "Radius", "Height", "Width", "FilletRadius", "ShelfX", "ShelfY",
         "ShelfDirection", "AutoSheet", "AutoZone", "AutoSorted", "Angle", "Style"]
REM_T = ["TextUp", "TextDown", "Text"]
BASE_P = ["X0", "Y0", "BranchX", "BranchY", "AutoSorted", "Angle", "Style"]
BASE_T = ["Text"]
MARK_P = ["X", "Y", "Type", "Angle", "Style", "Radius"]
TEXT_P = ["X", "Y", "Angle", "Style", "Align", "Allocation"]
VIEW_P = ["Name", "X", "Y", "Scale", "Angle", "Visible", "Current", "Number"]
DESIG_P = ["ShowScale", "ShowAngle", "ShowName", "ShowPage", "ShowZone", "ShowTurn", "Designation"]


def r3(v):
    try:
        f = float(v)
        return 0.0 if abs(f) < 5e-4 else round(f, 3)
    except Exception:
        return v


def probe(o, names):
    out = {}
    for n in names:
        try:
            v = getattr(o, n)
        except Exception:
            continue
        if isinstance(v, bool):
            out[n] = v
        elif isinstance(v, (int, float)):
            out[n] = r3(v)
        elif isinstance(v, str):
            out[n] = v
        elif v is None:
            out[n] = None
        else:               # COM-объект: Str, если есть
            try:
                out[n] = {"obj": str(v.Str)}
            except Exception:
                out[n] = {"obj": "?"}
    return out


def itext(t):
    """IText → {"str", "lines": [[{Str, ItemType, Number, FontName, Height, Italic...}]]}."""
    from win32com.client import CastTo
    res = {}
    try:
        res["str"] = t.Str
    except Exception:
        return None
    lines = []
    try:
        n = t.Count
        try:
            t.TextLine(0)
            tls = [t.TextLine(i) for i in range(n)]
        except Exception:          # ITextLine: сами элементы, без строк
            tls = [t]
        for tl in tls:
            items = []
            for j in range(tl.Count):
                ti = tl.TextItem(j)
                it = probe(ti, ["Str", "ItemType", "Number", "SymbolFontName", "NewLine", "SizeFactor"])
                try:
                    f = CastTo(ti, "ITextFont")
                    it.update(probe(f, ["FontName", "Height", "Italic", "Bold", "WidthFactor", "Underline"]))
                except Exception:
                    pass
                items.append(it)
            lines.append(items)
    except Exception:
        pass
    if lines:
        res["lines"] = lines
    return res


def texts_of(o, names):
    out = {}
    for n in names:
        try:
            t = getattr(o, n)
        except Exception:
            continue
        if t is None:
            continue
        try:
            d = itext(t)
        except Exception:
            d = None
        if d is not None and (d.get("str") or d.get("lines")):
            out[n] = d
    return out


def branches(o):
    from win32com.client import CastTo
    try:
        br = CastTo(o, "IBranchs")
        return {"X0": r3(br.X0), "Y0": r3(br.Y0), "n": br.BranchCount,
                "pts": [[r3(br.BranchX(k)), r3(br.BranchY(k))] for k in range(br.BranchCount)]}
    except Exception:
        return None


def coll(sc, cname, getter):
    try:
        c = getattr(sc, cname)
        n = c.Count
    except Exception:
        return []
    out = []
    for j in range(n):
        try:
            out.append(getattr(c, getter)(j))
        except Exception:
            out.append(None)
    return out


def dump_dim(o, kind):
    from win32com.client import CastTo
    d = {"kind": kind}
    d.update(probe(o, DIM_GEOM))
    try:
        dt = CastTo(o, "IDimensionText")
        d["text"] = probe(dt, DIM_TEXT)
        d["texts"] = texts_of(dt, DIM_TEXTS)
    except Exception as e:
        d["text_err"] = str(e)[:80]
    try:
        d["params"] = probe(CastTo(o, "IDimensionParams"),
                            ["TextPos", "ArrowPos", "ArrowType1", "ArrowType2", "Style"])
    except Exception:
        pass
    return d


def dump_view(i, v, D):
    from win32com.client import CastTo
    res = {"i": i}
    res.update(probe(v, VIEW_P))
    res["kind"] = server._view_kind(i, v.Name or "")
    try:
        vd = CastTo(v, "IViewDesignation")
        res["designation"] = probe(vd, DESIG_P)
        try:
            dt = vd.DrawingText
            if dt is not None:
                res["designation"]["text"] = itext(CastTo(dt, "IText"))
                res["designation"]["text_xy"] = probe(dt, ["X", "Y"])
        except Exception:
            pass
    except Exception:
        pass
    try:
        vc = D.view_content(v)
        res["bbox_view"] = vc.get("bbox")
        res["n_circles"] = len(vc.get("circles") or [])
        res["n_arcs"] = len(vc.get("arc_list") or [])
        if vc.get("bbox"):
            res["bbox_paper"] = [r3(q) for q in server._paper_rect(v, vc["bbox"], D)]
    except Exception as e:
        res["content_err"] = str(e)[:80]
    try:
        sc = CastTo(v, "ISymbols2DContainer")
    except Exception as e:
        res["sc_err"] = str(e)[:80]
        return res
    res["lin"] = [dump_dim(o, "lin") for o in coll(sc, "LineDimensions", "LineDimension") if o is not None]
    res["dia"] = [dump_dim(o, "dia") for o in coll(sc, "DiametralDimensions", "DiametralDimension") if o is not None]
    res["rad"] = [dump_dim(o, "rad") for o in coll(sc, "RadialDimensions", "RadialDimension") if o is not None]
    res["ang"] = [dump_dim(o, "ang") for o in coll(sc, "AngleDimensions", "AngleDimension") if o is not None]
    leaders = []
    for o in coll(sc, "Leaders", "Leader"):
        if o is None:
            continue
        d = probe(o, ["X", "Y", "Angle", "Style"])
        try:
            ld = CastTo(o, "ILeader")
            d.update(probe(ld, LEADER_P))
            d["texts"] = texts_of(ld, LEADER_T)
        except Exception as e:
            d["err"] = str(e)[:80]
        d["branches"] = branches(o)
        leaders.append(d)
    res["leaders"] = leaders
    tols = []
    for o in coll(sc, "Tolerances", "Tolerance"):
        if o is None:
            continue
        d = {}
        try:
            tp = CastTo(o, "IToleranceParam")
            d.update(probe(tp, TOL_P))
            d["texts"] = texts_of(tp, TOL_T)
            try:
                tb = tp.Table
                rows, cols = tb.RowsCount, tb.ColumnsCount
                cells = []
                for r in range(rows):
                    row = []
                    for c in range(cols):
                        try:
                            row.append(itext(tb.Cell(r, c).Text))
                        except Exception:
                            row.append(None)
                    cells.append(row)
                d["table"] = {"rows": rows, "cols": cols, "cells": cells}
            except Exception as e:
                d["table_err"] = str(e)[:80]
        except Exception as e:
            d["err"] = str(e)[:80]
        d["branches"] = branches(o)
        tols.append(d)
    res["tolerances"] = tols
    bases = []
    for o in coll(sc, "Bases", "Base"):
        if o is None:
            continue
        d = probe(o, BASE_P)
        d["texts"] = texts_of(o, BASE_T)
        bases.append(d)
    res["bases"] = bases
    rems = []
    for o in coll(sc, "RemoteElements", "RemoteElement"):
        if o is None:
            continue
        d = probe(o, REM_P)
        d["texts"] = texts_of(o, REM_T)
        adds = []
        for k in range(3):
            try:
                adds.append(itext(o.AdditionalText(k)))
            except Exception:
                break
        d["additional"] = adds
        rems.append(d)
    res["remotes"] = rems
    marks = [probe(o, MARK_P) for o in coll(sc, "CentreMarkers", "CentreMarker") if o is not None]
    res["centre_markers"] = {"n": len(marks), "first": marks[:3]}
    roughs = []
    for o in coll(sc, "Roughs", "Rough"):
        if o is None:
            continue
        d = probe(o, ["X", "Y", "Angle", "Style", "RoughType", "RoughKind"])
        d["texts"] = texts_of(o, ["Text", "TextUp", "TextDown", "TextLeft", "TextRight"])
        roughs.append(d)
    res["roughs"] = roughs
    try:
        dc = CastTo(v, "IDrawingContainer")
        res["geom"] = {}
        for cname in ("LineSegments", "Circles", "Arcs", "Polylines", "Hatches", "Splines", "Rectangles"):
            try:
                res["geom"][cname] = getattr(dc, cname).Count
            except Exception:
                pass
        texts = []
        for o in coll(dc, "DrawingTexts", "DrawingText"):
            if o is None:
                continue
            d = probe(o, TEXT_P)
            try:
                d["text"] = itext(CastTo(o, "IText"))
            except Exception:
                pass
            texts.append(d)
        res["texts"] = texts
    except Exception as e:
        res["dc_err"] = str(e)[:80]
    return res


def dump(path):
    from win32com.client import CastTo
    D = server._load_draw()
    out = {"doc": path}
    with server.com_context():
        app = server.get_app7()
        doc = D.find_doc(app, os.path.basename(path))
        opened = False
        with D.quiet(app):
            if doc is None:
                doc = app.Documents.Open(path, True, True)
                opened = True
            if doc is None:
                raise SystemExit("КОМПАС не открыл " + path)
            try:
                doc.Active = True
            except Exception:
                pass
            out["name"] = doc.Name
            sheets = []
            ls = doc.LayoutSheets
            for si in range(ls.Count):
                sh = ls.Item(si)
                d = {"i": si + 1}
                try:
                    d["format"] = int(sh.Format.Format)
                    d["vertical"] = bool(sh.Format.VerticalOrientation)
                    d["mult"] = int(sh.Format.FormatMultiplicity)
                except Exception:
                    pass
                stamp = {}
                try:
                    st = sh.Stamp
                    for cid in list(range(1, 60)) + [100, 101, 102, 110, 111, 112]:
                        try:
                            t = st.Text(cid)
                            s = (t.Str or "").strip()
                        except Exception:
                            continue
                        if s:
                            stamp[cid] = {"str": s}
                            if cid in (1, 2, 3, 9):
                                stamp[cid]["items"] = (itext(t) or {}).get("lines")
                except Exception as e:
                    d["stamp_err"] = str(e)[:80]
                d["stamp"] = stamp
                sheets.append(d)
            out["sheets"] = sheets
            try:
                tt = CastTo(doc, "IDrawingDocument").TechnicalDemand
                out["tt"] = {"lines": [tt.Text.TextLine(j).Str for j in range(tt.Text.Count)],
                             "props": probe(tt, ["AutoPlacement", "X", "Y", "Width", "Height"])}
                try:
                    out["tt"]["items"] = itext(tt.Text)
                except Exception:
                    pass
            except Exception as e:
                out["tt_err"] = str(e)[:80]
            views = D.views_of(doc)
            out["views"] = [dump_view(i, views.View(i), D) for i in range(views.Count)]
            if opened:
                try:
                    doc.Close(0)
                except Exception as e:
                    out["close_err"] = str(e)[:80]
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    path = os.path.abspath(args[0])
    outp = args[args.index("--out") + 1] if "--out" in args else None
    res = dump(path)
    s = json.dumps(res, ensure_ascii=False, indent=1)
    if outp:
        with open(outp, "w", encoding="utf-8") as f:
            f.write(s)
        v = res.get("views", [])

        def n(key):
            return sum(len(x.get(key, [])) for x in v)
        print(f"OK {outp}: листов {len(res.get('sheets', []))}, видов {len(v)}, lin {n('lin')}, "
              f"dia {n('dia')}, rad {n('rad')}, ang {n('ang')}, leaders {n('leaders')}, "
              f"tol {n('tolerances')}, remotes {n('remotes')}, texts {n('texts')}")
    else:
        print(s)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
