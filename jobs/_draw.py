# -*- coding: utf-8 -*-
"""Библиотека 2D-чертежей КОМПАС — проверенные приёмы пилотной партии КД.

Происхождение: kdl.py из сессии 97a1ecce (август 2026) — на этом коде сделаны
утверждённый пилот PRJB.001.100.002 и 4 готовых чертежа. Перенесено без смены
логики; убрана зависимость от локального kd.py.

Подключение: в kompas_run_script модуль уже есть как `D`; из job —
    import sys; sys.path.insert(0, JOBS_DIR)
    import _draw as D
    doc, views, ok = D.new_drawing(r"...\\деталь.cdw", r"...\\деталь.m3d",
                                   orient="#Слева", codes=[3], x=100, y=150, scale=0.4)

Координаты: у видов — БУМАЖНЫЕ мм (X,Y — точка привязки вида), у объектов
внутри вида — координаты ВИДА (бумага = D.view_to_paper(view, x, y)).

Умолчания задачи 6 (по опытам Э1–Э7, docs/experiments-2d.md):
- new_drawing: ProjectDimensions у видов + RebuildDocument (3D-размеры модели
  приходят сами), всё под D.quiet (HideMessage=1 — без модальных «Перестроить?»);
- lin/dia/rad: автономинал и автоположение текста; оси и обозначения центров
  КОМПАС строит сам (Э7) — вручную только оси симметрии `segment(style=3)`;
- stamp: дописывает только ПУСТЫЕ графы, остальное — из свойств модели (Э5);
- tech_req: БЕЗ AutoPlacement (Э3 FAIL) — ручной блок над штампом в два прохода.
"""
import contextlib
import math
import os
import re

import pythoncom
from win32com.client import CastTo, Dispatch, gencache


# --------------------------------------------------------------- подключение
def app7():
    return gencache.EnsureDispatch("Kompas.Application.7")


def api5():
    m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
    return m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(
        m5.KompasObject.CLSID, pythoncom.IID_IDispatch))


def find_doc(app, suffix):
    """Документ по хвосту пути/имени (без учёта регистра)."""
    t = suffix.strip().lower()
    for i in range(app.Documents.Count):
        try:
            d = app.Documents.Item(i)
            if d is None:
                continue
            if (d.PathName or "").lower().endswith(t) or t in (d.Name or "").lower():
                return d
        except Exception:
            pass
    return None


@contextlib.contextmanager
def quiet(app):
    """app.HideMessage = 1 (ksHideMessageYes) на время блока, в finally — старое
    значение. Без этого «Изменена модель… Перестроить чертёж?» и «Обновлены МЦХ…»
    копятся модальными WPF-окнами, COM висит, kompas_auto_dialogs их не нажимает
    (Э1, docs/experiments-2d.md «Общие условия» п.1). После аварийной остановки
    (finally не выполнился) вернуть вручную app7.HideMessage = 0."""
    old = app.HideMessage
    app.HideMessage = 1
    try:
        yield app
    finally:
        app.HideMessage = old


def rebuild(doc):
    """RebuildDocument чертежа без модальных диалогов (под quiet). Нужен после
    ProjectDimensions (Э1: без него размеров в видах 0) и CreateLocalView (Э2).
    Перестроение пересинхронизирует штамп с моделью — штамп заполнять ПОСЛЕ."""
    with quiet(app7()):
        return CastTo(doc, "IKompasDocument2D1").RebuildDocument()


# ------------------------------------------------------------------- чертёж
def new_drawing(cdw, model, orient, codes, x, y, scale, dx=0.0, dy=40.0,
                fmt=3, vertical=False, project_dims=True, mass_grams=False, mass_g=None):
    """Новый чертёж + формат листа + стандартные виды с модели + SaveAs.

    orient — СТРОКА ориентации главного вида с решёткой: "#Спереди", "#Слева",
      "#Сверху", "#Снизу", "#Сзади", "#Справа". Число и имя без «#» КОМПАС молча
      игнорирует (главный выходит «Спереди») — число здесь даёт TypeError,
      «#» дописывается сам. В v23 ось Z модели — вертикаль листа в боковых видах.
    codes — СПИСОК проекций AddStandartViews: 3 = под главным (сверху),
      4 = над главным (снизу), 5 = справа (профиль), 6 = слева; одиночный int
      ведёт себя как непонятная битмаска (TypeError), [] не создаёт видов вовсе
      (ValueError) — для одного главного вида [1].
    fmt: 3=А3, 4=А4 (формат — до видов: смена формата видов не двигает).
    project_dims=True — у видов IAssociationViewElements.ProjectDimensions=True и
      RebuildDocument (Э1): 3D-размеры модели приходят в вид, плоскость которого
      параллельна плоскости размера. Ø в модели ставить по ЦИЛИНДРИЧЕСКОЙ ГРАНИ
      (по ребру — теряется при правке модели). Оси и обозначения центров
      (CreateAxis/CreateCentresMarkers) включены у видов по умолчанию (Э7).
    mass_grams=True — масса в штампе в граммах, 2 знака (SheetStampMassUnit=0,
      SheetStampMassDigitsCount=2) до создания видов: для деталей < 5 г, иначе «0» (Э5).
    mass_g — масса модели в граммах: графа 5 в кг по закону серии (project_rules.json
      mass_format: 2 знака, < 5 г — 3 знака: «0,004»), см. mass_text; перебивает mass_grams.
    Всё — под quiet(): без модальных «Перестроить?»/«Сохранить МЦХ?»."""
    if not isinstance(orient, str):
        raise TypeError(f"orient={orient!r}: нужна строка вида '#Слева' — "
                        "число КОМПАС молча игнорирует (главный вид будет «Спереди»)")
    if not orient.startswith("#"):
        orient = "#" + orient
    if isinstance(codes, int):
        raise TypeError(f"codes={codes!r}: нужен список проекций, например [3] или [3, 5]")
    codes = [int(c) for c in codes]
    if not codes:
        raise ValueError("codes=[]: AddStandartViews не создаст ни одного вида; "
                         "для одного главного вида — [1]")
    app = app7()
    with quiet(app):
        doc = app.Documents.Add(1, True)
        sheet = doc.LayoutSheets.Item(0)
        f = sheet.Format
        f.Format = fmt
        f.VerticalOrientation = vertical
        sheet.Update()
        if mass_g is not None:
            ds = CastTo(doc.DocumentSettings, "IDrawingDocumentSettings")
            ds.SheetStampMassUnit = 1
            ds.SheetStampMassDigitsCount = mass_digits(mass_g)
        elif mass_grams:
            ds = CastTo(doc.DocumentSettings, "IDrawingDocumentSettings")
            ds.SheetStampMassUnit = 0
            ds.SheetStampMassDigitsCount = 2
        d2 = CastTo(doc, "IKompasDocument2D")
        views = d2.ViewsAndLayersManager.Views
        ok = views.AddStandartViews(model, orient, codes, float(x), float(y),
                                    float(scale), float(dx), float(dy))
        if project_dims:
            for i in range(1, views.Count):
                v = views.View(i)
                CastTo(v, "IAssociationViewElements").ProjectDimensions = True
                CastTo(v, "IDrawingObject").Update()
            rebuild(doc)
        os.makedirs(os.path.dirname(os.path.abspath(cdw)), exist_ok=True)
        doc.SaveAs(cdw)
    return doc, views, ok


def views_of(doc):
    return CastTo(doc, "IKompasDocument2D").ViewsAndLayersManager.Views


def dump_views(doc):
    views = views_of(doc)
    return [(i, views.View(i).Name, round(views.View(i).X, 1),
             round(views.View(i).Y, 1), views.View(i).Scale)
            for i in range(views.Count)]


def view_to_paper(view, x, y):
    """Точка из координат вида в бумажные мм: (X,Y) + Scale·R(Angle)·(x,y).
    Одинаково для проекций, разрезов и выносных (Э0, docs/experiments-2d.md:
    точка X3/Y3 попала в свой ksGetObjGabaritRect у 62/62 размеров неповёрнутых
    видов и 10/10 разреза с Angle=90; без поворота у того разреза — 0/10)."""
    s = view.Scale
    a = math.radians(getattr(view, "Angle", 0.0) or 0.0)
    c, n = math.cos(a), math.sin(a)
    return view.X + s * (x * c - y * n), view.Y + s * (x * n + y * c)


def dim_text_paper(view, x3, y3, gab, tol=1.0):
    """Бумажная точка текста линейного размера для проверки Ч10/Ч12.
    (X3,Y3) через view_to_paper — если точка лежит в бумажном габарите размера
    (ksGetObjGabaritRect, ±tol мм). Иначе — центр габарита: X3=Y3=0 читает
    устаревший COM-прокси (stale-x3-false-ch12), точка вне габарита — СК вида
    не та. Нет габарита — (X3,Y3) как есть, а при X3=Y3=0 — None (точка вида,
    не текст)."""
    zones = break_zones(view)
    px, py = view_to_paper(view, _bk_u(zones, x3) if zones else x3, y3)
    ok = not (x3 == 0 and y3 == 0)
    if gab is None:
        return [round(px, 1), round(py, 1)] if ok else None
    if ok and gab[0] - tol <= px <= gab[2] + tol and gab[1] - tol <= py <= gab[3] + tol:
        return [round(px, 1), round(py, 1)]
    return [round((gab[0] + gab[2]) / 2, 1), round((gab[1] + gab[3]) / 2, 1)]


def clip_rect(a, b):
    """Пересечение прямоугольников (x1, y1, x2, y2); None — не пересекаются."""
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] <= r[2] and r[1] <= r[3] else None


def circle_rect(xc, yc, r):
    return (xc - r, yc - r, xc + r, yc + r)


def detail_circle(v):
    """Круг выносного вида (ViewType 6) в СК вида, мм модели: (xc, yc, r) или None.
    Геометрия выносного лежит в СК родителя и НЕ обрезана (bbox — вся деталь:
    Пластина прижимная 4:1 — x до 3716 мм листа). RemoteElement (BaseObject) —
    центр в СК родителя, Radius в бумажных мм родителя → r = Radius / Scale родителя
    (BaseObject.Parent). Местный вид обрезки не требует: после CreateLocalView в его
    контейнере только контур (замер 14b). Прямоугольная граница (Form=1) — круг,
    описанный около прямоугольника; точнее — detail_region."""
    try:
        if v.ViewType != 6:
            return None
        bo = CastTo(v, "IAssociationView").BaseObject
        re_ = CastTo(bo, "IRemoteElement")
        ps = CastTo(bo.Parent, "IView").Scale
        if not ps:
            return None
        if int(getattr(re_, "Form", 0) or 0) in (1, 2):
            return (re_.Xc, re_.Yc, math.hypot(re_.Width, re_.Height) / 2 / ps)
        return (re_.Xc, re_.Yc, re_.Radius / ps)
    except Exception:
        return None


def detail_region(v):
    """Область выносного вида в СК вида, мм модели: прямоугольник (x1, y1, x2, y2) — по кругу
    (Form 0) или по прямоугольной границе (Form 1/2: Width/Height в мм листа родителя,
    Э5 plate_leap); None — не выносной."""
    try:
        if v.ViewType != 6:
            return None
        bo = CastTo(v, "IAssociationView").BaseObject
        re_ = CastTo(bo, "IRemoteElement")
        ps = CastTo(bo.Parent, "IView").Scale
        if not ps:
            return None
        if int(getattr(re_, "Form", 0) or 0) in (1, 2):
            w, h = re_.Width / ps / 2, re_.Height / ps / 2
            return (re_.Xc - w, re_.Yc - h, re_.Xc + w, re_.Yc + h)
        return circle_rect(re_.Xc, re_.Yc, re_.Radius / ps)
    except Exception:
        return None


# ------------------------------------------------------------ разрыв вида (ГОСТ 2.305 п. 6.3)
BREAK_GAP = 10.0   # мм листа: зазор между частями вида с разрывом (Clearance — API его не читает)


def break_zones(v):
    """Вырезанные зоны вида с разрывом: [(u1, u2, зазор)] в СК вида по горизонтали, по
    возрастанию; [] — разрыва нет. Замер PRJ.02 (план rotate 90, 1:2,5): AddBreakLine
    берёт X = u / Scale, Clearance — мм листа; геометрия контейнера вида и ProjectionMatrix
    остаются «сырыми» (без разрыва), КОМПАС переносит при показе и геометрию, и обозначения
    (их координаты задаются сырыми — project); GetProjectionPoint на виде с разрывом считает по
    другой формуле. Видимое положение геометрии — break_u (view_content/prims/segments: проверки
    сравнивают её с габаритами обозначений на листе)."""
    try:
        bp = CastTo(v, "IBreakViewParam")
        n = int(bp.BreaksCount)
    except Exception:
        return []
    sc = v.Scale or 1.0
    out = []
    for i in range(n):
        ok, x1, y1, x2, y2 = bp.GetBreakLinePosition(i)
        if ok:
            out.append((min(x1, x2) * sc, max(x1, x2) * sc, BREAK_GAP / sc))
    return sorted(out)


def break_u(zones, u):
    """Сырая u вида → u на листе вида с разрывом (правее зоны — сдвиг на длину зоны минус
    зазор); внутри зоны — None."""
    shift = 0.0
    for a, b, g in zones:
        if u >= b - 1e-9:
            shift += (b - a) - g
        elif u > a + 1e-9:
            return None
    return u - shift


def _bk_u(zones, u, toward=0):
    """break_u с прижатием точки зоны к её краю: toward −1 — к левому, +1 — к правому."""
    r = break_u(zones, u)
    if r is not None or not zones:
        return r
    for a, b, g in zones:
        if a < u < b:
            return break_u(zones, a) if toward <= 0 else break_u(zones, b)
    return u


