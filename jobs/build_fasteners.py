# -*- coding: utf-8 -*-
"""Строит библиотеку крепежа один раз, чтобы дальше только вставлять ссылками.

Запуск:  kompas_run_job("build_fasteners", '{"kinds":["screw"],"diameters":[8,10]}')

PARAMS (все необязательны):
  kinds      - список из "screw" (ISO 4762, внутр. шестигранник), "bolt"
               (ГОСТ 7798, шестигранная головка), "nut" (ГОСТ 5915),
               "washer" (ГОСТ 11371). По умолчанию все четыре.
  diameters  - список номинальных диаметров из таблиц ниже. По умолчанию все.
  lengths    - переопределить ряд длин (для screw/bolt).
  out_dir    - куда сохранять. По умолчанию ~/kompas-parts/fasteners
  overwrite  - True: перестроить уже существующие файлы. По умолчанию False.
  limit      - максимум файлов за один запуск (чтобы не упереться в таймаут).

ЕДИНАЯ СИСТЕМА КООРДИНАТ ДЛЯ ВСЕЙ БИБЛИОТЕКИ:
  опорная плоскость под головкой = Z0, головка растёт в +Z, стержень идёт в -Z.
  Поэтому винт в отверстие плиты с верхней гранью z=h ставится в origin (x, y, h)
  вообще без поворота.

ДОПУЩЕНИЯ (осознанные, по правилу пользователя «упрощённые модели допустимы»):
  - резьба не моделируется (ни профиль, ни условная): точны диаметр, длина и
    зацепление, а это всё, что проверяет аудит крепежа;
  - у винтов ISO 4762 гнездо под шестигранник построено, шлицев/накатки нет;
  - фаски на головках и гайках не строятся.
"""
import math
import os

from win32com.client import Dispatch, gencache
import pythoncom

# ---------------------------------------------------------------- таблицы ---
# ISO 4762 / ГОСТ Р ИСО 4762-2012: d -> (dk головки, k высота, s гнезда)
SCREW = {3: (5.5, 3.0, 2.5), 4: (7.0, 4.0, 3.0), 5: (8.5, 5.0, 4.0),
         6: (10.0, 6.0, 5.0), 8: (13.0, 8.0, 6.0), 10: (16.0, 10.0, 8.0),
         12: (18.0, 12.0, 10.0)}
# ГОСТ 7798-70: d -> (s под ключ, k высота головки)
BOLT = {6: (10.0, 4.0), 8: (13.0, 5.3), 10: (17.0, 6.4), 12: (19.0, 7.5)}
# ГОСТ 5915-70: d -> (s под ключ, m высота)
NUT = {3: (5.5, 2.4), 4: (7.0, 3.2), 5: (8.0, 4.7), 6: (10.0, 5.2),
       8: (13.0, 6.8), 10: (17.0, 8.4), 12: (19.0, 10.8)}
# ГОСТ 11371-78: d -> (d1 внутр., d2 наруж., s толщина)
WASHER = {3: (3.2, 7.0, 0.5), 4: (4.3, 9.0, 0.8), 5: (5.3, 10.0, 1.0),
          6: (6.4, 12.0, 1.6), 8: (8.4, 16.0, 1.6), 10: (10.5, 20.0, 2.0),
          12: (13.0, 24.0, 2.5)}
LENGTHS = {3: [6, 8, 10, 12, 16, 20, 25],
           4: [6, 8, 10, 12, 16, 20, 25, 30],
           5: [8, 10, 12, 16, 20, 25, 30, 35, 40],
           6: [10, 12, 16, 20, 25, 30, 35, 40, 50],
           8: [12, 16, 20, 25, 30, 35, 40, 45, 50, 60],
           10: [16, 20, 25, 30, 35, 40, 45, 50, 60, 70],
           12: [20, 25, 30, 35, 40, 45, 50, 60, 70, 80]}

MATERIAL = ("Сталь 12Х18Н10Т ГОСТ 5632-2014", 7.92)
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "kompas-parts", "fasteners")

# o3d
SKETCH, PLANE_XOY, PLANE_XOZ, PLANE_OFFSET = 5, 1, 2, 14
BASE_EXTRUDE, BOSS_EXTRUDE, CUT_EXTRUDE, BASE_ROTATED = 24, 25, 26, 27
FACE, BODY = 6, 115


# Построители вынесены в _shapes.py и общие с build_vacuum.py — правка в одном
# месте, а не в двух расходящихся копиях.
import sys
sys.path.insert(0, JOBS_DIR)                      # noqa: F821  (JOBS_DIR даёт сервер)
from _shapes import (api5, circle, hexagon, plane_at, sketch_on,  # noqa: E402
                     extrude, cut_both, revolve_profile)


# ------------------------------------------------------------- построители ---

def build_screw(kompas, part, d, L):
    dk, k, s = SCREW[d]
    # локальная Y эскиза -> глобальный -Z, поэтому знаки перевёрнуты:
    # головка окажется в 0..+k, стержень в 0..-L
    revolve_profile(part, [(0.0, -k), (dk / 2, -k), (dk / 2, 0.0),
                                   (d / 2.0, 0.0), (d / 2.0, L), (0.0, L)])
    sk = sketch_on(part, plane_at(part, k), lambda w: hexagon(w, s))
    cut_both(part, sk, 0.6 * k)
    return (f"Винт М{d}x{int(L)} А2",
            f"Винт М{d}-6gx{int(L)}.А2 ГОСТ Р ИСО 4762-2012",
            (dk, k + L))


