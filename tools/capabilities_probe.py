# -*- coding: utf-8 -*-
"""Живая проверка описи КОМПАС → docs/kompas-capabilities.checks.json (затем перегенерировать опись).

    python tools/capabilities_probe.py read     # доступность типов объектов (чтение)
    python tools/capabilities_probe.py create   # рецепты создания через kompas_api

Работает ТОЛЬКО с файлами песочницы SANDBOX; документы пользователя не трогает (открывает
свои, закрывает свои, активный документ возвращает).

Чтение 3D: тип модели считается доступным, если (а) API5 ksPart.EntityCollection(тип)
отдаётся без ошибки и (б) коллекция его API7-интерфейса достижима из контейнеров детали
(PASS — оба; API5 — только (а); «?» — не подтверждено: тип может создаваться через общую
коллекцию с параметром типа, например все выноски — ILeaders, это эвристика не видит)
(IModelContainer, IAuxiliaryGeomContainer, ISurfaceContainer, ISheetMetalContainer,
ISymbols3DContainer). 2D — коллекция интерфейса достижима из контейнеров вида
(IDrawingContainer, ISymbols2DContainer, IBuildingContainer).
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import api_index  # noqa: E402
import server  # noqa: E402

ROOT = os.path.dirname(HERE)
CHECKS = os.path.join(ROOT, "docs", "kompas-capabilities.checks.json")
CAPS = os.path.join(ROOT, "docs", "kompas-capabilities.json")
SANDBOX = os.environ.get("KOMPAS_SANDBOX") or os.path.join(os.path.expanduser("~"), "kompas-mcp-sandbox")
TODAY = datetime.date.today().isoformat()
C3D = ("IModelContainer", "IAuxiliaryGeomContainer", "ISurfaceContainer", "ISheetMetalContainer",
       "ISymbols3DContainer")
C2D = ("IDrawingContainer", "ISymbols2DContainer", "IBuildingContainer")


def tool(name):
    fn = getattr(server, name)
    return getattr(fn, "__wrapped__", fn)


def load_checks():
    try:
        with open(CHECKS, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_checks(d):
    with open(CHECKS, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)


def reachable(obj, containers, idx):
    """{имя интерфейса коллекции: путь} — всё, что отдают свойства контейнеров без аргументов."""
    from win32com.client import CastTo
    out = {}
    for c in containers:
        try:
            co = CastTo(obj, c)
        except Exception:
            continue
        for p, d in api_index.members(idx, c)["props"].items():
            if d.get("args"):
                continue
            try:
                v = getattr(co, p)
            except Exception:
                continue
            if v is not None and hasattr(v, "_oleobj_"):
                out.setdefault(server._gw_iface(v), f"{c}.{p}")
    return out


# Объекты, которые создаются через ОБЩУЮ коллекцию с параметром типа или под другим именем
# коллекции (разобрано 2026-10-19 по ISymbols2DContainer/IBuildingContainer/IDrawingContainer)
VIA = {"IPositionLeader": "ILeaders", "IBrandLeader": "ILeaders", "IMarkLeader": "ILeaders",
       "IChangeLeader": "ILeaders", "IBreakAngleDimension": "IAngleDimensions",
       "ICircularCentres": "ICircularsCentries", "ILinearCentres": "ILinearsCentries",
       "IStraightAxis": "IBuildingAxes", "ICircleAxis": "IBuildingAxes", "IArcAxis": "IBuildingAxes",
       "IMarkInsideForm": "IMarks", "IMarkOnLeader": "IMarks", "IMarkOnLine": "IMarks",
       "IInsertionView": "IInsertionObjects", "IInsertionFragment": "IInsertionObjects",
       "IConditionCrossing": "IConditionIntersects", "IPolyline": "IPolyLines2D",
       "IMultiTextLeade": "IMultiTextLeaders", "ILayer": "ILayers", "IView": "IViews"}


def coll_for(iface, have):
    if iface in VIA and VIA[iface] in have:
        return have[VIA[iface]] + " (общая коллекция)"
    for cand in (iface + "s", iface[:-1] + "ies", iface + "es", iface.replace("3D", "s3D"),
                 iface.replace("7", "s7"), iface):
        if cand in have:
            return have[cand]
    return ""


def open_or_make(kind, path):
    if os.path.exists(path):
        print(tool("kompas_open_document")(path=path))
    else:
        print(tool("kompas_create_document")(doc_type=kind))


def probe_read():
    from win32com.client import CastTo
    idx = api_index.load()
    with open(CAPS, encoding="utf-8") as f:
        rows = json.load(f)["rows"]
    checks = load_checks()
    part = os.path.join(SANDBOX, "gw_part.m3d")
    open_or_make("part", part)
    with server.com_context():
        kompas, app7 = server.get_typed_api()
        server._activate(app7, part)
        top = CastTo(app7.ActiveDocument, "IKompasDocument3D").TopPart
        top5 = kompas.ActiveDocument3D().GetPart(-1)
        have3 = reachable(top, C3D, idx)
        n = 0
        for r in rows:
            if not r["key"].startswith("o3d_") or r.get("service") or r["const"] >= 1500:
                continue
            try:
                coll = top5.EntityCollection(r["const"])
                ok5 = coll is not None
                cnt = coll.GetCount() if ok5 else None
            except Exception as e:
                ok5, cnt = False, str(e)[:40]
            path7 = coll_for(r["api7"], have3) if r.get("api7") else ""
            res = "PASS" if (ok5 and path7) else ("API5" if ok5 else "?")
            note = f"чтение: API5 EntityCollection({r['const']})" + (f" n={cnt}" if ok5 else " — нет")
            note += f"; API7 {path7}" if path7 else ("; API7 коллекция не найдена в контейнерах" if r.get("api7") else "")
            prev = checks.get(r["key"])
            if prev and "создание" in prev.get("note", ""):
                continue                 # результат создания важнее чтения
            checks[r["key"]] = {"result": res, "date": TODAY, "note": note}
            n += 1
        print(f"3D: проверено {n} типов; контейнеры API7 отдали {len(have3)} коллекций")
    # 2D: временный чертёж в песочнице (не сохраняется)
    print(tool("kompas_create_document")(doc_type="drawing"))
    with server.com_context():
        kompas, app7 = server.get_typed_api()
        doc = app7.ActiveDocument
        try:
            mgr = CastTo(doc, "IKompasDocument2D").ViewsAndLayersManager
            view = mgr.Views.ActiveView
            have2 = reachable(view, C2D, idx)
            have2.update(reachable(mgr, ("IViewsAndLayersManager",), idx))      # виды и слои
            have2.update(reachable(view, ("IView",), idx))
            n = 0
            for r in rows:
                if r["key"].startswith("o3d_") or r.get("service") or "api" in r:
                    continue
                ifaces = (r.get("api7") or "").split()
                path = next((coll_for(i, have2) for i in ifaces if coll_for(i, have2)), "")
                checks.setdefault(r["key"], {})
                checks[r["key"]] = {"result": "PASS" if path else "?", "date": TODAY,
                                    "note": f"чтение: {path}" if path else
                                    f"чтение: коллекция {r.get('api7') or '?'} не найдена в контейнерах вида"}
                n += 1
            print(f"2D: проверено {n} типов; контейнеры вида отдали {len(have2)} коллекций")
        finally:
            doc.Close(server.KD_DO_NOT_SAVE_CHANGES)
    save_checks(checks)


# ── Создание: рецепты операторов шлюза kompas_api (они же — примеры для агентов) ──
def sketch(name, plane_ops, prims):
    """Эскиз API5 на плоскости $<plane>: prims — [("ksLineSeg", [..]) | ("ksCircle", [..])]."""
    ops = [{"obj": "top5", "call": "NewEntity", "args": [{"const": "o3d_sketch"}], "as": name},
           {"obj": "$" + name, "call": "GetDefinition", "as": name + "d"}] + plane_ops + [
           {"obj": "$" + name + "d", "call": "SetPlane", "args": ["$pl"]},
           {"obj": "$" + name, "call": "Create"},
           {"obj": "$" + name + "d", "call": "BeginEdit", "as": "e2"}]
    ops += [{"obj": "$e2", "call": f, "args": a} for f, a in prims]
    return ops + [{"obj": "$" + name + "d", "call": "EndEdit"}]


XOY = [{"obj": "top5", "call": "GetDefaultEntity", "args": [{"const": "o3d_planeXOY"}], "as": "pl"}]
RECT = [("ksLineSeg", [-50, -30, 50, -30, 1]), ("ksLineSeg", [50, -30, 50, 30, 1]),
        ("ksLineSeg", [50, 30, -50, 30, 1]), ("ksLineSeg", [-50, 30, -50, -30, 1])]


def entity(name, o3d, sets=(), calls=()):
    ops = [{"obj": "top5", "call": "NewEntity", "args": [{"const": o3d}], "as": name},
           {"obj": "$" + name, "call": "GetDefinition", "as": name + "d"}]
    ops += [{"obj": "$" + name + "d", "set": k, "value": v} for k, v in sets]
    ops += [dict({"obj": "$" + name + "d"}, **c) for c in calls]
    return ops + [{"obj": "$" + name, "call": "Create"}]


BASE = sketch("sk", XOY, RECT) + entity("b", "o3d_baseExtrusion", [("directionType", 0)],
                                         [{"call": "SetSideParam", "args": [True, 0, 10.0, 0, False]}])
HOLE = sketch("sk2", XOY, [("ksCircle", [30, 0, 5, 1])]) + entity(
    "c", "o3d_cutExtrusion", [("directionType", 2)],
    [{"call": "SetSketch", "args": ["$sk2"]}, {"call": "SetSideParam", "args": [True, 1, 0, 0, False]},
     {"call": "SetSideParam", "args": [False, 1, 0, 0, False]}])
BASE[len(sketch("sk", XOY, RECT)) + 2:len(sketch("sk", XOY, RECT)) + 2] = [
    {"obj": "$bd", "call": "SetSketch", "args": ["$sk"]}]
FACES = [{"obj": "top5", "call": "EntityCollection", "args": [{"const": "o3d_face"}], "as": "fc"},
         {"obj": "$fc", "call": "GetCount"}]


def edges_at(x, y, z, name="ec"):
    return [{"obj": "top5", "call": "EntityCollection", "args": [{"const": "o3d_edge"}], "as": name},
            {"obj": "$" + name, "call": "SelectByPoint", "args": [x, y, z]}]


def faces_at(x, y, z, name="fs"):
    return [{"obj": "top5", "call": "EntityCollection", "args": [{"const": "o3d_face"}], "as": name},
            {"obj": "$" + name, "call": "SelectByPoint", "args": [x, y, z]}]


RECIPES = {
    "o3d_baseExtrusion": BASE + FACES,
    "o3d_cutExtrusion": BASE + HOLE + FACES,
    "o3d_fillet": BASE + edges_at(50, 30, 5) + entity("f", "o3d_fillet", [("radius", 3.0)], [
        {"call": "array", "as": "fa"}]) [:-1] + [{"obj": "$fa", "call": "Add", "args": ["$ec.0"]}] + [
        {"obj": "$f", "call": "Create"}] + FACES,
    "o3d_chamfer": BASE + edges_at(50, 30, 5) + entity("h", "o3d_chamfer", [], [
        {"call": "SetChamferParam", "args": [True, 2.0, 2.0]}, {"call": "array", "as": "ha"}])[:-1] + [
        {"obj": "$ha", "call": "Add", "args": ["$ec.0"]}, {"obj": "$h", "call": "Create"}] + FACES,
    "o3d_shellOperation": BASE + faces_at(0, 0, 10) + entity("s", "o3d_shellOperation", [("thickness", 2.0)], [
        {"call": "FaceArray", "as": "sa"}])[:-1] + [
        {"obj": "$sa", "call": "Add", "args": ["$fs.0"]}, {"obj": "$s", "call": "Create"}] + FACES,
    "o3d_planeOffset": XOY + entity("po", "o3d_planeOffset", [("offset", 20.0)],
                                    [{"call": "SetPlane", "args": ["$pl"]}]),
    "o3d_spline": entity("sp", "o3d_spline", [], [
        {"call": "AddVertex", "args": [0, 0, 0, 0]}, {"call": "AddVertex", "args": [10, 5, 3, 0]},
        {"call": "AddVertex", "args": [20, 0, 8, 0]}]),
    "o3d_ExtrusionSurface": sketch("ss", XOY, [("ksArcByAngle", [0, 0, 20, 0, 180, 1, 1])]) + entity(
        "es", "o3d_ExtrusionSurface", [("directionType", 0)],
        [{"call": "SetSketch", "args": ["$ss"]}, {"call": "SetSideParam", "args": [True, 0, 15.0, 0, False]}]),
    "aux.lcs": [{"obj": "top", "cast": "IAuxiliaryGeomContainer", "get": "LocalCoordinateSystems", "as": "lc"},
                {"obj": "$lc", "call": "Add", "as": "l1"}, {"obj": "$l1", "call": "Update"},
                {"obj": "$lc", "get": "Count"}],
    "emb.manage": BASE + [{"obj": "top", "cast": "IEmbodimentsManager", "call": "AddEmbodiment",
                           "args": [0, True, "ПЕСОК.001", 1, 0]},
                          {"obj": "top", "cast": "IEmbodimentsManager", "get": "EmbodimentCount"}],
}
# Листовое тело по API7: эскиз API5 → тот же эскиз как ISketch из IModelContainer.Sketchs
RECIPES["o3d_sheetMetalBody"] = sketch("sk", XOY, RECT[:3]) + [
    {"obj": "top", "cast": "IModelContainer", "get": "Sketchs", "as": "S"},
    {"obj": "top", "cast": "ISheetMetalContainer", "get": "SheetMetalBodies", "as": "SB"},
    {"obj": "$SB", "call": "Add", "as": "sb"},
    {"obj": "$sb", "set": "Sketch", "value": "$S.0"},
    {"obj": "$sb", "set": "Thickness", "value": 2.0},
    {"obj": "$sb", "set": "Depth", "args": [True], "value": 40.0},
    {"obj": "$sb", "call": "Update"}] + FACES
RECIPES["aux.lcs"] = [
    {"obj": "top", "cast": "IAuxiliaryGeomContainer", "get": "LocalCoordinateSystems", "as": "lc"},
    {"obj": "$lc", "call": "Add", "as": "l1"},
    {"obj": "$l1", "set": "X", "value": 10.0}, {"obj": "$l1", "set": "Y", "value": 0.0},
    {"obj": "$l1", "set": "Z", "value": 5.0}, {"obj": "$l1", "call": "Update"},
    {"obj": "$lc", "get": "Count"}]
RECIPES["o3d_meshCopy"] = BASE + HOLE + [
    {"obj": "top5", "call": "GetDefaultEntity", "args": [{"const": "o3d_axisOY"}], "as": "ax"}] + entity(
    "mc", "o3d_meshCopy", [], [{"call": "SetAxis1", "args": ["$ax"]},
                               {"call": "SetCopyParamAlongAxis", "args": [True, 0.0, 2, 20.0, False]},
                               {"call": "SetCopyParamAlongAxis", "args": [False, 90.0, 1, 10.0, False]},
                               {"call": "OperationArray", "as": "oa"}])[:-1] + [
    {"obj": "$oa", "call": "Add", "args": ["$c"]}, {"obj": "$mc", "call": "Create"}] + FACES
RECIPES["o3d_mirrorOperation"] = BASE + HOLE + [
    {"obj": "top5", "call": "GetDefaultEntity", "args": [{"const": "o3d_planeYOZ"}], "as": "yz"}] + entity(
    "mo", "o3d_mirrorOperation", [], [{"call": "SetPlane", "args": ["$yz"]},
                                      {"call": "GetOperationArray", "as": "oa"}])[:-1] + [
    {"obj": "$oa", "call": "Add", "args": ["$c"]}, {"obj": "$mo", "call": "Create"}] + FACES
RECIPES["o3d_incline"] = BASE + faces_at(50, 0, 5) + XOY + entity(
    "inc", "o3d_incline", [("angle", 5.0)], [{"call": "SetPlane", "args": ["$pl"]},
                                            {"call": "FaceArray", "as": "fa"}])[:-1] + [
    {"obj": "$fa", "call": "Add", "args": ["$fs.0"]}, {"obj": "$inc", "call": "Create"},
    {"obj": "top", "get": "Mass"}]   # без уклона 100×60×10 сталь = 471,0 г
RECIPES["o3d_baseLoft"] = sketch("s1", XOY, [("ksCircle", [0, 0, 20, 1])]) + XOY + entity(
    "po", "o3d_planeOffset", [("offset", 30.0)], [{"call": "SetPlane", "args": ["$pl"]}]) + [
    {"obj": "top5", "call": "NewEntity", "args": [{"const": "o3d_sketch"}], "as": "s2"},
    {"obj": "$s2", "call": "GetDefinition", "as": "s2d"},
    {"obj": "$s2d", "call": "SetPlane", "args": ["$po"]}, {"obj": "$s2", "call": "Create"},
    {"obj": "$s2d", "call": "BeginEdit", "as": "e2"}, {"obj": "$e2", "call": "ksCircle", "args": [0, 0, 10, 1]},
    {"obj": "$s2d", "call": "EndEdit"}] + entity("lo", "o3d_baseLoft", [], [{"call": "Sketchs", "as": "ls"}])[:-1] + [
    {"obj": "$ls", "call": "Add", "args": ["$s1"]}, {"obj": "$ls", "call": "Add", "args": ["$s2"]},
    {"obj": "$lo", "call": "Create"}] + FACES
# Циклическая копия выреза вокруг оси Z: 6 штук
RECIPES["o3d_circularCopy"] = BASE + HOLE + [
    {"obj": "top5", "call": "GetDefaultEntity", "args": [{"const": "o3d_axisOZ"}], "as": "ax"}] + entity(
    "cc", "o3d_circularCopy", [], [{"call": "SetAxis", "args": ["$ax"]},
                                   {"call": "SetCopyParamAlongDir", "args": [6, 60.0, False, False]},
                                   {"call": "GetOperationArray", "as": "oa"}])[:-1] + [
    {"obj": "$oa", "call": "Add", "args": ["$c"]}, {"obj": "$cc", "call": "Create"}] + FACES


# Ожидаемое число граней после рецепта (последний шаг FACES): иначе Create()=True ничего не доказывает
EXPECT_FACES = {"o3d_baseExtrusion": 6, "o3d_cutExtrusion": 7, "o3d_fillet": 7, "o3d_chamfer": 7,
                "o3d_shellOperation": 11, "o3d_meshCopy": 8, "o3d_mirrorOperation": 8, "o3d_baseLoft": 3,
                }
EXPECT_NOT = {"o3d_incline": 471.0}     # результат НЕ должен совпасть (масса без операции)


def probe_create(only=None):
    checks = load_checks()
    api = tool("kompas_api")
    for key, ops in RECIPES.items():
        if only and key not in only:
            continue
        print(tool("kompas_create_document")(doc_type="part").splitlines()[0])
        try:
            res = api(ops=json.dumps(ops, ensure_ascii=False))
            ok = not res.startswith("Failed")
            try:
                body = json.loads(res[len("Failed: "):] if not ok else res)
            except ValueError:
                body = {"steps": [{"i": "?", "error": res}]}
            last = body["steps"][-1]
            info = last.get("result") if ok else last.get("error")
            # Create() у API5 возвращает bool — False в любом шаге = провал
            falses = [s["i"] for s in body["steps"] if s.get("result") is False]
            good = ok and not falses
            if good and key in EXPECT_NOT and round(info, 1) == EXPECT_NOT[key]:
                good, falses = False, [f"итог {info} = значению без операции"]
            if good and key in EXPECT_FACES and info != EXPECT_FACES[key]:
                good, falses = False, [f"граней {info}, ожидалось {EXPECT_FACES[key]}"]
            checks[key] = {"result": "PASS" if good else "FAIL", "date": TODAY,
                           "note": f"создание через kompas_api ({len(ops)} операторов): "
                                   + (f"итог {json.dumps(info, ensure_ascii=False)[:80]}" if good else
                                      f"шаг {falses or last['i']}: {str(info)[:160]}")}
            print(key, checks[key]["result"], checks[key]["note"][:200])
        finally:
            print(tool("kompas_close_document")(save=False))
    save_checks(checks)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"
    if mode == "create":
        probe_create(sys.argv[2:] or None)
    else:
        probe_read()