def add_break(doc, v, u1, u2, gap=BREAK_GAP):
    """Разрыв вида по горизонтали: вырезать u1…u2 (СК вида), зазор gap мм листа. Ответ — индекс."""
    bp = CastTo(v, "IBreakViewParam")
    sc = v.Scale or 1.0
    i = bp.AddBreakLine(float(min(u1, u2)) / sc, 0.0, float(max(u1, u2)) / sc, 0.0, 0.0)
    bp.SetBreakLineParams(i, 0.0, float(gap), 0, 3.0, 6.0)
    bp.BreaksVisible = True
    v.Update()
    rebuild(doc)
    return i


def raw_projection(view, x, y, z):
    """Точка модели → СК вида без учёта разрыва: IAssociationView.ProjectionMatrix
    (16 чисел по строкам; совпадает с GetProjectionPoint вида без разрыва — проба PRJ.02)."""
    m = list(CastTo(view, "IAssociationView").ProjectionMatrix)
    x, y, z = float(x), float(y), float(z)
    return m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7]


def project(view, x, y, z):
    """Точка модели → СК вида для ОБОЗНАЧЕНИЙ: на виде с разрывом — «сырые» координаты (без
    разрыва): КОМПАС сам переносит размеры, выноски и знаки так же, как геометрию (сборка .008:
    размер, поставленный в координатах с учётом разрыва, уехал к линии обрыва). GetProjectionPoint
    на таком виде считает по своей формуле — не годится. Без разрыва — GetProjectionPoint."""
    if not break_zones(CastTo(view, "IView")):
        return CastTo(view, "IAssociationView").GetProjectionPoint(float(x), float(y), float(z))
    u, w = raw_projection(view, x, y, z)
    return True, u, w


def _bk_seg(zones, x1, y1, x2, y2):
    """Отрезок сырой геометрии → видимые отрезки вида с разрывом (кусок в зоне выпадает)."""
    if not zones:
        return [(x1, y1, x2, y2)]
    if x2 < x1:
        x1, y1, x2, y2 = x2, y2, x1, y1
    dx = x2 - x1

    def yat(u):
        return y1 + (y2 - y1) * (u - x1) / dx if abs(dx) > 1e-12 else None
    cuts = [x1] + [q for a, b, _ in zones for q in (a, b) if x1 < q < x2] + [x2]
    out = []
    for p, q in zip(cuts, cuts[1:]):
        if break_u(zones, (p + q) / 2) is None and abs(q - p) > 1e-9:
            continue
        ya, yb = (yat(p), yat(q)) if abs(dx) > 1e-12 else (y1, y2)
        out.append((_bk_u(zones, p, 1), ya, _bk_u(zones, q, -1), yb))
    return out


def arc_bbox_points(a):
    """Точки габарита ДУГИ: концы, середина и те из четырёх крайних точек окружности,
    что лежат на дуге. Раньше дуга считалась полной окружностью — у сливера Ø22 на
    торце (.011/.012) bbox уходил на R ниже контура, и лесенка ставила размеры в 30 мм."""
    import math
    xc, yc, r = a.Xc, a.Yc, a.Radius
    xs = [a.X1, a.X2, a.X3]
    ys = [a.Y1, a.Y2, a.Y3]
    a1 = math.atan2(a.Y1 - yc, a.X1 - xc)
    a2 = math.atan2(a.Y2 - yc, a.X2 - xc)
    a3 = math.atan2(a.Y3 - yc, a.X3 - xc)

    def ccw(frm, to):                       # поворот против часовой frm→to, [0, 2π)
        return (to - frm) % (2 * math.pi)

    ccw_arc = ccw(a1, a3) <= ccw(a1, a2)    # середина внутри хода против часовой?
    for q in (0.0, math.pi / 2, math.pi, -math.pi / 2):
        inside = ccw(a1, q) <= ccw(a1, a2) if ccw_arc else ccw(a2, q) <= ccw(a2, a1)
        if inside:
            xs.append(xc + r * math.cos(q))
            ys.append(yc + r * math.sin(q))
    return xs, ys


def view_content(v):
    """Геометрия вида: счётчики, окружности, дуги (arc_list), bbox в КООРДИНАТАХ ВИДА
    (у выносного — обрезан кругом выносного, detail_circle)."""
    dc = CastTo(v, "IDrawingContainer")
    lines, circles, arcs = dc.LineSegments, dc.Circles, dc.Arcs
    xs, ys = [], []
    for j in range(lines.Count):
        L = lines.LineSegment(j)
        xs += [L.X1, L.X2]
        ys += [L.Y1, L.Y2]
    for j in range(arcs.Count):
        ax, ay = arc_bbox_points(arcs.Arc(j))
        xs += ax
        ys += ay
    for j in range(circles.Count):
        c = circles.Circle(j)
        xs += [c.Xc - c.Radius, c.Xc + c.Radius]
        ys += [c.Yc - c.Radius, c.Yc + c.Radius]
    cs = [(round(circles.Circle(j).Xc, 2), round(circles.Circle(j).Yc, 2),
           round(circles.Circle(j).Radius, 3)) for j in range(circles.Count)]
    al = []     # дуги: центр, R, концы и средняя точка (layout.arc_angle)
    for j in range(arcs.Count):
        a = arcs.Arc(j)
        al.append(tuple(round(q, 3) for q in (a.Xc, a.Yc, a.Radius, a.X1, a.Y1,
                                               a.X2, a.Y2, a.X3, a.Y3)))
    bb = (round(min(xs), 1), round(min(ys), 1),
          round(max(xs), 1), round(max(ys), 1)) if xs else None
    dc_ = detail_region(v)
    if bb and dc_:      # выносной: в контейнере вся геометрия родителя — обрезать областью
        bb = clip_rect(bb, dc_)
    zones = break_zones(v) if bb else []
    shown = (bb[0], bb[1], _bk_u(zones, bb[2], 1), bb[3]) if zones else bb
    return {"lines": lines.Count, "arcs": arcs.Count, "circles": cs, "arc_list": al,
            "bbox": bb, "bbox_shown": shown}


def uncur(doc):
    """Сделать текущим системный вид + снять выделение (перед снимком/сдачей;
    zoom_all зовёт сам)."""
    kompas = api5()
    doc.Active = True
    kompas.ActiveDocument2D().ksOpenView(0)
    try:
        CastTo(doc, "IKompasDocument2D1").SelectionManager.UnselectAll()
    except Exception:
        pass


# ------------------------------------------------------------------- размеры
_GOST_SUBST = {"×": "х", "–": "-", "—": "-"}


def gost_text(s):
    """Символы, которые шрифт ГОСТ тип А рисует не так: «×» → «Ч» (CP1251 0xD7),
    «–»/«—» → пустой квадрат. Заменяются на кириллическую «х» и дефис.
    («α», «β» тоже квадраты — их заменять словами вручную.)"""
    for a, b in _GOST_SUBST.items():
        s = s.replace(a, b)
    return s


TIMES_FONT = "Arial"          # «×» (U+00D7): в шрифте ГОСТ тип А — «Ч» (CP1251 0xD7); Arial рисует ×
GOST_FONT = "GOST type A"     # шрифт размерных надписей и выносок по умолчанию (замер пилота PRJ.02)


_SPECIAL_SEGS = {"×": ("times",), "Ø": ("dia",), "⌀": ("dia",), "↧": ("depth",)}


def text_segs(s):
    """Строка → сегменты для text_segments: «×» → ("times",), «Ø»/«⌀» → ("dia",),
    «↧» → ("depth",), остальное — ("text", …). «⌴» (цековка) остаётся текстом: рабочего
    приёма для него нет (шрифт ГОСТ — квадрат, код Sign не снят; память
    suffix-symbols-and-ladder-rows)."""
    segs, buf = [], ""
    for ch in str(s):
        if ch in _SPECIAL_SEGS:
            if buf:
                segs.append(("text", buf))
                buf = ""
            segs.append(_SPECIAL_SEGS[ch])
        else:
            buf += ch
    if buf:
        segs.append(("text", buf))
    return segs


def set_text(target, s, font=None):
    """Строка в IText / ITextLine (префикс, суффикс, полка выноски). Со спецзнаками
    («×», «Ø», «↧» — text_segs) — элементами text_segments: «×» шрифтом TIMES_FONT, «Ø»
    спецзнаком, «↧» шрифтом DEPTH_FONT, остальное — font (по умолчанию GOST_FONT), шрифт
    явно у каждого элемента: КОМПАС сливает элемент «символ шрифта» со следующим текстом
    (задача 9; без этого «64=» после «×» печаталось Arial, проба PRJ.02). Без спецзнаков —
    gost_text, как раньше."""
    s = str(s)
    if not any(ch in s for ch in _SPECIAL_SEGS):
        target.Str = gost_text(s)
        return target
    return text_segments(target, text_segs(s), font=font or GOST_FONT)


def dev_parts(dev):
    """«+0,2» → («+0,2», «0»); «-0,1» → («0», «-0,1»); «±0,05» → («+0,05», «-0,05»);
    «+0,2/-0,1» → как есть (верхнее, нижнее)."""
    t = str(dev).replace("−", "-").replace(" ", "").replace(".", ",")
    if t.startswith("±"):
        return "+" + t[1:], "-" + t[1:]
    if "/" in t:
        hi, lo = t.split("/", 1)
        return hi, lo
    if t.startswith("-"):
        return "0", t
    return (t if t.startswith("+") else "+" + t), "0"


def set_deviations(dim, dev):
    """Предельные отклонения размера индексами (ГОСТ 2.307 п. 1.9): dev — «+0,2», «-0,1»,
    «±0,05» или «+0,2/-0,1» (верхнее/нижнее). Нулевое — не печатается (КОМПАС сам).
    IDimensionText.DeviationOn + HighDeviation/LowDeviation (ITextLine), DeviationType 0 —
    отклонения, не предельные размеры (проба PRJ.02: 1 — «940,05/939,95», 2 — «939,95–940,05»)."""
    hi, lo = dev_parts(dev)
    dt = CastTo(dim, "IDimensionText")
    dt.DeviationOn = True
    dt.DeviationType = 0
    dt.HighDeviation.Str = hi
    dt.LowDeviation.Str = lo
    CastTo(dim, "IDrawingObject").Update()
    return hi, lo


def split_dia_sign(prefix):
    """Префикс «Ø»/«⌀» у размера шрифт ГОСТ печатает «Ш» — значок ставится через
    IDimensionText.Sign=1. Возвращает (префикс без значка, нужен_ли_Sign)."""
    p = (prefix or "").rstrip()
    for ch in ("Ø", "⌀"):
        if p.endswith(ch):
            return p[:-1], True
    return prefix or "", False


def lin_orientation(orientation, x3, y3):
    """Ориентация линейного размера: явная — как есть; иначе по заданному вылету:
    только y3 → 1 (горизонтальный), только x3 → 2 (вертикальный)."""
    if orientation is not None:
        return orientation
    if y3 is not None and x3 is None:
        return 1
    if x3 is not None and y3 is None:
        return 2
    raise ValueError("lin: задать orientation (1 гориз., 2 верт.) или только один "
                     "из x3 (вертикальный) / y3 (горизонтальный)")


def lin(v, x1, y1, x2, y2, orientation=None, x3=None, y3=None, text=None,
        prefix="", suffix="", text_pos=0):
    """Линейный размер: две точки + вылет. y3 — положение горизонтальной
    размерной линии, x3 — вертикальной; orientation (1 гориз., 2 верт.) по
    умолчанию выводится из того, что задано. Вылет — ВНЕ контура (правило).
    Умолчания Э6: text=None → AutoNominalValue (номинал = геометрия), text_pos=0 —
    текст посередине размерной линии; другое TextPos — только для снятия
    наложения (Ч12). У горизонтального КОМПАС X3 не принимает, Y3 — принимает (Э0).
    prefix «Ø» → IDimensionText.Sign=1 (иначе печатается «Ш»)."""
    orientation = lin_orientation(orientation, x3, y3)
    sc = CastTo(v, "ISymbols2DContainer")
    d = sc.LineDimensions.Add()
    d.X1, d.Y1, d.X2, d.Y2 = float(x1), float(y1), float(x2), float(y2)
    d.Orientation = orientation
    if x3 is not None:
        d.X3 = float(x3)
    if y3 is not None:
        d.Y3 = float(y3)
        if x3 is None:
            d.X3 = float((x1 + x2) / 2)
    if y3 is None and x3 is not None:
        d.Y3 = float((y1 + y2) / 2)
    # TextPos и AutoNominalValue — всегда явно: сбойный процесс КОМПАС однажды
    # сменил умолчания Add() (задача 6)
    dp = CastTo(d, "IDimensionParams")
    if text_pos:            # сдвиг действует только при ручном размещении: TextType 1 ДО TextPos (замер .014)
        dp.TextType = 1
    dp.TextPos = int(text_pos)
    dt = CastTo(d, "IDimensionText")
    if text is None:
        dt.AutoNominalValue = True
    else:
        dt.AutoNominalValue = False
        dt.NominalText.Str = gost_text(text)
    prefix, sign = split_dia_sign(prefix)
    if sign:
        dt.Sign = 1
    if prefix:
        set_text(dt.Prefix, prefix)
    if suffix:
        set_text(dt.Suffix, suffix)
    CastTo(d, "IDrawingObject").Update()
    return d


DIA_SHELF_LEG = 10.0   # мм листа: ножка от окружности до полки (задача 14d)


def lin_shelf(dim, sx, sy):
    """Текст линейного размера — на полке с выноской (IDimensionParams.TextType=2, полка в
    ILineDimension.ShelfX/ShelfY, СК вида; опыт 2026-10-19 на вале: принято, КОМПАС сам
    пересчитал ShelfLength/ShelfAngle). Полка — от выноски в сторону sx."""
    dp = CastTo(dim, "IDimensionParams")
    dim.ShelfX, dim.ShelfY = float(sx), float(sy)
    dp.TextType = 2
    dp.ShelfDirection = shelf_dir(float(sx) - float(dim.X3))
    CastTo(dim, "IDrawingObject").Update()
    return dim


