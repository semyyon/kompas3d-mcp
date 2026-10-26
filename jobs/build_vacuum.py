# -*- coding: utf-8 -*-
"""Строит вакуумную оснастку ISO-K по DN — чтобы дальше её только вставлять.

Запуск:  kompas_run_job("build_vacuum")
         kompas_run_job("build_vacuum", '{"kinds":["blank"],"dn":[100,200]}')

PARAMS (все необязательны):
  kinds     - "blank" (заглушка ISO-K), "weld" (приварной фланец ISO-K),
              "ring" (центрирующее кольцо ISO-K). По умолчанию все три.
  dn        - список DN из таблиц ниже. По умолчанию все.
  tube      - для "weld": {"100": 101.6} — какой из вариантов трубы брать.
              По умолчанию первый в списке TUBES[dn].
  out_dir   - куда сохранять. По умолчанию ~/kompas-parts/flanges
  overwrite - True: перестроить существующие файлы. По умолчанию False.
  limit     - максимум файлов за запуск.

РАЗМЕРЫ — из каталога Hositrad «ISO-K Flange Components» (стр. 61–63), сверены
с техническими заметками Kurt J. Lesker: OD и толщины совпали у обоих.
Подробности и таблицы — в скилле kompas-gost, references/vacuum-flanges.md.

ЕДИНАЯ СК: фланец лежит на XOY, ось Z — ось трубы, нижняя грань в Z=0.

ЧЕГО ЭТОТ JOB НЕ СТРОИТ И ПОЧЕМУ:
  - СТРУБЦИНЫ (claw clamp). В каталогах публикуются только винт (M10 для DN63–250,
    M12 для DN320–500) и габаритная длина; форма лапы — нет. Строить её вслепую
    значит выдумать геометрию стыка. Профиль снимать с фактической модели и
    согласовывать с пользователем (правило «детали нет в справочнике — спросить»).
  - Уплотнительное кольцо витона центрирующего кольца — отдельная деталь,
    здесь строится только металлическая часть.

ДОПУЩЕНИЯ:
  - Заглушка — гладкий диск OD×T без расточки под центрирующее кольцо: глубина
    расточки в каталогах не публикуется, а существующие заглушки пользователя
    («Заглушка ISO-K 100» Ø130×12) сделаны именно гладкими дисками.
  - Скругление кромки R из каталога не моделируется (на стык не влияет).
"""
import os
import sys

sys.path.insert(0, JOBS_DIR)                      # noqa: F821  (даёт сервер)
from _shapes import (api5, circle, plane_at, sketch_on, extrude, cut_both,  # noqa: E402
                     finish, BASE_EXTRUDE)

# DN -> (OD фланца, толщина, Ø посадки центрирующего кольца D3)
FLANGE = {63: (95.0, 12.0, 70.0), 80: (110.0, 12.0, 83.0), 100: (130.0, 12.0, 102.0),
          160: (180.0, 12.0, 153.0), 200: (240.0, 12.0, 213.0), 250: (290.0, 12.0, 261.0),
          320: (370.0, 17.0, 318.0), 400: (450.0, 17.0, 400.0), 500: (550.0, 17.0, 501.0),
          630: (690.0, 22.0, 651.0)}
# DN -> (D1 наружный, D2 внутренний, T толщина) центрирующего кольца
RING = {63: (70.0, 67.0, 8.0), 80: (83.0, 80.0, 8.0), 100: (102.0, 99.0, 8.0),
        160: (153.0, 150.0, 8.0), 200: (213.0, 210.0, 8.0), 250: (261.0, 258.0, 8.0),
        320: (318.0, 313.0, 14.0), 400: (400.0, 395.0, 14.0), 500: (501.0, 496.0, 14.0),
        630: (651.0, 646.0, 14.0)}
# DN -> (варианты наружного Ø трубы, внутренний Ø фланца D2, глубина расточки E)
TUBES = {63: ([63.5, 70.0, 76.1], 60.3, 6.5), 80: ([76.1, 88.9], 73.0, 6.5),
         100: ([101.6, 104.0, 108.0], 98.6, 6.5), 160: ([154.0, 159.0], 151.0, 6.5),
         200: ([204.0, 219.1], 201.0, 6.5), 250: ([254.0, 256.0, 273.0], 251.0, 6.5),
         320: ([323.9], 319.0, 9.5)}

MATERIAL = ("Сталь 12Х18Н10Т ГОСТ 5632-2014", 7.92)
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "kompas-parts", "flanges")