def build_bolt(kompas, part, d, L):
    s, k = BOLT[d]
    sk = sketch_on(part, part.GetDefaultEntity(PLANE_XOY), lambda w: hexagon(w, s))
    extrude(part, sk, BASE_EXTRUDE, k, up=True)          # головка 0..+k
    sk2 = sketch_on(part, part.GetDefaultEntity(PLANE_XOY), lambda w: circle(w, d / 2.0))
    extrude(part, sk2, BOSS_EXTRUDE, L, up=False)        # стержень 0..-L
    return (f"Болт М{d}x{int(L)} А2",
            f"Болт М{d}-6gx{int(L)}.А2 ГОСТ 7798-70",
            (s / math.sqrt(3.0) * 2, k + L))


def build_nut(kompas, part, d, L=None):
    s, mm = NUT[d]
    sk = sketch_on(part, part.GetDefaultEntity(PLANE_XOY), lambda w: hexagon(w, s))
    extrude(part, sk, BASE_EXTRUDE, mm, up=True)
    sk2 = sketch_on(part, part.GetDefaultEntity(PLANE_XOY), lambda w: circle(w, d / 2.0))
    cut_both(part, sk2, mm + 1.0)
    return (f"Гайка М{d} А2", f"Гайка М{d}-6H.А2 ГОСТ 5915-70",
            (s / math.sqrt(3.0) * 2, mm))


def build_washer(kompas, part, d, L=None):
    d1, d2, t = WASHER[d]
    sk = sketch_on(part, part.GetDefaultEntity(PLANE_XOY), lambda w: circle(w, d2 / 2.0))
    extrude(part, sk, BASE_EXTRUDE, t, up=True)
    sk2 = sketch_on(part, part.GetDefaultEntity(PLANE_XOY), lambda w: circle(w, d1 / 2.0))
    cut_both(part, sk2, t + 1.0)
    return (f"Шайба {d} А2", f"Шайба {d}.А2 ГОСТ 11371-78", (d2, t))


BUILDERS = {"screw": (build_screw, SCREW, True),
            "bolt": (build_bolt, BOLT, True),
            "nut": (build_nut, NUT, False),
            "washer": (build_washer, WASHER, False)}


# -------------------------------------------------------------------- main ---
P = PARAMS if isinstance(PARAMS, dict) else {}          # noqa: F821  (из сервера)
kinds = P.get("kinds") or ["screw", "bolt", "nut", "washer"]
out_dir = P.get("out_dir") or DEFAULT_DIR
overwrite = bool(P.get("overwrite"))
limit = int(P.get("limit") or 10**6)
os.makedirs(out_dir, exist_ok=True)

kompas = api5()
jobs = []
for kind in kinds:
    if kind not in BUILDERS:
        print("неизвестный вид крепежа:", kind)
        continue
    fn, table, needs_len = BUILDERS[kind]
    diams = P.get("diameters") or sorted(table)
    for d in diams:
        if d not in table:
            continue
        if needs_len:
            for L in (P.get("lengths") or LENGTHS[d]):
                jobs.append((kind, fn, d, float(L)))
        else:
            jobs.append((kind, fn, d, None))

made, skipped, failed = 0, 0, []
for kind, fn, d, L in jobs:
    if made >= limit:
        print("достигнут limit=%d, остановка (запусти ещё раз, чтобы продолжить)" % limit)
        break
    doc = kompas.Document3D()
    doc.Create(False, True)                  # невидимая ДЕТАЛЬ: UI не засоряется
    part = doc.GetPart(-1)
    try:
        name, marking, (dia, height) = fn(kompas, part, d, L)
        path = os.path.join(out_dir, name + ".m3d")
        if os.path.exists(path) and not overwrite:
            doc.close()
            skipped += 1
            continue
        part.name = name
        part.marking = marking
        part.SetMaterial(MATERIAL[0], MATERIAL[1])
        part.Update()
        doc.RebuildDocument()
        ok, x1, y1, z1, x2, y2, z2 = part.GetGabarit(0., 0., 0., 0., 0., 0., 0.)
        gab = (round(x2 - x1, 2), round(y2 - y1, 2), round(z2 - z1, 2))
        # самоконтроль: габарит должен сойтись с расчётным
        if abs(gab[2] - height) > 0.05 or abs(max(gab[0], gab[1]) - dia) > 0.15:
            raise RuntimeError("габарит %s не сошёлся с расчётным (%.2f x %.2f)"
                               % (gab, dia, height))
        doc.SaveAs(path)
        doc.close()
        if not os.path.exists(path):
            raise RuntimeError("файл не появился на диске")
        made += 1
        print("OK  %-26s %s  габарит %sx%sx%s" % (name, os.path.basename(path), *gab))
    except Exception as e:
        try:
            doc.close()
        except Exception:
            pass
        failed.append("%s M%s x %s: %s" % (kind, d, L, e))

print("---")
print("построено: %d, пропущено (уже есть): %d, ошибок: %d" % (made, skipped, len(failed)))
for f in failed:
    print("  ОШИБКА", f)
print("папка:", out_dir)
if made >= 25:
    # Замерено 2026-08-25: 116 деталей -> процесс КОМПАС вырос с 742 до 1372 МБ.
    # doc.close() не освобождает невидимый документ полностью (~5 МБ за штуку),
    # а на машине с 8 ГБ это сразу уводит систему в подкачку.
    print("ВНИМАНИЕ: после пакетной сборки перезапусти КОМПАС — невидимые документы")
    print("оставляют примерно по 5 МБ каждый (%d шт. ≈ %d МБ) и засоряют Documents." % (made, made * 5))