def shelf_dir(dx):
    """Направление горизонтальной полки (ksShelfDirectionEnum): 1 — вправо, -1 — влево.
    dx — проекция на ось X направления «от стрелки к полке» (cos угла выноски Ø или
    x полки − x стрелки у линии-выноски); вертикаль (|dx| ≈ 0) — вправо."""
    return -1 if dx < -1e-9 else 1


def dia(v, xc, yc, r, angle, prefix="", suffix="", text=None, no_sign=False,
        leg=DIA_SHELF_LEG):
    """Диаметральный размер; значок Ø ставится сам, номинал — авто (Э6).
    Надпись — ВСЕГДА на горизонтальной полке (ГОСТ 2.316, задача 14d): авто-размещение
    (TextType 0) клало текст вдоль наклонной линии при любом угле (живой замер 14d:
    315/225/45/135, «Ø4,5» повёрнут). TextType=2 + ShelfDirection по стороне angle
    (shelf_dir(cos)) + ShelfLength = leg мм листа: ножка идёт от окружности вдоль angle,
    затем полка. ShelfAngle КОМПАС пересчитывает сам (задавать бесполезно); TextPos=1
    после TextType=2 обнуляет ножку (1 мм) — не трогать. Сторона — через angle."""
    sc = CastTo(v, "ISymbols2DContainer")
    d = sc.DiametralDimensions.Add()
    d.Xc, d.Yc, d.Radius, d.Angle = float(xc), float(yc), float(r), float(angle)
    dp = CastTo(d, "IDimensionParams")
    dp.TextType = 2              # ksDTPOnShelf
    dp.ShelfDirection = shelf_dir(math.cos(math.radians(float(angle))))
    dp.ShelfLength = float(leg)
    dt = CastTo(d, "IDimensionText")
    if text is not None:
        dt.AutoNominalValue = False
        dt.NominalText.Str = gost_text(text)
    if prefix:
        set_text(dt.Prefix, prefix)
    if suffix:
        set_text(dt.Suffix, suffix)
    if no_sign:
        dt.Sign = 0
    CastTo(d, "IDrawingObject").Update()
    # Angle на НОВОМ диаметральном размере после первого Update читается 0 (память
    # dia-angle-reset-on-create, 2026-10-15): повторная установка держится — без неё все
    # полки уходят вправо (замер шаблона плиты)
    try:
        if abs(float(d.Angle) - float(angle) % 360.0) > 0.5:
            d.Angle = float(angle)
            dp.ShelfDirection = shelf_dir(math.cos(math.radians(float(angle))))
            CastTo(d, "IDrawingObject").Update()
    except Exception:
        pass
    return d


def dia_shelf_start(view, dim):
    """Бумажная точка начала полки Ø-размера с текстом на полке (TextType 2): конец ножки —
    точка окружности под углом Angle плюс ShelfLength мм листа вдоль того же направления."""
    dp = CastTo(dim, "IDimensionParams")
    a = math.radians(float(dim.Angle))
    r = float(dim.Radius)
    leg = float(dp.ShelfLength) / view.Scale
    x, y = dim.Xc + (r + leg) * math.cos(a), dim.Yc + (r + leg) * math.sin(a)
    px, py = view_to_paper(view, x, y)
    return px, py, int(dp.ShelfDirection)


RAD_ARROW_POS = 1   # ksDimArrowOutside: стрелка снаружи, остриё на дуге к центру (Р19).
                    # 0 (Inside) — стрелка ВНУТРИ дуги остриём наружу, линия идёт сквозь
                    # дугу к центру, хвост ложится на соосную окружность (R10 ушка, задача 15b)


def rad(v, xc, yc, r, angle, prefix="", shelf=None, suffix=""):
    """Радиальный размер. ArrowPos=RAD_ARROW_POS — остриё на дуге снаружи, вдоль
    радиуса к центру, число снаружи (Р19). Номинал авто, сторона текста —
    через angle (TextPos не принимается, читается 6 — Э6).
    shelf — мелкий R при крупном масштабе (текст ложится ВНУТРЬ контура):
    TextType=2 (на полке) + ShelfDirection=shelf: 1 вправо, -1 влево, 2 вверх,
    3 вниз-влево; полка наружу от острия. Проверять D.gabarits."""
    sc = CastTo(v, "ISymbols2DContainer")
    d = sc.RadialDimensions.Add()
    d.Xc, d.Yc, d.Radius, d.Angle = float(xc), float(yc), float(r), float(angle)
    d.DimensionType = False
    try:
        dp = CastTo(d, "IDimensionParams")
        dp.ArrowPos = RAD_ARROW_POS
        if shelf is not None:
            dp.TextType = 2          # ksDTPOnShelf
            dp.ShelfDirection = int(shelf)
    except Exception:
        if shelf is not None:
            raise
    if prefix or suffix:
        dt = CastTo(d, "IDimensionText")
        if prefix:
            set_text(dt.Prefix, prefix)
        if suffix:
            set_text(dt.Suffix, suffix)
    CastTo(d, "IDrawingObject").Update()
    return d


LEADER_ARROWS = {"none": 0, "dot": 1, "arrow": 2}   # IBaseLeader.ArrowType (Р2 plate_leap, по PNG)


def leader(v, x0, y0, xt, yt, text, under=None, arrow=None):
    """Линия-выноска: полка в (x0,y0) — ЦЕЛИКОМ вне контура, стрелка в (xt,yt).
    Полка горизонтальная, ОТ стрелки (shelf_dir(x0 − xt)): умолчание КОМПАС — всегда
    вправо (замер 14d), и при полке левее стрелки текст ложился на ветвь.
    under — текст под полкой (ILeader.TextUnderShelf); arrow — «arrow» | «dot» (точка на
    поверхности: толщина «s 10» эталона) | «none» (IBaseLeader.ArrowType).
    Ч11: «отв.» с Ø на полке — FAIL; отверстия — линейным размером или «М..-6H»."""
    sc = CastTo(v, "ISymbols2DContainer")
    l = sc.Leaders.Add(20)  # ksDrLeader
    br = CastTo(l, "IBranchs")
    br.X0, br.Y0 = float(x0), float(y0)
    br.AddBranchByPoint(0, float(xt), float(yt))
    CastTo(l, "ILeader").ShelfDirection = shelf_dir(float(x0) - float(xt))
    set_text(CastTo(l, "ILeader").TextOnShelf, text)
    if under:
        set_text(CastTo(l, "ILeader").TextUnderShelf, under)
    if arrow is not None:
        CastTo(l, "IBaseLeader").ArrowType = LEADER_ARROWS.get(arrow, arrow) if isinstance(arrow, str) else int(arrow)
    CastTo(l, "IDrawingObject").Update()
    return l


def segment(v_or_dc, x1, y1, x2, y2, style=1):
    """Отрезок. style: 1 основная, 2 тонкая, 3 осевая. Осевые и обозначения
    центров КОМПАС строит в видах сам (Э7, ISymbols2DContainer.AxisLines):
    style=3 — только для осей симметрии и того, чего нет в AxisLines вида
    (13 из 77 ручных осей в старых чертежах оказались дублями)."""
    dc = v_or_dc if hasattr(v_or_dc, "LineSegments") else CastTo(v_or_dc, "IDrawingContainer")
    L = dc.LineSegments.Add()
    L.X1, L.Y1, L.X2, L.Y2 = float(x1), float(y1), float(x2), float(y2)
    L.Style = style
    CastTo(L, "IDrawingObject").Update()
    return L


# --------------------------------------------- оси симметрии и центры (задача 15a)
AXIS_JUT = 3.0           # мм листа: выступ осевых и центровых за контур (ГОСТ 2.303: 2–5;
                         # умолчание КОМПАС 2 — на глаз 1,2–1,8, задача 15b)
SYM_AXIS_OVERRUN = AXIS_JUT   # оси симметрии (отрезки стиля 3) — тот же выступ
SYM_MIN_SIDE = 10.0      # мм листа: вид уже — без осей (пилот: торец 6 мм в 1:2,5 — без осей)


def _prim_samples(p):
    if p[0] == "seg":
        _, x1, y1, x2, y2 = p
        return [(x1, y1), (x2, y2), ((x1 + x2) / 2, (y1 + y2) / 2)]
    if p[0] == "circ":
        _, xc, yc, r = p
        return [(xc + r, yc), (xc - r, yc), (xc, yc + r), (xc, yc - r)]
    _, xc, yc, r, a1, sw = p
    return [(xc + r * math.cos(math.radians(a1 + sw * k / 4)),
             yc + r * math.sin(math.radians(a1 + sw * k / 4))) for k in range(5)]


def _prim_dist(p, x, y):
    if p[0] == "seg":
        _, x1, y1, x2, y2 = p
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
        return math.hypot(x1 + t * dx - x, y1 + t * dy - y)
    if p[0] == "circ":
        return abs(math.hypot(x - p[1], y - p[2]) - p[3])
    _, xc, yc, r, a1, sw = p
    if (math.degrees(math.atan2(y - yc, x - xc)) - a1) % 360.0 <= sw + 1e-6:
        return abs(math.hypot(x - xc, y - yc) - r)
    return min(math.hypot(x - px, y - py) for px, py in _prim_samples(p)[::4])


