# -*- coding: utf-8 -*-
"""Общие построители для job-скриптов КОМПАС.

Не job, а библиотека: подключать из задания так —

    import sys; sys.path.insert(0, JOBS_DIR)   # JOBS_DIR даёт сервер
    from _shapes import api5, circle, hexagon, plane_at, sketch_on, extrude, cut_both, revolve_profile

Код вынесен из `build_fasteners.py` без изменений после того, как тот собрал
116 деталей крепежа с нулём ошибок. Менять его — только с повторной проверкой
габаритов на крепеже.
"""
import math

import pythoncom
from win32com.client import Dispatch, gencache

# o3d — константы типов объектов КОМПАС v23
SKETCH, PLANE_XOY, PLANE_XOZ, PLANE_OFFSET = 5, 1, 2, 14
BASE_EXTRUDE, BOSS_EXTRUDE, CUT_EXTRUDE, BASE_ROTATED = 24, 25, 26, 27
FACE, EDGE = 6, 7


def api5():
    """API5 KompasObject через gencache — обычный динамический враппер
    не видит ActiveDocument3D и часть членов."""
    m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
    return m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(
        m5.KompasObject.CLSID, pythoncom.IID_IDispatch))


def circle(w, r, xc=0.0, yc=0.0):
    w.ksCircle(xc, yc, r, 1)


def hexagon(w, s, xc=0.0, yc=0.0):
    """Правильный шестигранник «под ключ» s (расстояние между гранями)."""
    R = s / math.sqrt(3.0)
    v = [(xc + R * math.cos(math.radians(a)), yc + R * math.sin(math.radians(a)))
         for a in range(0, 360, 60)]
    for i in range(6):
        w.ksLineSeg(v[i][0], v[i][1], v[(i + 1) % 6][0], v[(i + 1) % 6][1], 1)


def plane_at(part, z):
    """Смещённая от XOY плоскость на высоте z (знак задаёт сторону)."""
    pl = part.NewEntity(PLANE_OFFSET)
    d = pl.GetDefinition()
    d.SetPlane(part.GetDefaultEntity(PLANE_XOY))
    d.direction = bool(z >= 0)      # False -> в минус Z
    d.offset = abs(float(z))
    if not pl.Create():
        raise RuntimeError("не удалось построить смещённую плоскость z=%s" % z)
    return pl


def sketch_on(part, plane, draw):
    sk = part.NewEntity(SKETCH)
    d = sk.GetDefinition()
    d.SetPlane(plane)
    if not sk.Create():
        raise RuntimeError("не удалось создать эскиз")
    w = d.BeginEdit()
    draw(w)
    d.EndEdit()
    return sk


def extrude(part, sk, kind, depth, up=True):
    """Выдавливание в ОДНУ сторону.

    directionType: 0 — прямое (+Z), 1 — обратное (−Z). Параметры задаются ТОЙ ЖЕ
    стороне, что и направление: для обратного это SetSideParam(False, ...),
    иначе операция не строится вовсе (проверено на шестигранном болте)."""
    e = part.NewEntity(kind)
    d = e.GetDefinition()
    d.SetSketch(sk)
    d.directionType = 0 if up else 1
    d.SetSideParam(bool(up), 0, float(depth), 0.0, False)
    before = part.EntityCollection(FACE).GetCount()
    if not e.Create():
        raise RuntimeError("операция %d не построилась" % kind)
    after = part.EntityCollection(FACE).GetCount()
    if after <= before and kind != BASE_EXTRUDE:
        raise RuntimeError("операция %d прошла вхолостую (граней %d -> %d)"
                           % (kind, before, after))
    return e


def cut_both(part, sk, depth):
    """Вырез ВСЕГДА directionType=2 и оба SetSideParam: сторона, уходящая
    в воздух, безвредна, а односторонний вырез регулярно режет мимо тела."""
    e = part.NewEntity(CUT_EXTRUDE)
    d = e.GetDefinition()
    d.SetSketch(sk)
    d.directionType = 2
    d.SetSideParam(True, 0, float(depth), 0.0, False)
    d.SetSideParam(False, 0, float(depth), 0.0, False)
    before = part.EntityCollection(FACE).GetCount()
    if not e.Create():
        raise RuntimeError("вырез не построился")
    after = part.EntityCollection(FACE).GetCount()
    if after <= before:
        raise RuntimeError("вырез прошёл вхолостую (граней %d -> %d)" % (before, after))
    return e


def revolve_profile(part, pts):
    """Тело вращения вокруг оси Z по половине профиля.

    Эскиз строится на XOZ, и его локальная ось Y смотрит в ГЛОБАЛЬНЫЙ −Z,
    поэтому pts задаются уже с учётом этого."""
    sk = part.NewEntity(SKETCH)
    d = sk.GetDefinition()
    d.SetPlane(part.GetDefaultEntity(PLANE_XOZ))
    if not sk.Create():
        raise RuntimeError("не удалось создать эскиз профиля")
    w = d.BeginEdit()
    lo = min(p[1] for p in pts) - 5.0
    hi = max(p[1] for p in pts) + 5.0
    w.ksLineSeg(0.0, lo, 0.0, hi, 3)                     # ось вращения
    for i in range(len(pts) - 1):
        w.ksLineSeg(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], 1)
    w.ksLineSeg(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], 1)
    d.EndEdit()
    e = part.NewEntity(BASE_ROTATED)
    rd = e.GetDefinition()
    rd.SetSketch(sk)
    rd.SetSideParam(True, 360.0)
    if not e.Create():
        raise RuntimeError("тело вращения не построилось")
    return e


def finish(doc, part, name, marking, material, density, path, expect=None):
    """Оформление, самоконтроль габаритом и сохранение.

    expect = (Ø, высота): расхождение больше допуска — исключение, а не
    предупреждение. Именно эта проверка дала 0 ошибок на 116 деталях крепежа."""
    import os
    part.name = name
    part.marking = marking
    part.SetMaterial(material, float(density))
    part.Update()
    doc.RebuildDocument()
    ok, x1, y1, z1, x2, y2, z2 = part.GetGabarit(0., 0., 0., 0., 0., 0., 0.)
    gab = (round(x2 - x1, 2), round(y2 - y1, 2), round(z2 - z1, 2))
    if expect:
        dia, h = expect
        if abs(gab[2] - h) > 0.05 or abs(max(gab[0], gab[1]) - dia) > 0.15:
            raise RuntimeError("габарит %s не сошёлся с расчётным (%.2f x %.2f)" % (gab, dia, h))
    doc.SaveAs(path)
    if not os.path.exists(path):
        raise RuntimeError("файл не появился на диске")
    return gab
