# -*- coding: utf-8 -*-
"""Машиночитаемый слепок активной сборки -> <папка сборки>\\состояние.json

Запуск:  kompas_run_job("state_dump")
         kompas_run_job("state_dump", '{"max_depth":2,"path":"D:\\\\...\\\\s.json"}')

PARAMS:
  max_depth - глубина обхода дерева (по умолчанию 3)
  max_items - предел числа записей (по умолчанию 2000)
  path      - куда писать (по умолчанию рядом со сборкой, «состояние.json»)

Зачем: следующая сессия читает готовый JSON вместо повторного обхода сборки
скриптом. `КОНТЕКСТ_ДЛЯ_CLAUDE.md` остаётся человекочитаемым и перестаёт
дублировать координаты — там только решения, допущения и открытые вопросы.
"""
import json
import os

from win32com.client import Dispatch, gencache, CastTo
import pythoncom

P = PARAMS if isinstance(PARAMS, dict) else {}          # noqa: F821
MAX_DEPTH = int(P.get("max_depth") or 3)
MAX_ITEMS = int(P.get("max_items") or 2000)

m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
kompas = m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(
    m5.KompasObject.CLSID, pythoncom.IID_IDispatch))
app = gencache.EnsureDispatch("Kompas.Application.7")

doc7 = app.ActiveDocument
if doc7 is None or doc7.DocumentType not in (4, 5):
    raise RuntimeError("активный документ — не 3D-модель")
d3 = CastTo(doc7, "IKompasDocument3D")
doc5 = kompas.ActiveDocument3D()


def m16(v):
    """API5 отдаёт (ok, (16 чисел)), API7 — сразу 16."""
    v = tuple(v)
    if len(v) == 16:
        return [float(x) for x in v]
    if len(v) == 2 and len(v[1]) == 16:
        return [float(x) for x in v[1]]
    raise RuntimeError("неожиданная форма матрицы")


def mul(a, b):
    return [sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4))
            for i in range(4) for j in range(4)]


I4 = [1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1., 0, 0, 0, 0, 1.]
count = [0]


def walk(p7, parent, depth):
    """Дерево с МИРОВЫМИ координатами: матрицы перемножаются по цепочке."""
    out = []
    if depth > MAX_DEPTH or count[0] >= MAX_ITEMS:
        return out
    try:
        parts = p7.Parts
        n = parts.Count
    except Exception:
        return out
    groups = {}
    for i in range(n):
        c = parts.Part(i)
        groups.setdefault((c.Name, getattr(c, "FileName", "")), []).append(c)
    for (name, fname), comps in groups.items():
        if count[0] >= MAX_ITEMS:
            break
        count[0] += 1
        first = comps[0]
        origins = []
        for c in comps:
            try:
                origins.append([round(v, 3) for v in mul(m16(c.Placement.GetMatrix3D()), parent)[12:15]])
            except Exception:
                origins.append(None)
        try:
            world = mul(m16(first.Placement.GetMatrix3D()), parent)
        except Exception:
            world = parent
        rec = {"name": name, "file": fname, "count": len(comps), "origins": origins,
               "axis_z": [round(world[8], 4), round(world[9], 4), round(world[10], 4)],
               "hidden": bool(getattr(first, "Hidden", False))}
        kids = walk(first, world, depth + 1)
        if kids:
            rec["children"] = kids
        out.append(rec)
    return out


top5 = doc5.GetPart(-1)
ok, x1, y1, z1, x2, y2, z2 = top5.GetGabarit(0., 0., 0., 0., 0., 0., 0.)
state = {
    "document": doc7.PathName or doc7.Name,
    "type": "assembly" if doc7.DocumentType == 5 else "part",
    "gabarit": {"x": [round(x1, 3), round(x2, 3)], "y": [round(y1, 3), round(y2, 3)],
                "z": [round(z1, 3), round(z2, 3)],
                "size": [round(x2 - x1, 3), round(y2 - y1, 3), round(z2 - z1, 3)]},
}
try:
    state["mass"] = round(d3.TopPart.Mass, 4)
except Exception:
    state["mass"] = None
try:
    state["faces"] = top5.EntityCollection(6).GetCount()
    state["bodies"] = top5.BodyCollection().GetCount()   # НЕ EntityCollection(115)!
except Exception:
    pass
try:
    state["name_marking_material"] = [top5.name, top5.marking, top5.GetMaterial()]
except Exception:
    pass
state["tree"] = walk(d3.TopPart, I4, 0)
state["tree_groups"] = count[0]
state["truncated"] = count[0] >= MAX_ITEMS

path = P.get("path")
if not path:
    base = doc7.PathName
    if not base:
        raise RuntimeError("документ не сохранён — передай path")
    path = os.path.join(os.path.dirname(base), "состояние.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=1)

print("документ:", state["document"])
print("габарит:", state["gabarit"]["size"], "| масса:", state["mass"],
      "| групп в дереве:", state["tree_groups"], "| усечено:", state["truncated"])
print("записано:", path, "(%d байт)" % os.path.getsize(path))