def _prim_bbox(prims):
    xs, ys = [], []
    for p in prims:
        if p[0] == "circ":
            xs += [p[1] - p[3], p[1] + p[3]]
            ys += [p[2] - p[3], p[2] + p[3]]
            continue
        pts = _prim_samples(p)
        if p[0] == "arc":      # крайние точки дуги по осям, если попадают в её угол
            _, xc, yc, r, a1, sw = p
            pts += [(xc + r * math.cos(math.radians(q)), yc + r * math.sin(math.radians(q)))
                    for q in (0, 90, 180, 270) if (q - a1) % 360.0 <= sw]
        xs += [q[0] for q in pts]
        ys += [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def symmetry_axes(prims, scale, have=(), tol=0.05):
    """Оси симметрии вида по его геометрии (СК вида, мм модели): горизонталь и вертикаль
    через центр габарита, если зеркало каждой точки геометрии лежит на геометрии (±tol).
    prims: ("seg", x1,y1,x2,y2) | ("circ", xc,yc,r) | ("arc", xc,yc,r, a1°, угол°) —
    дуга против часовой от a1. Ось — на SYM_AXIS_OVERRUN мм листа за габарит; вид уже
    SYM_MIN_SIDE мм листа — без осей; ось, уже лежащая в have (AxisLines/стиль 3), не
    повторяется (Э7: дубли). Ответ: [(x1, y1, x2, y2)], сначала горизонтальная."""
    if not prims:
        return []
    x0, y0, x1, y1 = _prim_bbox(prims)
    if min(x1 - x0, y1 - y0) * scale < SYM_MIN_SIDE:
        return []
    cx, cy, o = (x0 + x1) / 2, (y0 + y1) / 2, SYM_AXIS_OVERRUN / scale
    pts = [q for p in prims for q in _prim_samples(p)]

    def mirrored_ok(f):
        return all(min(_prim_dist(p, *f(x, y)) for p in prims) <= tol for x, y in pts)

    def present(horizontal, c):
        k = 1 if horizontal else 0
        return any(abs(a[k] - c) <= 0.3 and abs(a[k + 2] - c) <= 0.3 for a in have)

    out = []
    if not present(True, cy) and mirrored_ok(lambda x, y: (x, 2 * cy - y)):
        out.append((round(x0 - o, 2), round(cy, 2), round(x1 + o, 2), round(cy, 2)))
    if not present(False, cx) and mirrored_ok(lambda x, y: (2 * cx - x, y)):
        out.append((round(cx, 2), round(y0 - o, 2), round(cx, 2), round(y1 + o, 2)))
    return out


def view_prims(v):
    """Геометрия вида для symmetry_axes (без осевых отрезков стиля 3). Координаты сырые — и на
    виде с разрывом (оси и кресты строятся в них, КОМПАС переносит их вместе с геометрией)."""
    dc = CastTo(v, "IDrawingContainer")
    prims = []
    for j in range(dc.LineSegments.Count):
        L = dc.LineSegments.LineSegment(j)
        if L.Style != 3:
            prims.append(("seg", L.X1, L.Y1, L.X2, L.Y2))
    for j in range(dc.Circles.Count):
        c = dc.Circles.Circle(j)
        prims.append(("circ", c.Xc, c.Yc, c.Radius))
    for j in range(dc.Arcs.Count):
        a = dc.Arcs.Arc(j)
        prims.append(("arc", a.Xc, a.Yc, a.Radius)
                     + _rules().arc_sweep(a.Xc, a.Yc, a.X1, a.Y1, a.X2, a.Y2, a.X3, a.Y3))
    return prims


def view_axes(v):
    """Осевые вида в СК вида: AxisLines (автоосевые КОМПАС) + отрезки стиля 3."""
    sc = CastTo(v, "ISymbols2DContainer")
    out = [(a.X1, a.Y1, a.X2, a.Y2) for a in
           (sc.AxisLines.AxisLine(j) for j in range(sc.AxisLines.Count))]
    dc = CastTo(v, "IDrawingContainer")
    out += [(L.X1, L.Y1, L.X2, L.Y2) for L in
            (dc.LineSegments.LineSegment(j) for j in range(dc.LineSegments.Count)) if L.Style == 3]
    return out


def axis_gaps(axes, tol=0.3):
    """Разрывы между соосными осевыми (горизонтальными или вертикальными, на одной прямой
    ±tol): [(x1, y1, x2, y2)] — перемычки. Автоосевая КОМПАС вала рвётся на участке резьбы
    (Вал_v6: 0…457,2 и 505…579), а по ГОСТ 2.303 ось тела вращения — через весь контур.
    Перемычка — только если разрыв не длиннее суммы соседних отрезков (не связывать чужие оси)."""
    out = []
    for k in (0, 1):             # 0 — горизонтали (общий y), 1 — вертикали (общий x)
        c, a = (1, 0) if k == 0 else (0, 1)
        segs = [(min(q[a], q[a + 2]), max(q[a], q[a + 2]), (q[c] + q[c + 2]) / 2) for q in axes
                if abs(q[c] - q[c + 2]) <= tol and abs(q[a] - q[a + 2]) > tol]
        lines = []
        for lo, hi, w in sorted(segs, key=lambda t: t[2]):
            if lines and abs(lines[-1][0] - w) <= tol:
                lines[-1][1].append((lo, hi))
            else:
                lines.append([w, [(lo, hi)]])
        for w, iv in lines:
            iv.sort()
            lo0, hi0 = iv[0]
            for lo, hi in iv[1:]:
                gap = lo - hi0
                if gap > tol and gap <= (hi0 - lo0) + (hi - lo):
                    p, q = round(hi0, 2), round(lo, 2)
                    out.append((p, round(w, 2), q, round(w, 2)) if k == 0 else (round(w, 2), p, round(w, 2), q))
                if hi > hi0:
                    lo0, hi0 = (lo, hi) if gap > tol else (lo0, hi)
    return out


def add_symmetry_axes(v):
    """Оси симметрии проекционного вида отрезками стиля 3 (КОМПАС их не строит, Э7;
    обозначения центров отверстий — строит) и перемычки в разрывах соосных осевых
    (axis_gaps). Ответ — добавленные отрезки (СК вида)."""
    axes = symmetry_axes(view_prims(v), v.Scale, have=view_axes(v))
    for a in axes:
        segment(v, *a, style=3)
    gaps = axis_gaps(view_axes(v))
    for a in gaps:
        segment(v, *a, style=3)
    return axes + gaps


def force_axes(v, which=("h", "v")):
    """Оси симметрии вида через центр габарита геометрии принудительно (spec views[].axes):
    плита с одним несимметричным отверстием не проходит зеркальную проверку symmetry_axes, а
    инженер оси ставит (эталон ACME.100.04.00.001). Уже имеющаяся ось (AxisLines/стиль 3 через
    центр) не дублируется. Ответ — добавленные оси (СК вида)."""
    prims = view_prims(v)
    if not prims:
        return []
    x0, y0, x1, y1 = _prim_bbox(prims)
    cx, cy, o = (x0 + x1) / 2, (y0 + y1) / 2, SYM_AXIS_OVERRUN / v.Scale
    have = view_axes(v)
    out = []
    for w in which:
        if w == "h" and not any(abs(a[1] - cy) <= 0.3 and abs(a[3] - cy) <= 0.3 for a in have):
            out.append((round(x0 - o, 2), round(cy, 2), round(x1 + o, 2), round(cy, 2)))
        if w == "v" and not any(abs(a[0] - cx) <= 0.3 and abs(a[2] - cx) <= 0.3 for a in have):
            out.append((round(cx, 2), round(y0 - o, 2), round(cx, 2), round(y1 + o, 2)))
    for a in out:
        segment(v, *a, style=3)
    return out


def set_axis_jut(v, jut=AXIS_JUT):
    """Выступ jut мм листа у осевых КОМПАС вида (AxisLines — оси отверстий в разрезах)
    и обозначений центра (CentreMarkers): IAxisLineParam.JutLength (задание сбрасывает
    JutLengthModify в True — своё значение вместо настроек документа; живой замер 15b:
    2 → 3 мм на 300 dpi). Ответ — число исправленных объектов."""
    sc = CastTo(v, "ISymbols2DContainer")
    objs = [sc.AxisLines.AxisLine(j) for j in range(sc.AxisLines.Count)]
    objs += [sc.CentreMarkers.CentreMarker(j) for j in range(sc.CentreMarkers.Count)]
    for o in objs:
        CastTo(o, "IAxisLineParam").JutLength = float(jut)
        CastTo(o, "IDrawingObject").Update()
    return len(objs)


def view_markers(v):
    """Обозначения центра вида: [(x, y, SignType)] в СК вида (Ч16: крест — только 2)."""
    sc = CastTo(v, "ISymbols2DContainer")
    return [(m.X, m.Y, m.SignType) for m in
            (sc.CentreMarkers.CentreMarker(j) for j in range(sc.CentreMarkers.Count))]


def add_centre_marks(v):
    """Обозначение центра (две оси) окружностям и дугам контура > ARC_MIN_SWEEP вида,
    у которых нет креста в обе стороны: маркера «две оси» или двух пересекающихся осей
    через всю окружность (Ч16, R.centre_covered; одна ось симметрии через отверстие —
    не крест). Обычно пусто: отверстиям
    КОМПАС ставит маркеры сам (CreateCentresMarkers, Э7), центр контура покрывают оси
    симметрии — звать ПОСЛЕ add_symmetry_axes. База маркера — сама окружность/дуга
    (длина полуосей авто). Ответ — центры добавленных маркеров (СК вида)."""
    R = _rules()
    sc = CastTo(v, "ISymbols2DContainer")
    dc = CastTo(v, "IDrawingContainer")
    marks = view_markers(v)
    axes = view_axes(v)
    bases = [c for c in (dc.Circles.Circle(j) for j in range(dc.Circles.Count))
             if c.Style != 11]   # 11 — граница выносного элемента, не контур (замер .014)
    bases += [a for a in (dc.Arcs.Arc(j) for j in range(dc.Arcs.Count))
              if R.arc_sweep(a.Xc, a.Yc, a.X1, a.Y1, a.X2, a.Y2, a.X3, a.Y3)[1] > R.ARC_MIN_SWEEP]
    reg = detail_region(v)        # выносной: в контейнере вся геометрия родителя — только центры в зоне
    if reg:                       # (сборка .014: крест центра торца рисовался в выносном Г за его зоной)
        bases = [b for b in bases if reg[0] <= b.Xc <= reg[2] and reg[1] <= b.Yc <= reg[3]]
    added = []
    # крест — по наибольшему из концентрических контуров (ревьюер .014: автомаркер КОМПАС стоит
    # на отверстии Ø5,3 и не выходит за наружный Ø9; ГОСТ 2.303 — центровые за контур на 2–5 мм)
    for j in range(sc.CentreMarkers.Count):
        m = sc.CentreMarkers.CentreMarker(j)
        conc = [b for b in bases if abs(b.Xc - m.X) < 0.05 and abs(b.Yc - m.Y) < 0.05]
        if len(conc) > 1:
            try:
                m.BaseObject = CastTo(max(conc, key=lambda b: b.Radius), "IDrawingObject")
                CastTo(m, "IDrawingObject").Update()
            except Exception:
                pass
    for b in bases:
        x, y = b.Xc, b.Yc
        if R.centre_covered(x, y, marks, axes, r=b.Radius):
            continue
        m = CastTo(sc.CentreMarkers.Add(), "ICentreMarker")
        m.BaseObject = CastTo(b, "IDrawingObject")
        m.SignType = 2            # ksCMTwoAxis — как у автомаркеров и в пилоте
        # X/Y НЕ задавать: с BaseObject маркер сам встаёт в центр базовой кривой, а явный X
        # КОМПАС умножает на масштаб вида (Y — нет): маркеры дуг прорезей плиты уезжали на
        # x·s, в выносном 1:5 — на лист 1 (живой замер 2026-10-15, задача 16)
        CastTo(m, "IDrawingObject").Update()
        marks.append((x, y, 2))
        added.append((round(x, 2), round(y, 2)))
    return added


def text(v_or_dc, x, y, s, h=5.0):
    """Текст. Высота: IDrawingText.Height не применяется — задаётся через
    ITextFont.Height каждого TextItem + TextItem.Update() (без Update молча 5)."""
    dc = v_or_dc if hasattr(v_or_dc, "DrawingTexts") else CastTo(v_or_dc, "IDrawingContainer")
    t = dc.DrawingTexts.Add()
    t.X, t.Y = float(x), float(y)
    try:
        t.Height = float(h)
    except Exception:
        pass
    it = CastTo(t, "IText")
    it.Str = gost_text(s)
    for k in range(it.Count):
        tl = it.TextLine(k)
        for m in range(tl.Count):
            ti = tl.TextItem(m)
            CastTo(ti, "ITextFont").Height = float(h)
            ti.Update()
    CastTo(t, "IDrawingObject").Update()
    return t


DEPTH_FONT = "Cambria Math"   # «↧» (U+21A7): в шрифте ГОСТ — пустой квадрат (задача 9)


def text_segments(target, segs, height=None, font=None):
    """Заменить текст одной строкой сегментов. target — IText (текст) или ITextLine
    (IDimensionText.Prefix/Suffix — это СТРОКА, у неё нет TextLine). Сегменты:
    ("text", s) — текст (gost_text) шрифтом font; ("depth",) — знак глубины «↧»
    (U+21A7) отдельным элементом шрифта DEPTH_FONT: в шрифте ГОСТ его нет (квадрат),
    среди спецзнаков 0–279 тоже нет (растр-перебор, задача 9); ("dia",) — «Ø»
    спецзнаком 2 (ItemType 0x11; таблица SDK NumbSymb.frw); ("times",) — «×» шрифтом
    TIMES_FONT (в шрифте ГОСТ — «Ч»).
    Пример: глухая резьба «↧8 / Ø3,3↧11». font/height — шрифт и высота размерной
    надписи: элемент «символ шрифта» (ItemType 0x17) КОМПАС сливает со следующим
    текстом и рисует его тем же шрифтом (задача 9), поэтому шрифт задаётся КАЖДОМУ
    элементу явно."""
    target.Str = ""
    if hasattr(target, "TextLine"):                     # IText
        ln = target.TextLine(0) if target.Count else target.Add()
    else:                                               # ITextLine
        ln = target
    for seg in segs:
        it = ln.Add()
        f = CastTo(it, "ITextFont")
        if seg[0] == "dia":
            it.ItemType = 0x11
            it.Number = 2
        elif seg[0] == "depth":
            it.Str = "↧"
            f.FontName = DEPTH_FONT
        elif seg[0] == "times":
            it.Str = "×"
            f.FontName = TIMES_FONT
        else:
            it.Str = gost_text(seg[1])
            if font:
                f.FontName = font
        if height:
            f.Height = float(height)
        it.Update()
    return target


# --------------------------------------------------- разрезы и выносные элементы
def cutline(parent_view, x1, y1, x2, y2, letter):
    """Секущая. Штрихи/стрелки/буквы НЕ должны касаться контура (правило) —
    задавать точки с зазором от вида."""
    sc = CastTo(parent_view, "ISymbols2DContainer")
    cl = sc.CutLines.Add()
    cl.Points = [float(x1), float(y1), float(x2), float(y2)]
    try:
        cl.Text.Str = letter
    except Exception:
        pass
    CastTo(cl, "IDrawingObject").Update()
    return cl


def section_view(doc, cl, x, y, scale, angle=None, show_scale=False):
    """Ассоциативный разрез по секущей. show_scale=True если масштаб ≠ штампу
    (тогда в заголовке «А-А (1:1)»)."""
    nv = views_of(doc).Add(7)
    av = CastTo(nv, "IAssociationView")
    av.BaseObject = CastTo(cl, "IDrawingObject")
    av.X, av.Y = float(x), float(y)
    av.Scale = float(scale)
    if angle is not None:
        av.Angle = float(angle)
    av.Update()
    try:
        CastTo(nv, "IViewDesignation").ShowScale = show_scale
        if angle is not None:
            nv.Angle = float(angle)
        CastTo(nv, "IDrawingObject").Update()
    except Exception:
        pass
    return nv


def remote(parent_view, xc, yc, radius_paper, shelf_x, shelf_y, letter, form="circle",
           w_paper=None, h_paper=None):
    """Обозначение выносного элемента — ТОЛЬКО так, не ручной отрисовкой
    (правило пользователя, итерация 6 пилота). form «circle» — круг Radius (мм листа);
    «rect» — прямоугольник Width×Height (мм ЛИСТА, ksContourFormEnum 1; Э5 plate_leap:
    эталон обводит зоны Б 117×53 и А 27×27 прямоугольниками)."""
    sc = CastTo(parent_view, "ISymbols2DContainer")
    re = sc.RemoteElements.Add()
    re.Xc, re.Yc = float(xc), float(yc)
    if form == "rect":
        re.Form = 1
        re.Width, re.Height = float(w_paper), float(h_paper)
    else:
        re.Radius = float(radius_paper)
    re.ShelfX, re.ShelfY = float(shelf_x), float(shelf_y)
    try:
        re.TextUp.Str = letter
    except Exception:
        pass
    CastTo(re, "IDrawingObject").Update()
    return re


TITLE_H = 10.0   # высота шрифта заголовка вида (поля «А», «(1:5)» — 10 мм, замер 2026-10-15)

# Заголовок разреза/выносного по закону серии (план v5 П1): «А-А (1:1)», «Б (4:1)», «4 места В (4:1)»
VIEW_TITLE_RE = re.compile(r"^(\d+ мест[а]? )?[А-Я](-[А-Я])?( \(\d+(,\d+)?:\d+\))?$")


def scale_text(scale):
    """Масштаб в текст заголовка: 4.0 → «4:1», 0.4 → «1:2,5», строка «1:1» — как есть."""
    if isinstance(scale, str):
        return scale.strip()
    s = float(scale)
    k = s if s >= 1.0 else 1.0 / s
    t = f"{k:g}".replace(".", ",")
    return f"{t}:1" if s >= 1.0 else f"1:{t}"


def view_title(letter, scale=None, prefix=None):
    """Текст заголовка разреза/выносного: буква («А» или «А-А»), масштаб в скобках через
    ПРОБЕЛ (ГОСТ 2.316; замечания A14/A38 «А-А(2:1)»), префикс повтора «4 места» перед
    буквой. На листе заголовок формирует КОМПАС (IViewDesignation.ShowScale + title_prefix);
    здесь — эталон для приёмки по VIEW_TITLE_RE."""
    t = letter.strip()
    if scale is not None:
        t += f" ({scale_text(scale)})"
    if prefix:
        t = prefix.strip() + " " + t
    return t


def title_prefix(view, text):
    """Текст перед буквой в заголовке выносного/разреза («2 места А (1:5)», ГОСТ 2.305 —
    повторяющийся узел): ITextLine.AddBefore(0) в DrawingText обозначения вида, затем
    высоту ВСЕХ компонент вернуть к TITLE_H — любая правка строки сбрасывает её 10 → 5
    (живой замер 2026-10-15; IView.Name на заголовок не влияет). Возвращает обозначение."""
    vd = CastTo(view, "IViewDesignation")
    dt = vd.DrawingText
    t = CastTo(dt, "IText")
    tl = t.TextLine(0) if t.Count else t.Add()
    heights = []
    for j in range(tl.Count):
        try:
            heights.append(CastTo(tl.TextItem(j), "ITextFont").Height)
        except Exception:
            pass
    h = max(heights) if heights else TITLE_H
    new = tl.AddBefore(0)
    new.Str = gost_text(text.rstrip()) + " "
    for j in range(tl.Count):
        it = tl.TextItem(j)
        try:
            CastTo(it, "ITextFont").Height = float(h)
            it.Update()
        except Exception:
            pass
    CastTo(dt, "IDrawingObject").Update()
    return vd.Designation


def set_title_height(view, h=TITLE_H):
    """Высота всех компонент заголовка вида = h (после title_prefix её сбрасывает одна из
    последующих операций сборки до 5 — финальный проход в server._build_sheet; после
    lift/Save/rebuild высота держится, замер 2026-10-15)."""
    vd = CastTo(view, "IViewDesignation")
    t = CastTo(vd.DrawingText, "IText")
    n = 0
    for i in range(t.Count):
        tl = t.TextLine(i)
        for j in range(tl.Count):
            it = tl.TextItem(j)
            CastTo(it, "ITextFont").Height = float(h)
            it.Update()
            n += 1
    CastTo(vd.DrawingText, "IDrawingObject").Update()
    return n


# Буква выносного на полке: прямоугольник от точки полки (ShelfX/Y) вправо-вверх,
# бумажные мм, не зависит от масштаба и стороны полки (замер v23, задача 14c:
# полка (110,155) → габарит до (120,3; 170,3); полка слева/снизу — то же смещение).
SHELF_BOX_W, SHELF_BOX_H = 10.3, 15.3


def arc_points(xc, yc, r, a1, a2, n=12):
    """n+1 точек дуги от угла a1 до a2 (рад, против часовой)."""
    while a2 <= a1:
        a2 += 2 * math.pi
    return [(xc + r * math.cos(a1 + (a2 - a1) * k / n), yc + r * math.sin(a1 + (a2 - a1) * k / n))
            for k in range(n + 1)]


def _polyline(pts):
    return [(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]) for k in range(len(pts) - 1)]