def build_blank(kompas, doc, part, dn, P):
    od, t, _d3 = FLANGE[dn]
    sk = sketch_on(part, part.GetDefaultEntity(1), lambda w: circle(w, od / 2.0))
    extrude(part, sk, BASE_EXTRUDE, t, up=True)
    return (f"Заглушка ISO-K {dn}", f"Заглушка ISO-K {dn} ISO 1609", (od, t))


def build_weld(kompas, doc, part, dn, P):
    if dn not in TUBES:
        raise RuntimeError("для DN%d нет данных по трубе" % dn)
    od, t, _d3 = FLANGE[dn]
    variants, bore, e = TUBES[dn]
    want = (P.get("tube") or {}).get(str(dn))
    tube = float(want) if want else variants[0]
    if tube not in variants and not want:
        raise RuntimeError("труба %s вне списка %s" % (tube, variants))
    sk = sketch_on(part, part.GetDefaultEntity(1), lambda w: circle(w, od / 2.0))
    extrude(part, sk, BASE_EXTRUDE, t, up=True)
    # сквозной проход
    sk2 = sketch_on(part, part.GetDefaultEntity(1), lambda w: circle(w, bore / 2.0))
    cut_both(part, sk2, t + 1.0)
    # расточка под трубу с ВЕРХНЕЙ грани на глубину E
    sk3 = sketch_on(part, plane_at(part, t), lambda w: circle(w, tube / 2.0))
    cut_both(part, sk3, e)
    return (f"Фланец ISO-K {dn} приварной Ø{tube:g}",
            f"Фланец ISO-K {dn} ISO 1609, труба Ø{tube:g}", (od, t))


def build_ring(kompas, doc, part, dn, P):
    d1, d2, t = RING[dn]
    sk = sketch_on(part, part.GetDefaultEntity(1), lambda w: circle(w, d1 / 2.0))
    extrude(part, sk, BASE_EXTRUDE, t, up=True)
    sk2 = sketch_on(part, part.GetDefaultEntity(1), lambda w: circle(w, d2 / 2.0))
    cut_both(part, sk2, t + 1.0)
    return (f"Кольцо центрирующее ISO-K {dn}",
            f"Кольцо центрирующее ISO-K {dn} ISO 1609", (d1, t))


BUILDERS = {"blank": (build_blank, FLANGE), "weld": (build_weld, TUBES),
            "ring": (build_ring, RING)}

P = PARAMS if isinstance(PARAMS, dict) else {}     # noqa: F821
kinds = P.get("kinds") or ["blank", "weld", "ring"]
out_dir = P.get("out_dir") or DEFAULT_DIR
overwrite = bool(P.get("overwrite"))
limit = int(P.get("limit") or 10 ** 6)
os.makedirs(out_dir, exist_ok=True)

kompas = api5()
jobs = []
for kind in kinds:
    if kind not in BUILDERS:
        print("неизвестный вид:", kind)
        continue
    fn, table = BUILDERS[kind]
    for dn in (P.get("dn") or sorted(table)):
        if dn in table:
            jobs.append((kind, fn, dn))

made, skipped, failed = 0, 0, []
for kind, fn, dn in jobs:
    if made >= limit:
        print("достигнут limit=%d, остановка" % limit)
        break
    doc = kompas.Document3D()
    doc.Create(False, True)                        # невидимая ДЕТАЛЬ: UI не засоряется
    part = doc.GetPart(-1)
    try:
        name, marking, expect = fn(kompas, doc, part, dn, P)
        path = os.path.join(out_dir, name + ".m3d")
        if os.path.exists(path) and not overwrite:
            doc.close()
            skipped += 1
            continue
        gab = finish(doc, part, name, marking, MATERIAL[0], MATERIAL[1], path, expect)
        doc.close()
        made += 1
        print("OK  %-38s габарит %sx%sx%s" % (name, *gab))
    except Exception as e:
        try:
            doc.close()
        except Exception:
            pass
        failed.append("%s DN%s: %s" % (kind, dn, e))

print("---")
print("построено: %d, пропущено (уже есть): %d, ошибок: %d" % (made, skipped, len(failed)))
for f in failed:
    print("  ОШИБКА", f)
print("папка:", out_dir)
if made >= 25:
    print("ВНИМАНИЕ: после пакетной сборки перезапусти КОМПАС — невидимые документы")
    print("оставляют примерно по 5 МБ каждый (%d шт. ≈ %d МБ)." % (made, made * 5))