def odd_crossings(x, y, segs):
    """Луч из (x, y) вправо пересекает отрезки нечётное число раз — точка внутри
    замкнутого контура (у разреза — на штриховке)."""
    n = 0
    for x1, y1, x2, y2 in segs:
        if (y1 > y) != (y2 > y):
            if x1 + (y - y1) * (x2 - x1) / (y2 - y1) > x:
                n += 1
    return n % 2 == 1


def detail_shelf(xc, yc, r, segs, bbox, w=SHELF_BOX_W, h=SHELF_BOX_H, gap=1.0, avoid=()):
    """Полка буквы выносного, бумажные мм: (sx, sy). Круг (xc, yc, r) и отрезки
    геометрии родительского вида segs, его bbox — на листе. Умолчание (как раньше) —
    по диагонали вправо-вверх на 5 мм за кругом; оно остаётся, если буква не задевает
    геометрию и не лежит внутри контура (штриховка разреза). Иначе — лучший из
    кандидатов: справа-вверху/справа-внизу/слева-вверху/слева-внизу на 5…20 мм;
    порядок: не пересекает линии → не внутри контура → не по стороне avoid
    («top»/«bottom»/«left»/«right» — там встанут размеры вида) → вне bbox → ближе.
    Кандидаты не кладут ветвь на букву (буква — вправо-вверх от точки полки)."""
    def score(sx, sy):
        box = (sx - gap, sy - gap, sx + w + gap, sy + h + gap)
        cross = any(_rules().seg_rect_cross(s_, box) for s_ in segs)
        inside = odd_crossings(sx + w / 2, sy + h / 2, segs)
        in_bb = bool(bbox) and _rules().rects_cross(box, bbox)
        sides = set()
        if bbox:
            sides = {s_ for s_, ok in (("bottom", box[3] <= bbox[1]), ("top", box[1] >= bbox[3]),
                                       ("left", box[2] <= bbox[0]), ("right", box[0] >= bbox[2])) if ok}
        return int(cross), int(inside), int(bool(sides & set(avoid))), int(in_bb)

    cands = []
    for d in (5.0, 10.0, 15.0, 20.0):
        cands += [(xc + r + d, yc + r + d), (xc + r + d, yc - r - d - h),
                  (xc - r - d - w, yc + r + d), (xc - r - d - w, yc - r - d - h)]
    if score(*cands[0])[:2] == (0, 0):
        return cands[0]
    best = min(range(len(cands)), key=lambda k: (score(*cands[k]), k))
    return cands[best]


def view_segments(v, n_arc=12):
    """Отрезки геометрии вида (линии + дуги и окружности ломаными) на ЛИСТЕ, мм."""
    dc = CastTo(v, "IDrawingContainer")
    segs = []
    zones = break_zones(v)
    lines, arcs, circles = dc.LineSegments, dc.Arcs, dc.Circles
    for j in range(lines.Count):
        L = lines.LineSegment(j)
        segs += _bk_seg(zones, L.X1, L.Y1, L.X2, L.Y2)
    if zones:                     # дуги и окружности — со сдвигом центра (в зоне — выпадают)
        for j in range(arcs.Count):
            a = arcs.Arc(j)
            xc = break_u(zones, a.Xc)
            if xc is None:
                continue
            a1 = math.atan2(a.Y1 - a.Yc, a.X1 - a.Xc)
            a2 = math.atan2(a.Y2 - a.Yc, a.X2 - a.Xc)
            a3 = math.atan2(a.Y3 - a.Yc, a.X3 - a.Xc)
            if (a3 - a1) % (2 * math.pi) > (a2 - a1) % (2 * math.pi):
                a1, a2 = a2, a1
            segs += _polyline(arc_points(xc, a.Yc, a.Radius, a1, a2, n_arc))
        for j in range(circles.Count):
            c = circles.Circle(j)
            xc = break_u(zones, c.Xc)
            if xc is not None:
                segs += _polyline(arc_points(xc, c.Yc, c.Radius, 0.0, 2 * math.pi, 2 * n_arc))
        out = []
        for x1, y1, x2, y2 in segs:
            p1, p2 = view_to_paper(v, x1, y1), view_to_paper(v, x2, y2)
            out.append((p1[0], p1[1], p2[0], p2[1]))
        return out
    for j in range(arcs.Count):
        a = arcs.Arc(j)
        a1 = math.atan2(a.Y1 - a.Yc, a.X1 - a.Xc)
        a2 = math.atan2(a.Y2 - a.Yc, a.X2 - a.Xc)
        a3 = math.atan2(a.Y3 - a.Yc, a.X3 - a.Xc)
        if (a3 - a1) % (2 * math.pi) > (a2 - a1) % (2 * math.pi):   # дуга идёт по часовой
            a1, a2 = a2, a1
        segs += _polyline(arc_points(a.Xc, a.Yc, a.Radius, a1, a2, n_arc))
    for j in range(circles.Count):
        c = circles.Circle(j)
        segs += _polyline(arc_points(c.Xc, c.Yc, c.Radius, 0.0, 2 * math.pi, 2 * n_arc))
    out = []
    for x1, y1, x2, y2 in segs:
        p1, p2 = view_to_paper(v, x1, y1), view_to_paper(v, x2, y2)
        out.append((p1[0], p1[1], p2[0], p2[1]))
    return out


def paper_to_view(view, px, py):
    """Обратное view_to_paper: бумажные мм → координаты вида."""
    s = view.Scale
    a = math.radians(getattr(view, "Angle", 0.0) or 0.0)
    c, n = math.cos(a), math.sin(a)
    dx, dy = (px - view.X) / s, (py - view.Y) / s
    return dx * c + dy * n, -dx * n + dy * c


def hatch_style(v, style):
    """Стиль ВСЕХ штриховок вида (IHatchParam.Style): 1 — неметалл «в клетку»
    ГОСТ 2.306 (память чертёжника nonmetal-hatch-and-plan-section-layout; держится
    после RebuildDocument и переоткрытия — задача 14c). Возвращает число штриховок."""
    hs = CastTo(v, "IDrawingContainer").Hatches
    for j in range(hs.Count):
        h = hs.Hatch(j)
        CastTo(h, "IHatchParam").Style = int(style)
        CastTo(h, "IDrawingObject").Update()
    return hs.Count


def hatch_step(v, step):
    """Шаг ВСЕХ штриховок вида, мм листа (IHatchParam.Step; умолчание 2). ГОСТ 2.306: 1…10 мм
    по площади — на крупном сечении (вал Ø76 в 1:1) шаг 2 прячет центровые (ревьюер Вал_v5);
    4 держится после RebuildDocument (опыт probe_B). Возвращает число штриховок."""
    hs = CastTo(v, "IDrawingContainer").Hatches
    for j in range(hs.Count):
        h = hs.Hatch(j)
        CastTo(h, "IHatchParam").Step = float(step)
        CastTo(h, "IDrawingObject").Update()
    return hs.Count


def wave_line(v, x, y, half, angle=90.0, style=2):
    """Волнистая линия обрыва (ГОСТ 2.303/2.305) в СК вида: центр (x, y), длина 2·half (мм
    модели), угол к оси X вида. IWaveLine: X1/Y1 — начало, Angle, Length, Style (опыт probe_B:
    встала, пережила RebuildDocument; амплитуда — из настроек документа)."""
    wl = CastTo(v, "ISymbols2DContainer").WaveLines.Add()
    a = math.radians(angle)
    wl.X1, wl.Y1 = float(x) - half * math.cos(a), float(y) - half * math.sin(a)
    wl.Angle, wl.Length, wl.Style = float(angle), 2.0 * float(half), int(style)
    CastTo(wl, "IDrawingObject").Update()
    return wl


def detail_view(doc, re, x, y, scale, angle=None, show_scale=True):
    """Ассоциативный выносной вид по RemoteElement. Масштаб крупный (10:1 для
    мелочи), show_scale=True — «Б (10:1)». Чтобы вырезка не захватывала
    лишнее, родительский вид должен быть крупного масштаба."""
    nv = views_of(doc).Add(6)
    av = CastTo(nv, "IAssociationView")
    av.BaseObject = CastTo(re, "IDrawingObject")
    av.X, av.Y = float(x), float(y)
    av.Scale = float(scale)
    if angle is not None:
        av.Angle = float(angle)
    av.Update()
    try:
        CastTo(nv, "IViewDesignation").ShowScale = show_scale
        if angle is not None:
            nv.Angle = float(angle)
        CastTo(nv, "IDrawingObject").Update()
    except Exception:
        pass
    return nv


def local_view(doc, view, xc, yc, r):
    """Местный вид: обрезать ассоциативный вид окружностью (xc, yc, r) в СК
    этого же вида — CreateLocalView + RebuildDocument (Э2). Update() вида после
    этого = False — не критерий; итог — IAssociationView.Local. Местный РАЗРЕЗ
    (ICutViewParam.AddCut) не работает (−1 в 7 вариантах) — разрезы только
    cutline + section_view."""
    c = CastTo(view, "IDrawingContainer").Circles.Add()
    c.Xc, c.Yc, c.Radius = float(xc), float(yc), float(r)
    c.Style = 1
    CastTo(c, "IDrawingObject").Update()
    av = CastTo(view, "IAssociationView")
    ok = av.CreateLocalView(c)
    rebuild(doc)
    return bool(ok) and bool(av.Local)


def local_view_rect(doc, view, x1, y1, x2, y2):
    """Местный вид прямоугольником (x1, y1)–(x2, y2) в СК этого же вида: частичный разрез,
    обрезанный по прямой (волнистая линия обрыва — wave_line по краю). Окружность
    (local_view) прямой край не даёт: металл за обрывом оставался в кадре (ревьюер Вал_v6:
    секущая Е кончалась внутри контура Ø90 — теперь секущая на всю длину, вид — обрезан)."""
    c = CastTo(view, "IDrawingContainer").Rectangles.Add()
    c.X, c.Y = float(min(x1, x2)), float(min(y1, y2))
    c.Width, c.Height = abs(float(x2) - float(x1)), abs(float(y2) - float(y1))
    c.Angle, c.Style = 0.0, 1
    CastTo(c, "IDrawingObject").Update()
    av = CastTo(view, "IAssociationView")
    ok = av.CreateLocalView(c)
    rebuild(doc)
    return bool(ok) and bool(av.Local)


# ---------------------------------------------------------------- оформление
def radical(doc, vx, vy, value="Ra 1,6"):
    """Знак неуказанной шероховатости (правый верхний угол листа 1):
    радикал из 3 отрезков + полка + значение ПОД полкой. Пропорции эталона
    ACME: ветви ~6/12 мм под 60°, полка 30 мм. БЕЗ знака «остальное»."""
    views = views_of(doc)
    vsys = views.View(0)
    vsys.Current = True
    dc = CastTo(vsys, "IDrawingContainer")
    c60, s60 = 0.5, math.sin(math.radians(60))
    p_short = (vx - 6.1 * c60, vy + 6.1 * s60)
    p_long = (vx + 12.1 * c60, vy + 12.1 * s60)
    segment(dc, p_short[0], p_short[1], vx, vy)
    segment(dc, vx, vy, p_long[0], p_long[1])
    segment(dc, p_long[0], p_long[1], p_long[0] + 30.0, p_long[1])
    text(dc, p_long[0] + 12.0, vy + 4.5, value, 5.0)
    vsys.Current = False


def spec_rough(doc, text, add_sign=True):
    """Штатный знак неуказанной шероховатости (ISpecRough, Э4) — как у инженера (эталон вала
    ACME.107.002.004: Text «Ra 3,2», SignType 0, AddSign True → «√Ra 3,2 (√)»), место — авто."""
    sr = CastTo(doc, "IDrawingDocument").SpecRough
    sr.Text = gost_text(str(text))
    sr.SignType = 0
    sr.AddSign = bool(add_sign)
    sr.AutoPlacement = True
    sr.Update()
    return sr


TT_LINE_MM = 7.0     # шаг физической строки ТТ шрифта 5 мм на листе (Э3)


def tt_area(fmt, vertical):
    """Полоса ТТ над штампом (185×55 в правом нижнем углу рамки) и рамка листа:
    x1 = W−185, x2 = W−10, низ 65, верх не выше H−12. Э3: А4 верт. — x 25..200,
    А3 гориз. — x 235..410 (другие сочетания — та же формула, живьём не проверены)."""
    short, long_ = {3: (297.0, 420.0), 4: (210.0, 297.0)}[int(fmt)]
    w, h = (short, long_) if vertical else (long_, short)
    return {"x1": w - 185.0, "x2": w - 10.0, "y1": 65.0, "ytop": h - 12.0,
            "frame": (20.0, 5.0, w - 5.0, h - 5.0)}


TT_PAD_MM = 4.0      # мм: запас блока ТТ над последней строкой (Э3); ужимается до TT_PAD_MIN, если
TT_PAD_MIN = 1.0     # блок задевает вид над ним только запасом (Вал_v7: вид y 101, блок 65..97, зазор 4 < 5)


def tt_box(x1, x2, k, y1=65.0, pad=TT_PAD_MM):
    """Блок ТТ под k физических строк: высота 7·k + pad мм (Э3, второй проход)."""
    return (float(x1), float(y1), float(x2), float(y1) + TT_LINE_MM * k + float(pad))


def rect_inside(b, frame, tol=0.5):
    return (b[0] >= frame[0] - tol and b[1] >= frame[1] - tol
            and b[2] <= frame[2] + tol and b[3] <= frame[3] + tol)


def rects_cross(a, b, gap=0.0):
    """Прямоугольники (x1,y1,x2,y2) ближе gap мм (касание на gap — не пересечение)."""
    return a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap


TT_MIN_WIDTH = 100.0  # мм: уже — блок ТТ шрифтом 5 мм рвётся на слова (допущение)


def tt_fallback_x1(block, view_rects, gap=5.0, x2=None, min_width=TT_MIN_WIDTH):
    """Запасной x1 блока ТТ, если он задевает вид (зазор < gap): правый край
    видов в полосе блока по высоте + 10 (Э3: план x..245 → x1 = 255). None — не задевает.
    x2 задан и x1 > x2 − min_width → RuntimeError (иначе блок узкий или вывернутый)."""
    if not any(rects_cross(block, r, gap) for r in view_rects):
        return None
    band = [r for r in view_rects if r[1] < block[3] + gap and block[1] < r[3] + gap]
    x1 = max(r[2] for r in band) + 10.0
    if x2 is not None and x1 > float(x2) - min_width:
        raise RuntimeError(f"tech_req: справа от видов нет места под ТТ — x1 {x1:g} > "
                           f"x2 {float(x2):g} − {min_width:g}; подвинуть виды или задать x1/x2")
    return x1


def tt_shrink_pad(block, view_rects, y1, k, gap=5.0):
    """Запас блока ТТ поменьше (TT_PAD_MIN..TT_PAD_MM), если блок задевает вид над
    собой ТОЛЬКО запасом над строками: верх блока = низ вида − gap. None — не
    помогает (вид ниже верха строк или блок не задевает видов)."""
    hit = [r for r in view_rects if rects_cross(block, r, gap)]
    if not hit:
        return None
    top = min(r[1] for r in hit) - gap
    pad = top - float(y1) - TT_LINE_MM * k
    if TT_PAD_MIN <= pad < TT_PAD_MM:
        return round(pad, 1)
    return None


def view_rect(v):
    """Бумажный прямоугольник геометрии вида (углы bbox через view_to_paper);
    None — вид пуст."""
    vc = view_content(v)
    b = vc.get("bbox_shown") or vc["bbox"]
    if not b:
        return None
    pts = [view_to_paper(v, x, y) for x in (b[0], b[2]) for y in (b[1], b[3])]
    return (round(min(p[0] for p in pts), 1), round(min(p[1] for p in pts), 1),
            round(max(p[0] for p in pts), 1), round(max(p[1] for p in pts), 1))


def _tt_two_pass(tt, x1, x2, y1, ytop, pad=TT_PAD_MM):
    """Э3: блок во всю высоту → Update (КОМПАС переносит длинные пункты, перенос —
    отдельная строка Text) → k = Text.Count → высота 7·k+4 → Update.
    Один проход с высотой «на пункт» даёт второй блок-призрак (0, −258,5, 175, 0)."""
    tt.AutoPlacement = False
    tt.BlocksGabarits = (float(x1), float(y1), float(x2), float(ytop))
    tt.Update()
    tt.BlocksGabarits = tt_box(x1, x2, tt.Text.Count, y1, pad)
    tt.Update()
    g = [float(c) for c in (tt.BlocksGabarits or ())]
    return g, tuple(tt.BlocksStartLineNumbers or ())


TT_NUM_W = 6.0       # мм: ширина «1. » шрифтом 5 — висячий отступ пункта ТТ (оценка, проверяется вживую)
TT_NBSP = "\u00a0"


def tt_keep(s):
    """Обозначение стандарта не рвать переносом: «ГОСТ 30893.1», «ГОСТ Р 2.308», «ОСТ 92-…» —
    неразрывный пробел перед номером (ревьюер Вал_v6: «по ГОСТ / 30893.1»)."""
    return re.sub(r"(ГОСТ Р|ГОСТ|ОСТ|ТУ) (?=\d)",
                  lambda m: m.group(1).replace(" ", TT_NBSP) + TT_NBSP, s)


def tech_req(doc, lines, x1=None, x2=None):
    """Технические требования — ЗАМЕНЯЮТ прежний текст ТТ целиком (старые строки
    удаляются с конца: правка TextLine.Str со строкой-переносом ненадёжна).
    AutoPlacement НЕ используется (Э3 FAIL: на А4 блок за листом x<0, на А3 —
    зона во всю высоту): блок над штампом (tt_area листа 1) в два прохода, затем
    проверка — ровно один блок, внутри рамки, не ближе 5 мм к видам; если задевает
    вид — x1 = правый край видов + 10 и ещё два прохода; иначе RuntimeError.
    Номера пунктов писать в тексте («1. …»). Пункт о сертификате входного
    контроля НЕ включать (решение пользователя). «–», «×» заменяются (gost_text).
    Возвращает бумажный габарит блока (x1, y1, x2, y2)."""
    tt = CastTo(doc, "IDrawingDocument").TechnicalDemand
    txt = tt.Text
    for i in reversed(range(txt.Count)):
        txt.TextLine(i).Delete()
    for s in lines:
        ln = txt.Add()
        ln.Str = tt_keep(gost_text(s))
        try:
            ln.Align = 0      # по левому краю, как у инженера (по ширине растягивало слова, вал v3)
        except Exception:
            pass
        if re.match(r"^\d+[.)]\s", s):   # перенос — под текстом пункта, не под номером (ГОСТ 2.316)
            try:
                ln.LeftEdge, ln.IndentedLine = TT_NUM_W, -TT_NUM_W
            except Exception:
                pass
    f = doc.LayoutSheets.Item(0).Format
    area = tt_area(f.Format, f.VerticalOrientation)
    x1 = area["x1"] if x1 is None else float(x1)
    x2 = area["x2"] if x2 is None else float(x2)
    views = views_of(doc)
    # только виды листа 1 (лист 2 — правее, x ≥ ширины листа 1: задача 14d)
    rects = [r for r in (view_rect(views.View(i)) for i in range(1, views.Count))
             if r and r[0] < area["frame"][2] + 5.0]
    g, start = _tt_two_pass(tt, x1, x2, area["y1"], area["ytop"])
    if len(g) == 4:
        pad = tt_shrink_pad(g, rects, area["y1"], tt.Text.Count)
        if pad is not None:
            g, start = _tt_two_pass(tt, x1, x2, area["y1"], area["ytop"], pad)
    if len(g) == 4:
        nx1 = tt_fallback_x1(g, rects, x2=x2)
        if nx1 is not None and nx1 > x1:
            g, start = _tt_two_pass(tt, nx1, x2, area["y1"], area["ytop"])
    blocks = [tuple(round(c, 1) for c in g[i:i + 4]) for i in range(0, len(g), 4)]
    bad = []
    if len(blocks) != 1 or start != (0,):
        bad.append(f"блоков {len(blocks)} {blocks}, BlocksStartLineNumbers {start} "
                   "(строки не влезли — второй блок-призрак)")
    elif not rect_inside(blocks[0], area["frame"]):
        bad.append(f"блок {blocks[0]} вне рамки {area['frame']}")
    elif any(rects_cross(blocks[0], r, 5.0) for r in rects):
        bad.append(f"блок {blocks[0]} задевает вид(ы) {[r for r in rects if rects_cross(blocks[0], r, 5.0)]}")
    if bad:
        raise RuntimeError("tech_req: " + "; ".join(bad) + " — сократить пункты, "
                           "подвинуть виды или задать x1/x2 явно")
    return blocks[0]


_STAMP_PROPS = {1: "Наименование", 3: "Материал"}
_RULES_MOD = []


def _stamp_text(raw):
    """Отображаемый текст графы (поле-ссылка «^(#N~…)…~» → значение) — та же
    функция, что у kompas_check_drawing (drawing_rules.stamp_text)."""
    return _rules().stamp_text(raw or "")


def _rules():
    """drawing_rules.py (на уровень выше jobs/) — без правки sys.path."""
    if not _RULES_MOD:
        import importlib.util as ilu
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "drawing_rules.py")
        spec = ilu.spec_from_file_location("_draw_rules", p)
        m = ilu.module_from_spec(spec)
        spec.loader.exec_module(m)
        _RULES_MOD.append(m)
    return _RULES_MOD[0]


def stamp_cell_empty(raw):
    return not _stamp_text(raw).strip()


def _set_doc_property(doc, title, value):
    """Графы 1/3 штампа: Stamp.Text(1/3).Str не держится (КОМПАС возвращает
    значение модели) — писать свойство документа через IPropertyKeeper."""
    props = CastTo(app7(), "IPropertyMng").GetProperties(doc.PathName)
    if not isinstance(props, (tuple, list)):
        props = (props,)
    for p in props:
        if not hasattr(p, "_oleobj_"):
            p = Dispatch(p)
        p = CastTo(p, "IProperty")
        if p.Name == title:
            return CastTo(doc, "IPropertyKeeper").SetPropertyValue(p, value, False)
    raise KeyError(f"свойство документа «{title}» не найдено")


_STAMP_OVERRIDE = {1: "Наименование", 2: "Обозначение", 3: "Материал"}


def mass_digits(grams):
    """Знаков после запятой у массы в кг (project_rules.json: mass_format): digits,
    а для деталей легче small_g — small_digits (закон серии п. 8: «0,05», «0,004»)."""
    mf = _rules().load_rules()["mass_format"]
    return int(mf["small_digits"] if float(grams) < float(mf["small_g"]) else mf["digits"])


def mass_text(grams):
    """Масса модели (г) → текст графы 5 в кг: 2570 → «2,57», 48,24 → «0,05», 4,2 → «0,004»,
    1 → «0,001» (запятая). Единицу/знаки чертежа задаёт new_drawing(mass_g=...)."""
    return f"{float(grams) / 1000.0:.{mass_digits(grams)}f}".replace(".", ",")


STAMP_NAME_W = 68.0   # мм: ширина текста в графе 1 (графа 70 мм, ГОСТ 2.104) при шрифте 5


def name_lines(name, width=STAMP_NAME_W, h=5.0):
    """Перенос наименования по словам (закон серии: «Проставка-полюсник 200х10» →
    «Проставка-полюсник» / «200х10»; дефисное слово не рвать). Слово длиннее строки —
    целиком одной строкой. Ширина — оценка text_width."""
    lines, cur = [], ""
    for w in str(name).split():
        cand = (cur + " " + w) if cur else w
        if cur and text_width(cand, h) > width:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def stamp_should_write(n, raw):
    """Писать ли графу n штампа при заданном значении: 1/2/3 (наименование,
    обозначение, материал из spec.stamp) — ВСЕГДА (явное значение важнее модели,
    шифр ACME.100.01 и ГОСТ-форма материала, задача 13a); прочие — только пустые (Э5)."""
    return n in _STAMP_OVERRIDE or stamp_cell_empty(raw)


def stamp(doc, marking=None, name=None, material=None, mass=None, scale=None,
          sheet_no=None, sheets_total=None, company=None, first_use=None, litera=None):
    """Штамп листа 1. Графы 1 наименование, 2 обозначение, 3 материал, 5 масса
    приходят сами из свойств модели (Name, Marking, Material, Mass) при первом
    ассоциативном виде; 6 масштаб — поле главного вида.
    Переданные name/marking/material пишутся ВСЕГДА (перебивают модель) — в свойства
    ДОКУМЕНТА-чертежа через IPropertyKeeper, модель не меняется; пустая графа 2 без
    значения модели — Stamp.Text(2).Str, как раньше. Прочие графы ДОПИСЫВАЮТСЯ только
    пустыми (Э5), заполненные не трогаются (Stamp.Text(n).Str).
    Масса: берётся из массы, СОХРАНЁННОЙ в файле модели (перед чертежом модель
    открыть, прочитать TopPart.Mass, RebuildDocument, Save); детали < 5 г —
    new_drawing(..., mass_grams=True), иначе в графе «0». Графу 5 вручную не
    перебить (откатывается). Материал по проекту PRJB: «Сталь AISI 304».
    Возвращает {графа: "written" | "kept: <текст>"} для переданных значений
    («kept» — только у граф 5–8). company — графа 9 (организация), first_use — графа 25
    «Перв. примен.» (Э10 plate_leap; эталон: «Acme Engineering», «ACME.100.04.00.000 СБ»),
    litera — графа 4 «Лит.» (первая ячейка, 40; ревьюер Вал_v6)."""
    st = doc.LayoutSheets.Item(0).Stamp
    want = {1: name, 2: marking, 3: material, 5: mass, 6: scale,
            7: sheet_no, 8: sheets_total, 9: company, 25: first_use, 40: litera}
    res = {}
    for n, val in want.items():
        if val is None:
            continue
        cur = st.Text(n).Str
        if not stamp_should_write(n, cur):
            res[n] = "kept: " + _stamp_text(cur)
            continue
        if n == 1:                        # перенос по словам, не по буквам (закон серии)
            val = "\n".join(name_lines(val))
        if n in _STAMP_PROPS or (n in _STAMP_OVERRIDE and not stamp_cell_empty(cur)):
            _set_doc_property(doc, _STAMP_OVERRIDE[n], gost_text(str(val)))
        else:
            st.Text(n).Str = gost_text(str(val))
        res[n] = "written"
    st.Update()
    return res


def sheet2(doc, marking, fmt=3, name=None, material=None, company=None):
    """Второй лист со штампом формы 2а (только обозначение). Виды на нём —
    колонкой по центру, промежуток 30–40 мм, заголовки над видами.
    marking пустое — графа 2 не пишется (остаётся поле модели); сбой записи — исключение
    (сборка сверяет графу 2 листа 2 с листом 1 после штампа: server._sync_sheet2_marking).
    name/material/company — графы 1/3/9 листа 2 (форма 2а их не показывает, но данные
    документа должны совпадать с листом 1; Э10 plate_leap)."""
    sheets = doc.LayoutSheets
    sh2 = sheets.Add() if sheets.Count < 2 else sheets.Item(1)
    f2 = sh2.Format
    f2.Format = fmt
    f2.VerticalOrientation = False
    sh2.LayoutStyleNumber = 2.0
    sh2.Update()
    cells = {2: marking, 1: name, 3: material, 9: company}
    if any(cells.values()):
        for n, val in cells.items():
            if val:
                sh2.Stamp.Text(n).Str = gost_text(str(val))
        sh2.Stamp.Update()
    return sh2


# ----------------------------------- схема инженера для плит (задача 16, Э-plate_leap)
def rotate_view(doc, view, deg):
    """Повернуть ассоциативный вид без знака «повёрнуто»: ProjectionMatrix = Rz(deg)·M
    (M — 16 чисел IAssociationView.ProjectionMatrix как 4×4 по строкам) + Update + rebuild
    (Э7 plate_leap: плита 2300×2959 легла горизонтально, Angle вида 0, GetProjectionPoint
    и координаты вида — повёрнутые модельные; память long-plate-projection-rotated-section)."""
    av = CastTo(view, "IAssociationView")
    m = list(av.ProjectionMatrix)
    if len(m) != 16:
        raise RuntimeError(f"ProjectionMatrix: ждали 16 чисел, получили {len(m)}")
    M = [m[i * 4:(i + 1) * 4] for i in range(4)]
    c, s = math.cos(math.radians(float(deg))), math.sin(math.radians(float(deg)))
    R = [[c, -s, 0.0, 0.0], [s, c, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    P = [[sum(R[i][k] * M[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    av.ProjectionMatrix = [x for row in P for x in row]
    av.Update()
    rebuild(doc)


def set_frame(dim, on=True):
    """Рамка теоретически точного размера (ГОСТ Р 2.308): IDimensionText.Rectangle. Э1
    plate_leap: рисует ту же рамку, что разметка «@80~$(…)$» эталона, номинал остаётся авто."""
    CastTo(dim, "IDimensionText").Rectangle = bool(on)
    CastTo(dim, "IDrawingObject").Update()


def set_square(dim):
    """Знак «□» перед числом — квадратная сетка отверстий (эталон «□1127»): Sign=2."""
    CastTo(dim, "IDimensionText").Sign = 2
    CastTo(dim, "IDrawingObject").Update()


def shift_text(view, dim, shift_mm):
    """Вынести текст линейного размера за выносную линию: shift_mm > 0 — за вторую
    (бóльшую координату), < 0 — за первую, на |shift| мм листа. IDimensionParams.TextPos
    для этого непригоден (Р2 plate_leap: −20 читается 107, текст на 120 мм в сторону);
    X3 (гориз.) / Y3 (верт.) за пролётом КОМПАС сам подрезает до допустимого (память
    dense-hole-plate-rows). Возвращает принятую координату."""
    k = abs(float(shift_mm)) / view.Scale
    if int(dim.Orientation) == 1:
        lo, hi = sorted((dim.X1, dim.X2))
        dim.X3 = hi + k if shift_mm > 0 else lo - k
    else:
        lo, hi = sorted((dim.Y1, dim.Y2))
        dim.Y3 = hi + k if shift_mm > 0 else lo - k
    CastTo(dim, "IDrawingObject").Update()
    return dim.X3 if int(dim.Orientation) == 1 else dim.Y3


def axis_circle(v, xc, yc, r):
    """Осевая окружность (стиль 3) — окружность центров группы отверстий (PCD)."""
    dc = CastTo(v, "IDrawingContainer")
    c = dc.Circles.Add()
    c.Xc, c.Yc, c.Radius = float(xc), float(yc), float(r)
    c.Style = 3
    CastTo(c, "IDrawingObject").Update()
    return c


def ray(v, xc, yc, ang_deg, r_out, r_in=0.0):
    """Осевой луч (стиль 3) из центра под углом ang_deg (СК вида) — опора углового
    размера (BaseObject) и ось отверстия на окружности центров."""
    a = math.radians(float(ang_deg))
    return segment(v, xc + r_in * math.cos(a), yc + r_in * math.sin(a),
                   xc + r_out * math.cos(a), yc + r_out * math.sin(a), style=3)


def ang(v, base1, base2, radius_paper, direction=False, frame=False):
    """Угловой размер между двумя отрезками (осевыми лучами): AngleDimensions.Add(10) +
    BaseObject1/2 — параметры Xc/Angle1/Angle2 КОМПАС игнорирует (Э3 plate_leap, память
    angle-dim-title-move-raster). Угол — от направления base1 к base2 против часовой
    (направление отрезка X1→X2). Radius — мм ЛИСТА (140 дало дугу на весь лист; 18 — норма);
    direction=True — дуга с другой стороны; frame — рамка теоретически точного угла."""
    sc = CastTo(v, "ISymbols2DContainer")
    d = sc.AngleDimensions.Add(10)
    d.BaseObject1 = CastTo(base1, "IDrawingObject")
    d.BaseObject2 = CastTo(base2, "IDrawingObject")
    d.Radius = float(radius_paper)
    if direction:
        d.Direction = True
    CastTo(d, "IDrawingObject").Update()
    if frame:
        set_frame(d)
    return d


TOL_FRAME_W, TOL_FRAME_H = 29.4, 10.0   # мм листа: рамка «⊕ Ø0,5» шрифтом 5 (Р2 plate_leap)
TOL_SIGNS = {"pos": 27, "flat": 17, "straight": 16, "round": 22, "cyl": 23,
             "par": 24, "perp": 25, "coax": 26, "runout": 28, "runout_total": 29}   # ksTolerance spec-знаки (память gdt-tolerance-base-api)


def tolerance(v, x0, y0, sign=27, value="0,5", prefix_sign=2, base_pos=3, branch=None, base=None):
    """Рамка допуска формы/расположения (ГОСТ Р 2.308): Tolerances.Add. sign — номер
    спецзнака (27 позиционный, 17 плоскостность) или ключ TOL_SIGNS; prefix_sign 2 — «Ø»
    перед значением, 0 — без; (x0, y0) — точка рамки в СК вида, base_pos — какой угол
    рамки в ней (ksTablePointEnum, Р2 plate_leap: 1 левый низ, 3 левый верх, 5 правый
    верх, 7 правый низ); branch — (x, y) острия стрелки в СК вида; base — буква базы."""
    sc = CastTo(v, "ISymbols2DContainer")
    t = sc.Tolerances.Add()
    tp = CastTo(t, "IToleranceParam")
    tp.Sign = int(TOL_SIGNS.get(sign, sign))
    tp.PrefixSign = int(prefix_sign)
    tp.Value = gost_text(str(value))
    tp.BasePointPos = int(base_pos)
    bases = [b for b in str(base or "").split("|") if b]
    if bases:
        tp.BaseValue1.Str = bases[0]
    br = CastTo(t, "IBranchs")
    br.X0, br.Y0 = float(x0), float(y0)
    if branch is not None:
        br.AddBranchByPoint(0, float(branch[0]), float(branch[1]))
    CastTo(t, "IDrawingObject").Update()
    if len(bases) > 1:            # вторая и далее базы — новые столбцы таблицы рамки ПОСЛЕ первого Update
        tab = CastTo(t, "IToleranceParam").Table    # (до него столбцы теряются — сборка .008; ячейка —
        for b in bases[1:]:                         # ITextLine; BaseValue2 в v23 столбца не добавляет)
            tab.AddColumn(tab.ColumnsCount - 1, True)
            CastTo(tab.Cell(0, tab.ColumnsCount - 1).Text, "ITextLine").Str = b
        CastTo(t, "IDrawingObject").Update()
    return t


def base_sign(v, x0, y0, bx, by, letter):
    """Знак базы (ГОСТ 2.308): треугольник в (x0, y0) — на поверхности или продолжении
    размерной линии, рамка с буквой в (bx, by); всё в СК вида (память gdt-tolerance-base-api:
    Bases.Add, X0/Y0 + BranchX/Y, AutoSorted=False, Text)."""
    sc = CastTo(v, "ISymbols2DContainer")
    b = sc.Bases.Add()
    b.X0, b.Y0 = float(x0), float(y0)
    b.BranchX, b.BranchY = float(bx), float(by)
    try:
        b.AutoSorted = False
    except Exception:
        pass
    b.Text.Str = str(letter)
    CastTo(b, "IDrawingObject").Update()
    return b


def rough_sign(v, x, y, ra, shelf=None):
    """Знак шероховатости поверхности (ГОСТ 2.309) без опорного объекта: вершина в (x, y) СК
    вида на линии поверхности, знак вверх; текст «Ra 0,8» (память rough-sign-free-position:
    положение — ShelfX/ShelfY; BranchX0/Y0 тоже задаются — в разрезах применяются они).
    shelf=(sx, sy) СК вида — знак на полке линии-выноски: стрелка в (x, y), полка от (sx, sy)
    вправо, если sx ≥ x, иначе влево. Замер v23 (опыт probe_B, 2026-10-19): ветвь —
    IBranchs.AddBranchByPoint (возвращает False, но BranchCount=1), выноска — LeaderAngle (°) и
    LeaderLength (мм ЛИСТА) от стрелки; ShelfDirection 0 убирает выноску; −1 (влево) не замерен."""
    sc = CastTo(v, "ISymbols2DContainer")
    r = sc.Roughs.Add()
    rp = None
    try:
        rp = CastTo(r, "IRoughParams")
        rp.SignType = 0                  # как у инженера (эталон вала: SignType 0)
        rp.RoughParamText.Str = gost_text("Ra " + str(ra))
    except Exception:
        pass
    if shelf is not None:
        sx, sy = map(float, shelf)
        CastTo(r, "IBranchs").AddBranchByPoint(0, float(x), float(y))
        dx, dy = (sx - float(x)) * v.Scale, (sy - float(y)) * v.Scale
        rp.ShelfDirection = 1 if dx >= 0 else -1
        rp.LeaderAngle = math.degrees(math.atan2(dy, dx))
        rp.LeaderLength = math.hypot(dx, dy)
        CastTo(r, "IDrawingObject").Update()
        return r
    r.ShelfX, r.ShelfY = float(x), float(y)
    try:
        r.BranchX0, r.BranchY0 = float(x), float(y)
    except Exception:
        pass
    CastTo(r, "IDrawingObject").Update()
    return r


TEXT_CHAR_W = 3.1   # мм листа на символ размерной надписи шрифтом 5 (ГОСТ тип А курсив) — оценка


def text_width(s, h=5.0):
    return max(len(s or ""), 1) * TEXT_CHAR_W * h / 5.0


def obj_gabarit(doc, obj):
    """Бумажный габарит одного объекта (x1, y1, x2, y2) через ksGetObjGabaritRect или None."""
    kompas = api5()
    doc.Active = True
    d5 = kompas.ActiveDocument2D()
    par = kompas.GetParamStruct(15)
    if d5.ksGetObjGabaritRect(CastTo(obj, "IDrawingObject").Reference, par):
        b, t = par.GetpBot(), par.GetpTop()
        return (round(b.x, 2), round(b.y, 2), round(t.x, 2), round(t.y, 2))
    return None


def dim_label(dim):
    """Видимая надпись размера: префикс + значок + номинал + суффикс (для оценки ширины)."""
    dt = CastTo(dim, "IDimensionText")
    sign = {1: "Ø", 2: "□", 3: "R", 4: "М"}.get(int(dt.Sign or 0), "")
    return (dt.Prefix.Str or "") + sign + (dt.NominalText.Str or "") + (dt.Suffix.Str or "")


def dim_text_estimate(view, dim, gab):
    """ОЦЕНКА бумажной точки текста Ø/R/углового размера (у них нет X3/Y3-текста): текст сидит
    в углу габарита, дальнем от центра размера (Xc/Yc), сдвинут внутрь на полширины надписи
    и полвысоты. Для Ч12 — предупреждение, не FAIL (точность ±3 мм)."""
    if not gab:
        return None
    cx, cy = view_to_paper(view, dim.Xc, dim.Yc)
    corners = [(gab[0], gab[1]), (gab[0], gab[3]), (gab[2], gab[1]), (gab[2], gab[3])]
    fx, fy = max(corners, key=lambda p: math.hypot(p[0] - cx, p[1] - cy))
    w = min(text_width(dim_label(dim)), max(gab[2] - gab[0], 1.0))
    x = fx - w / 2 if fx == gab[2] else fx + w / 2
    y = fy - 2.5 if fy == gab[3] else fy + 2.5
    return [round(x, 1), round(y, 1)]


def tol_anchor_under_dim(doc, v, dim, shelf_right=True, gap=0.5):
    """Точка левого верха рамки допуска ПОД текстом размера (СК вида), как в эталоне
    («44 отв.Ø8», под полкой «⊕ Ø0,5»): левый верх рамки = левый низ текста. Ширина текста —
    оценка text_width по надписи; габарит Ø-размера включает ножку, поэтому левый край
    текста = правый край габарита − ширина при полке вправо, иначе левый край габарита.
    Линейный размер (текст по центру линии): середина габарита − полширины."""
    w = text_width(dim_label(dim))
    if hasattr(dim, "Orientation"):                  # линейный: текст над серединой линии
        g = obj_gabarit(doc, dim)
        if g is None:
            raise RuntimeError("габарит размера не прочитан")
        left = (g[0] + g[2]) / 2 - w / 2
        top = view_to_paper(v, dim.X3, dim.Y3)[1] - gap
    else:                                            # Ø/R на полке: от конца ножки по направлению полки
        try:
            sx, sy, sd = dia_shelf_start(v, dim)
            left = sx if sd == 1 else sx - w
            top = sy - gap
        except Exception:
            g = obj_gabarit(doc, dim)
            if g is None:
                raise RuntimeError("габарит размера не прочитан")
            left = g[2] - w if shelf_right else g[0]
            top = g[1] - gap
    return paper_to_view(v, left, top)


def tolerance_under_dim(doc, v, dim, sign=27, value="0,5", branch=None, shelf_right=True, gap=0.5, base=None):
    """Рамка допуска под текстом размера (tol_anchor_under_dim) со стрелкой branch;
    base — базы «А» или «А|Б|В» (столбцами рамки)."""
    x0, y0 = tol_anchor_under_dim(doc, v, dim, shelf_right, gap)
    return tolerance(v, x0, y0, sign=sign, value=value, base_pos=3, branch=branch, base=base)


# ------------------------------------------------------------------ измерение
def gabarits(doc, view_idx=None):
    """БУМАЖНЫЕ габариты размеров/выносок/текстов/секущих по видам:
    [(имя, (x1,y1,x2,y2)), ...] через ksGetObjGabaritRect. Основа самопроверки."""
    kompas = api5()
    doc.Active = True
    d5 = kompas.ActiveDocument2D()
    par = kompas.GetParamStruct(15)
    res = []
    views = views_of(doc)
    idxs = range(views.Count) if view_idx is None else view_idx
    for i in idxs:
        v = views.View(i)
        colls = []
        try:
            sc = CastTo(v, "ISymbols2DContainer")
            colls = [("lin", sc.LineDimensions, "LineDimension"),
                     ("dia", sc.DiametralDimensions, "DiametralDimension"),
                     ("rad", sc.RadialDimensions, "RadialDimension"),
                     ("ang", sc.AngleDimensions, "AngleDimension"),
                     ("tol", sc.Tolerances, "Tolerance"),
                     ("led", sc.Leaders, "Leader"),
                     ("cut", sc.CutLines, "CutLine")]
        except Exception:
            pass
        try:
            colls.append(("txt", CastTo(v, "IDrawingContainer").DrawingTexts, "DrawingText"))
        except Exception:
            pass
        for tag, coll, getter in colls:
            for j in range(coll.Count):
                try:
                    o = getattr(coll, getter)(j)
                    ref = CastTo(o, "IDrawingObject").Reference
                    if d5.ksGetObjGabaritRect(ref, par):
                        b = par.GetpBot()
                        t = par.GetpTop()
                        res.append((f"v{i}.{tag}{j}",
                                    (round(b.x, 1), round(b.y, 1), round(t.x, 1), round(t.y, 1))))
                except Exception:
                    pass
    return res


def overlaps(rects, tol=0.5):
    """Пары пересекающихся прямоугольников — размеры не должны налезать."""
    bad = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            n1, a = rects[i]
            n2, b = rects[j]
            if a[0] < b[2] - tol and b[0] < a[2] - tol and a[1] < b[3] - tol and b[1] < a[3] - tol:
                bad.append((n1, n2))
    return bad


def view_title_rect(doc, vi):
    """Бумажный габарит заголовка вида vi (IViewDesignation.DrawingText) или None
    (нет заголовка: у проекций габарит (0,0,0,0))."""
    kompas = api5()
    doc.Active = True
    d5 = kompas.ActiveDocument2D()
    par = kompas.GetParamStruct(15)
    v = views_of(doc).View(vi)
    ref = CastTo(CastTo(v, "IViewDesignation").DrawingText, "IDrawingObject").Reference
    if not d5.ksGetObjGabaritRect(ref, par):
        return None
    b, t = par.GetpBot(), par.GetpTop()
    r = [round(b.x, 1), round(b.y, 1), round(t.x, 1), round(t.y, 1)]
    return r if r[2] - r[0] > 0.1 else None


def title_lift(title, rects, gap=3.0):
    """На сколько мм поднять заголовок вида (бумажный габарит title), чтобы его низ
    был на gap выше верха ВСЕХ rects (габариты размеров/выносок вида). ≤ 0 — уже выше
    (отрицательное — запас)."""
    hit = [r for r in rects if r[2] > title[0] - 2.0 and r[0] < title[2] + 2.0]   # только под заголовком
    if not hit:                        # по X (±2 мм): полка сбоку от зоны заголовок не поднимает
        return 0.0
    return round(max(r[3] for r in hit) + gap - title[1], 2)


def lift_view_title(doc, vi, gap=3.0, tries=3):
    """Заголовок разреза/выносного (IViewDesignation.DrawingText) — над всеми
    размерами и выносками вида с зазором ≥ gap мм (ревьюер, Стойка корпуса: «А-А»
    между линией Ø90 и контуром). Только вверх и только если нужно. Сдвиг — API5
    ksMoveObj (t.Y сбрасывает шрифт заголовка); ksMoveObj масштабирует шаг
    непредсказуемо (×1 на 1:1, ×2/×4 в выносных — память чертёжника), поэтому шаг
    правится по замеру габарита, не больше tries сдвигов. Возвращает подъём, мм
    (0 — не нужен); не сошлось — RuntimeError."""
    kompas = api5()
    doc.Active = True
    d5 = kompas.ActiveDocument2D()
    par = kompas.GetParamStruct(15)

    def title():
        v = views_of(doc).View(vi)
        ref = CastTo(CastTo(v, "IViewDesignation").DrawingText, "IDrawingObject").Reference
        if not d5.ksGetObjGabaritRect(ref, par):
            raise RuntimeError(f"вид {vi}: габарит заголовка не прочитан")
        b, t = par.GetpBot(), par.GetpTop()
        return ref, (b.x, b.y, t.x, t.y)

    rects = [r for n, r in gabarits(doc, [vi])
             if n.split(".", 1)[1][:3] in ("lin", "dia", "rad", "ang", "tol", "led")]
    ref, g = title()
    y0, need, k = g[1], title_lift(g, rects, gap), 1.0
    if need <= 0:
        return 0.0
    for _ in range(tries):
        step = need / k
        d5.ksMoveObj(ref, 0.0, step)
        ref, g2 = title()
        if abs(g2[1] - g[1]) > 1e-3:
            k = (g2[1] - g[1]) / step
        g, need = g2, title_lift(g2, rects, gap)
        if -1.0 <= need <= 0.05:          # выше нужного, перелёт не больше 1 мм
            return round(g[1] - y0, 2)
    raise RuntimeError(f"вид {vi}: заголовок не встал над размерами за {tries} сдвига "
                       f"(не хватает {need} мм, низ {g[1]:.1f})")


def obj_parts(doc, view, obj):
    """Видимые части обозначения (ksDecomposeObj level 0): отрезки в мм ЛИСТА (дуги — ломаными)
    и бумажные габариты текстов. Для жёсткой проверки «без пересечений» (Ч19), как в
    tools/drawing_geom.py (замер 2026-10-19: отрезки — в СК вида, тексты — на листе)."""
    kompas = api5()
    doc.Active = True
    d5 = kompas.ActiveDocument2D()
    ref = CastTo(obj, "IDrawingObject").Reference
    segs, texts = [], []
    grp = d5.ksDecomposeObj(ref, 0, 0.1, 0)
    if not grp:
        return segs, texts
    it = kompas.GetIterator()
    it.ksCreateIterator(0, grp)
    e = it.ksMoveIterator("F")
    par = kompas.GetParamStruct(15)
    while e:
        typ = d5.ksGetObjParam(e, None, 0)
        if typ == 1:
            p = kompas.GetParamStruct(11)
            d5.ksGetObjParam(e, p, -1)
            a, b = view_to_paper(view, p.x1, p.y1), view_to_paper(view, p.x2, p.y2)
            segs.append((a[0], a[1], b[0], b[1]))
        elif typ == 3:
            p = kompas.GetParamStruct(12)
            d5.ksGetObjParam(e, p, -1)
            a1, a2 = math.radians(p.ang1), math.radians(p.ang2)
            if p.dir < 0:
                a1, a2 = a2, a1
            pts = [view_to_paper(view, x, y) for x, y in arc_points(p.xc, p.yc, p.rad, a1, a2, 8)]
            segs += [(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]) for k in range(len(pts) - 1)]
        elif typ == 4 and d5.ksGetObjGabaritRect(e, par):
            bt, tp = par.GetpBot(), par.GetpTop()
            texts.append((bt.x, bt.y, tp.x, tp.y))
        e = it.ksMoveIterator("N")
    try:
        d5.ksDeleteObj(grp)
    except Exception:
        pass
    if texts and break_zones(view):
        texts = _break_texts(doc, view, obj, texts)
    return segs, texts


def _break_texts(doc, view, obj, texts):
    """Вид с разрывом: габариты текстов разбиения КОМПАС отдаёт не там, где текст на листе
    (сборка .008: «40» у правого края плана — x 155 мм вместо 280; отрезки — на месте). Текст
    линейного размера — к его точке X3/Y3 (dim_text_paper, с учётом разрыва): у горизонтального
    — над размерной линией, у вертикального — левее неё; прочие — внутрь габарита объекта."""
    g = obj_gabarit(doc, obj)
    if not g:
        return texts
    inside = lambda t: g[0] - 1.0 <= t[0] and t[2] <= g[2] + 1.0
    if all(inside(t) for t in texts):
        return texts
    out = []
    try:
        d = CastTo(obj, "ILineDimension")
        cx, cy = dim_text_paper(view, d.X3, d.Y3, g)
        vertical = int(d.Orientation) == 2
    except Exception:
        d = None
    for t in texts:
        w, h = t[2] - t[0], t[3] - t[1]
        if inside(t):
            out.append(t)
        elif d is not None and vertical:
            out.append((cx - 0.5 - w, cy - h / 2, cx - 0.5, cy + h / 2))
        elif d is not None:
            out.append((cx - w / 2, cy + 0.5, cx + w / 2, cy + 0.5 + h))
        else:
            x0 = min(max(t[0], g[0]), g[2] - w)
            out.append((x0, t[1], x0 + w, t[3]))
    return out


def place_view_title(doc, vi, xc, y_bottom, tries=3):
    """Заголовок вида vi — по центру xc, низом на y_bottom (мм листа). Для частичного разреза
    (crop): КОМПАС ставит заголовок над НЕобрезанным видом, а виден лишь фрагмент. Сдвиг —
    ksMoveObj с поправкой шага по замеру габарита (как lift_view_title). Возвращает (dx, dy)."""
    kompas = api5()
    doc.Active = True
    d5 = kompas.ActiveDocument2D()
    par = kompas.GetParamStruct(15)

    def title():
        v = views_of(doc).View(vi)
        ref = CastTo(CastTo(v, "IViewDesignation").DrawingText, "IDrawingObject").Reference
        if not d5.ksGetObjGabaritRect(ref, par):
            raise RuntimeError(f"вид {vi}: габарит заголовка не прочитан")
        b, t = par.GetpBot(), par.GetpTop()
        return ref, (b.x, b.y, t.x, t.y)

    ref, g = title()
    x0, y0, k = g[0], g[1], 1.0
    for _ in range(tries):
        dx, dy = xc - (g[0] + g[2]) / 2, y_bottom - g[1]
        if abs(dx) < 0.3 and abs(dy) < 0.3:
            break
        d5.ksMoveObj(ref, dx / k, dy / k)
        ref, g2 = title()
        moved = math.hypot(g2[0] - g[0], g2[1] - g[1])
        want = math.hypot(dx, dy) / k
        if moved > 1e-3 and want > 1e-3:
            k = moved / want * k
        g = g2
    return round(g[0] - x0, 2), round(g[1] - y0, 2)


def zoom_all(doc):
    """Показать лист целиком — подготовка к кадру: сначала uncur (текущий вид и
    выделение портят превью системной рамкой)."""
    uncur(doc)
    kompas = api5()
    doc.Active = True
    kompas.ActiveDocument2D().ksZoomPrevNextOrAll(2)
