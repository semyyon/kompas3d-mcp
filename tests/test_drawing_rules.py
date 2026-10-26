# tests/test_drawing_rules.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import drawing_rules as R

RULES = R.load_rules()

def facts(**kw):
    f = {"formats": [3], "stamp": {"name": "Проставка", "marking": "PRJ.02.019",
         "material": "Сталь 10 ГОСТ 1050-2013", "mass": "0,05", "scale": "1:2"},
         "views": [{"idx": 1, "name": "Вид спереди", "kind": "proj", "scale": 0.5,
                    "scale_shown": False, "has_geometry": True, "bbox_paper": [50, 50, 150, 100]},
                   {"idx": 2, "name": "Вид сверху", "kind": "proj", "scale": 0.5,
                    "scale_shown": False, "has_geometry": True, "bbox_paper": [50, 120, 150, 140]}],
         "dims": [], "leaders": [], "sys_texts": ["Ra 1,6"], "tt_lines": ["1. Острые кромки притупить."]}
    f.update(kw); return f

def fails(f):
    return {c["code"] for c in R.evaluate(f, RULES) if not c["ok"]}

class T(unittest.TestCase):
    def test_clean_steel_part_passes(self):
        self.assertEqual(fails(facts()), set())
    def test_copper_material_ok(self):
        f = facts(); f["stamp"]["material"] = "Медь М0б ГОСТ 859-2014"
        self.assertNotIn("Ч04", fails(f))
    def test_unknown_material_fails_with_hint(self):
        f = facts(); f["stamp"]["material"] = "Титан ВТ1-0"
        cs = [c for c in R.evaluate(f, RULES) if c["code"] == "Ч04"]
        self.assertFalse(cs[0]["ok"]); self.assertIn("project_rules.json", cs[0]["fix_hint"])
    def test_nonmetal_needs_ra125(self):
        f = facts(sys_texts=["Ra 1,6"]); f["stamp"]["material"] = "Капролон В"
        self.assertIn("Ч13", fails(f))
        f["sys_texts"] = ["Ra 12,5"]; self.assertNotIn("Ч13", fails(f))
    def test_assembly_rules(self):
        f = facts(sys_texts=[]); f["stamp"]["marking"] = "PRJ.02.000 СБ"; f["stamp"]["material"] = ""
        self.assertEqual(fails(f) & {"Ч04", "Ч13"}, set())
    def test_scale_not_in_series(self):
        f = facts(); f["stamp"]["scale"] = "1:3"
        self.assertIn("Ч06", fails(f))
    def test_deep_decimals(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "12,35", "text_xy_paper": [20, 60]}])
        self.assertIn("Ч09", fails(f))
    def test_text_inside_contour(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [100, 75]}])
        self.assertIn("Ч10", fails(f))
    def test_overlap(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [20, 60]},
                        {"view": 1, "id": "lin1", "kind": "lin", "text": "50", "text_xy_paper": [23, 62]}])
        self.assertIn("Ч12", fails(f))
    def test_tt_certificate(self):
        f = facts(tt_lines=["1. Сертификат входного контроля."])
        self.assertIn("Ч15", fails(f))

class StampText(unittest.TestCase):
    # сырые Str графы 6 из живого прогона на PRJ.02 (2026-10-14)
    def test_field_strings_observed_live(self):
        for raw, want in [("^(#5~1:2,5)1;288314011823.000000;1~", "1:2,5"),   # Основание медное
                          ("^(#3~1:1)1;320017264225.000000;1~", "1:1"),       # Проставка 210х10
                          ("^(#3~2:1)1;361082009465.000000;1~", "2:1"),       # Лепесток заземления
                          ("^(#5~1:2,5)1;223930714141.000000;1~", "1:2,5")]:  # Корпус assembly
            self.assertEqual(R.stamp_text(raw), want)
    def test_plain_passthrough(self):
        self.assertEqual(R.stamp_text("1:2"), "1:2")
        self.assertEqual(R.stamp_text("Проставка-полюсн\nик 210х10"), "Проставка-полюсн\nик 210х10")
        self.assertEqual(R.stamp_text(""), "")
    def test_field_scale_passes_ch06_ch08(self):
        f = facts(); f["stamp"]["scale"] = R.stamp_text("^(#3~1:2)1;1.000000;1~")
        self.assertEqual(fails(f) & {"Ч06", "Ч08"}, set())

def section_facts(dims):
    f = facts(dims=dims)
    f["views"].append({"idx": 3, "name": "Разрез А-А", "kind": "section", "scale": 0.5,
                       "scale_shown": False, "has_geometry": True,
                       "bbox_paper": [200, 50, 300, 100]})
    return f

class SectionDetail(unittest.TestCase):
    # Review Focus 2 (задача 4): разрезы/выносные судятся так же, как проекции
    def test_section_overlapping_texts_fail_ch12(self):
        f = section_facts([{"view": 3, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [250, 110]},
                           {"view": 3, "id": "lin1", "kind": "lin", "text": "50", "text_xy_paper": [252, 112]}])
        cs = {c["code"]: c for c in R.evaluate(f, RULES)}
        self.assertFalse(cs["Ч12"]["ok"])
        self.assertIn("v3.lin0~v3.lin1", cs["Ч12"]["detail"])
        self.assertNotIn("не проверено", cs["Ч12"]["detail"])
    def test_section_text_inside_bbox_not_judged_by_ch10(self):
        # bbox разреза — не контур (уступы, осевые, неклипованный выносной): живой Э0
        # дал 8 ложных Ч10 на 3 утверждённых чертежах (Фланец «8/36/18/58» под полкой)
        f = section_facts([{"view": 3, "id": "lin0", "kind": "lin", "text": "8", "text_xy_paper": [250, 75]}])
        cs = {c["code"]: c for c in R.evaluate(f, RULES)}
        self.assertTrue(cs["Ч10"]["ok"])
        self.assertIn("разрезы/выносные по Ч10 не судятся: 1", cs["Ч10"]["detail"])
        self.assertIn("текстов проверено: 1", cs["Ч12"]["detail"])
    def test_detail_clean_texts_pass_and_are_counted(self):
        f = section_facts([{"view": 3, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [250, 110]},
                           {"view": 3, "id": "lin1", "kind": "lin", "text": "50", "text_xy_paper": [250, 120]}])
        f["views"][-1]["kind"] = "detail"
        cs = {c["code"]: c for c in R.evaluate(f, RULES)}
        self.assertTrue(cs["Ч10"]["ok"]); self.assertTrue(cs["Ч12"]["ok"])
        self.assertIn("линейных проверено: 0", cs["Ч10"]["detail"])
        self.assertIn("текстов проверено: 2", cs["Ч12"]["detail"])
    def test_projection_still_judged_by_ch10(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [20, 60]}])
        cs = {c["code"]: c for c in R.evaluate(f, RULES)}
        self.assertIn("линейных проверено: 1", cs["Ч10"]["detail"])


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))
import types
import _draw as D

def view(x, y, s, a=0.0):
    return types.SimpleNamespace(X=x, Y=y, Scale=s, Angle=a)

class ViewToPaper(unittest.TestCase):
    # числа — живой Э0 (docs/experiments-2d.md)
    def test_unrotated_section(self):
        # Фланец крепления, Разрез А-А, lin0: X3,Y3 = (-7.19, 226) -> (242.8, 261.0)
        px, py = D.view_to_paper(view(250, 35, 1.0), -7.19, 226.0)
        self.assertAlmostEqual(px, 242.81, 2); self.assertAlmostEqual(py, 261.0, 2)
    def test_detail_scale(self):
        # Водило, Выносной элемент Г 2:1, lin0: (10.22, -33) -> (727.44, 94)
        px, py = D.view_to_paper(view(707, 160, 2.0), 10.22, -33.0)
        self.assertAlmostEqual(px, 727.44, 2); self.assertAlmostEqual(py, 94.0, 2)
    def test_rotated_90(self):
        # повёрнутый разрез Angle=90: бумага = (X - s*y, Y + s*x)
        px, py = D.view_to_paper(view(145, 165, 4.0, 90.0), 10.0, 2.0)
        self.assertAlmostEqual(px, 137.0, 6); self.assertAlmostEqual(py, 205.0, 6)

class DimTextPaper(unittest.TestCase):
    def test_x3_inside_gabarit_is_used(self):
        self.assertEqual(D.dim_text_paper(view(250, 35, 1.0), -7.19, 226.0,
                                          (137.5, 247.0, 362.5, 269.3)), [242.8, 261.0])
    def test_x3_outside_gabarit_falls_back_to_center(self):
        # СК не та (например, неучтённый поворот): точка вне своего габарита
        self.assertEqual(D.dim_text_paper(view(145, 165, 4.0), 10.0, 2.0,
                                          (100.0, 100.0, 120.0, 140.0)), [110.0, 120.0])
    def test_stale_zero_proxy_falls_back_to_center(self):
        # устаревший прокси читает X3=Y3=0 -> точка вида (v.X, v.Y), даже если она в габарите
        self.assertEqual(D.dim_text_paper(view(110, 100, 1.0), 0.0, 0.0,
                                          (90.0, 95.0, 130.0, 125.0)), [110.0, 110.0])
    def test_no_gabarit_keeps_x3(self):
        self.assertEqual(D.dim_text_paper(view(250, 35, 1.0), -7.19, 226.0, None), [242.8, 261.0])
    def test_stale_zero_proxy_without_gabarit_is_none(self):
        # X3=Y3=0 без габарита: точка вида (v.X, v.Y) — не текст; координат нет
        self.assertIsNone(D.dim_text_paper(view(110, 100, 1.0), 0.0, 0.0, None))


class TextsLeadersTitles(unittest.TestCase):
    # задача 14c: Ч12 судит тексты размеров, тексты на полках выносок и заголовки видов;
    # выноска «4 фаски 1,5х45°» — живой замер на копии .012 (вид 2)
    LED = {"view": 2, "id": "led0", "text": "4 фаски 1,5х45°",
           "text_rect_paper": [256.5, 281.0, 301.5, 289.1],
           "lines_paper": [[256.5, 281.0, 246.5, 269.5]]}

    def base(self, **kw):
        f = facts(**kw)
        f["views"][1]["bbox_paper"] = [200, 200, 320, 270]
        return f

    def ch12(self, f):
        return {c["code"]: c for c in R.evaluate(f, RULES)}["Ч12"]

    def test_leader_text_rect_live_numbers(self):
        self.assertEqual(R.leader_text_rect((246.5, 269.5, 301.5, 289.1), (256.5, 281.0), 1),
                         [256.5, 281.0, 301.5, 289.1])
        self.assertEqual(R.leader_text_rect((200.0, 269.5, 256.5, 289.1), (256.5, 281.0), -1),
                         [200.0, 281.0, 256.5, 289.1])
        self.assertIsNone(R.leader_text_rect(None, (256.5, 281.0), 1))
        self.assertIsNone(R.leader_text_rect((246.5, 269.5, 301.5, 289.1), (256.5, 281.0), 2))

    def test_seg_rect_cross(self):
        r = [0, 0, 10, 10]
        self.assertTrue(R.seg_rect_cross((-5, 5, 15, 5), r))      # насквозь
        self.assertTrue(R.seg_rect_cross((5, 5, 20, 20), r))      # из середины
        self.assertFalse(R.seg_rect_cross((-5, -5, -1, 20), r))   # мимо
        self.assertFalse(R.seg_rect_cross((10, -5, 10, 20), r))   # по границе — касание
        self.assertFalse(R.seg_rect_cross((12, 12, 20, 20), r))

    def test_clean_leader_counted_and_passes(self):
        f = self.base(leaders=[dict(self.LED)],
                      dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [100, 40]}])
        c = self.ch12(f)
        self.assertTrue(c["ok"], c["detail"])
        self.assertIn("текстов проверено: 2", c["detail"])

    def test_own_branch_not_against_own_text(self):
        led = dict(self.LED, lines_paper=[[256.5, 281.0, 290.0, 285.0]])   # ветвь под своим текстом
        self.assertTrue(self.ch12(self.base(leaders=[led]))["ok"])

    def test_dim_text_on_leader_text_fails(self):
        f = self.base(leaders=[dict(self.LED)],
                      dims=[{"view": 1, "id": "lin3", "kind": "lin", "text": "22,6", "text_xy_paper": [270, 286]}])
        c = self.ch12(f)
        self.assertFalse(c["ok"]); self.assertIn("v1.lin3~v2.led0", c["object"])

    def test_dim_text_on_leader_branch_fails(self):
        f = self.base(leaders=[dict(self.LED)],
                      dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "7", "text_xy_paper": [251.5, 275.0]}])
        c = self.ch12(f)
        self.assertFalse(c["ok"]); self.assertIn("v1.lin0~v2.led0(линия)", c["object"])

    def test_view_title_on_leader_fails(self):
        f = self.base(leaders=[dict(self.LED)])
        f["views"].append({"idx": 3, "name": "Разрез А-А", "kind": "section", "scale": 0.5,
                           "scale_shown": False, "has_geometry": True, "bbox_paper": [240, 200, 300, 250],
                           "title_rect_paper": [245.0, 270.0, 265.0, 284.3]})
        c = self.ch12(f)
        self.assertFalse(c["ok"])
        self.assertIn("v2.led0~v3.title", c["object"])
        self.assertIn("v3.title~v2.led0(линия)", c["object"])
        self.assertIn("leaders[].shelf", c["fix_hint"])

    def test_zero_title_rect_ignored(self):
        f = self.base()
        f["views"][0]["title_rect_paper"] = [0.0, 0.0, 0.0, 0.0]   # проекция без заголовка
        c = self.ch12(f)
        self.assertTrue(c["ok"]); self.assertIn("текстов проверено: 0", c["detail"])


class Sheets(unittest.TestCase):                  # задача 14d: два листа
    def two(self, **kw):
        f = facts(formats=[3, 3], **kw)
        f["views"][0]["sheet"] = 1
        f["views"][1]["sheet"] = 2
        f["views"][1]["bbox_paper"] = [470, 50, 570, 100]
        return f

    def test_ranges_and_sheet_of_x(self):
        r = R.sheet_x_ranges([3, 3])
        self.assertEqual(r, [(0.0, 420.0), (420.0, 840.0)])
        self.assertEqual(R.sheet_x_ranges([4, 3], [True, False]), [(0.0, 210.0), (210.0, 630.0)])
        self.assertEqual([R.sheet_of_x(x, r) for x in (10, 419.9, 420, 700, 900, -5)],
                         [1, 1, 2, 2, 2, 1])

    def test_clean_two_sheets_pass(self):
        self.assertEqual(fails(self.two()), set())

    def test_ch01_per_sheet(self):
        c = [c for c in R.evaluate(facts(formats=[3, 2]), RULES) if c["code"] == "Ч01"][0]
        self.assertFalse(c["ok"]); self.assertEqual(c["object"], "лист 2")

    def test_ra_only_on_sheet_1(self):
        f = self.two(sys_texts=["Ra 1,6"], sys_text_sheets=[2])
        c = [c for c in R.evaluate(f, RULES) if c["code"] == "Ч13"][0]
        self.assertFalse(c["ok"]); self.assertIn("лист 2", c["object"])
        f = self.two(sys_texts=["Ra 1,6", "Ra 1,6"], sys_text_sheets=[1, 2])
        self.assertIn("Ч13", fails(f))
        f = self.two(sys_texts=["Ra 1,6"], sys_text_sheets=[1])
        self.assertNotIn("Ч13", fails(f))

    def test_ch03_designation_on_every_sheet(self):     # 14d правка 1 (I1)
        f = self.two(sheet_markings=["PRJ.02.019", "PRJ.02.019"])
        self.assertNotIn("Ч03", fails(f))
        f = self.two(sheet_markings=["PRJ.02.019", ""])
        c = [c for c in R.evaluate(f, RULES) if c["code"] == "Ч03"][0]
        self.assertFalse(c["ok"]); self.assertEqual(c["object"], "лист 2, графа 2")
        f = self.two(sheet_markings=["PRJ.02.019", "PRJ.02.018"])
        self.assertIn("Ч03", fails(f))
        self.assertNotIn("Ч03", fails(facts()))               # один лист, без sheet_markings

    def test_sheet_frame(self):
        self.assertEqual(R.sheet_frame(3), (20.0, 5.0, 415.0, 292.0))
        self.assertEqual(R.sheet_frame(4, True), (20.0, 5.0, 205.0, 292.0))

    def test_ch12_not_across_sheets(self):
        # один и тот же бумажный прямоугольник на разных листах — не налезание
        dims = [{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [20, 60]},
                {"view": 2, "id": "lin0", "kind": "lin", "text": "50", "text_xy_paper": [21, 61]}]
        self.assertNotIn("Ч12", fails(self.two(dims=dims)))
        f = self.two(dims=dims); f["views"][1]["sheet"] = 1
        self.assertIn("Ч12", fails(f))


class Nonmetal(unittest.TestCase):
    def test_stamp_materials(self):
        for m in ("Капролон В ТУ 6-05-988-87", "Фторопласт-4 ГОСТ 10007-80", "Резина FKM"):
            self.assertTrue(R.is_nonmetal(m, RULES), m)
        for m in ("Сталь AISI 304", "АД0 ГОСТ 4784-2019", "", None):
            self.assertFalse(R.is_nonmetal(m, RULES), m)


# Ч16 (задача 15a): живые замеры видов в СК вида, мм модели
STOIKA_PLAN = {  # ACME.100.01.01.009 Стойка корпуса, «Снизу 1» 1:1, сборка до исправления
    "circles": [[0, -25, 5.5], [55, 0, 3], [25, 0, 5.5], [0, 25, 11], [-25, 0, 5.5], [-25, 0, 11],
                [0, -25, 11], [25, 0, 11], [-55, 0, 3], [0, 25, 5.5]],
    "arcs": [[55, 0, 10, 180.0], [0, 0, 45, 154.3], [0, 0, 45, 154.3], [-55, 0, 10, 180.0]],
    "centre_markers": [[-55, 0], [-25, 0], [0, -25], [0, 25], [25, 0], [55, 0]],
    "axes": []}
PILOT_PLAN = {   # PRJB.001.100.002 Пластина слотовая, «Проекционный вид 2» 1:2,5 (эталон)
    "circles": [[-293, -74, 3.621], [283, -74, 3.621], [-293, -74, 1.621], [283, 76, 1.621],
                [283, 76, 3.621], [-293, 76, 1.621], [283, -74, 1.621], [-293, 76, 3.621]],
    "arcs": [], "centre_markers": [[-293, -74], [-293, 76], [283, -74], [283, 76]],
    "axes": [[-327, 1, 317, 1], [-5, -127, -5, 129]]}


def centre_facts(kind="proj", **geo):
    f = facts()
    f["views"][1].update(kind=kind, **geo)
    return f


class Centres(unittest.TestCase):
    def ch16(self, f):
        return [c for c in R.evaluate(f, RULES) if c["code"] == "Ч16"][0]
    def test_pilot_passes(self):
        c = self.ch16(centre_facts(**PILOT_PLAN))
        self.assertTrue(c["ok"], c)
    def test_stoika_without_symmetry_axes_fails_at_contour_arc_centre(self):
        c = self.ch16(centre_facts(**STOIKA_PLAN))
        self.assertFalse(c["ok"])
        self.assertIn("v2", c["object"])
        self.assertIn("(0,0)", c["detail"])
    def test_stoika_with_symmetry_axes_passes(self):
        geo = dict(STOIKA_PLAN, axes=[[-69, 0, 69, 0], [0, -49, 0, 49]])
        self.assertTrue(self.ch16(centre_facts(**geo))["ok"])
    def test_hole_without_marker_fails(self):
        geo = dict(PILOT_PLAN, centre_markers=PILOT_PLAN["centre_markers"][1:])
        c = self.ch16(centre_facts(**geo))
        self.assertFalse(c["ok"]); self.assertIn("(-293,-74)", c["detail"])
    def test_one_axis_is_not_a_centre(self):
        geo = {"circles": [[0, 0, 5]], "arcs": [], "centre_markers": [], "axes": [[-10, 0, 10, 0]]}
        self.assertFalse(self.ch16(centre_facts(**geo))["ok"])
    def test_axis_must_reach_centre(self):
        geo = {"circles": [[0, 0, 5]], "arcs": [], "centre_markers": [],
               "axes": [[-10, 0, 10, 0], [0, 20, 0, 40]]}
        self.assertFalse(self.ch16(centre_facts(**geo))["ok"])
    def test_one_axis_marker_is_not_a_cross(self):      # задача 15b: крест в ОБЕ стороны
        geo = {"circles": [[25, 0, 5.5]], "arcs": [], "centre_markers": [[25, 0, 1]],
               "axes": [[-68, 0, 68, 0]]}                  # ksCMOneAxis + ось симметрии
        c = self.ch16(centre_facts(**geo))
        self.assertFalse(c["ok"]); self.assertIn("(25,0)", c["detail"])
    def test_two_axis_marker_on_symmetry_axis_passes(self):
        geo = {"circles": [[25, 0, 5.5]], "arcs": [], "centre_markers": [[25, 0, 2]],
               "axes": [[-68, 0, 68, 0]]}
        self.assertTrue(self.ch16(centre_facts(**geo))["ok"])
    def test_crossing_axes_must_span_the_circle(self):
        short = {"circles": [[0, 0, 5]], "arcs": [], "centre_markers": [],
                 "axes": [[-3, 0, 3, 0], [0, -3, 0, 3]]}    # крестик внутри отверстия
        self.assertFalse(self.ch16(centre_facts(**short))["ok"])
        full = dict(short, axes=[[-8, 0, 8, 0], [0, -8, 0, 8]])
        self.assertTrue(self.ch16(centre_facts(**full))["ok"])
    def test_fillet_arcs_not_judged(self):
        geo = {"circles": [], "arcs": [[5, 5, 5, 90.0]], "centre_markers": [], "axes": []}
        self.assertTrue(self.ch16(centre_facts(**geo))["ok"])
    def test_sections_and_old_facts_not_judged(self):
        self.assertTrue(self.ch16(centre_facts(kind="section", **STOIKA_PLAN))["ok"])
        self.assertTrue(self.ch16(facts())["ok"])
    def test_arc_sweep_follows_mid_point(self):
        a1, sw = R.arc_sweep(0, 0, 10, 0, -10, 0, 0, 10)       # верхняя полуокружность
        self.assertAlmostEqual(sw, 180.0); self.assertAlmostEqual(a1, 0.0)
        a1, sw = R.arc_sweep(0, 0, 10, 0, 0, 10, -10, 0)       # по часовой через низ: 270°
        self.assertAlmostEqual(sw, 270.0); self.assertAlmostEqual(a1, 90.0)


if __name__ == "__main__":
    unittest.main()


class IntentHints(unittest.TestCase):
    """Spec по утверждённой таблице замысла (composer.py): Ø на ступенях и общий Ra — решения."""

    def facts(self, **hints):
        return {"hints": hints}

    def test_ra_spec_overrides_default(self):
        import drawing_rules as R
        rules = R.load_rules()
        f = {"views": [], "dims": [], "sys_texts": ["Ra 6,3"], "stamp": {"marking": "X", "material": "12Х18Н10Т ГОСТ 5632-2014"},
             "hints": {"ra_spec": "Ra 6,3"}}
        ch = [c for c in R.evaluate(f, rules) if c["code"] == "Ч13"]
        self.assertTrue(ch and ch[0]["ok"], ch)
        f["hints"] = {}
        ch = [c for c in R.evaluate(f, rules) if c["code"] == "Ч13"]
        self.assertFalse(ch[0]["ok"])            # без таблицы замысла — закон проекта Ra 1,6

    def test_dia_on_image_skips_ch10(self):
        import drawing_rules as R
        rules = R.load_rules()
        f = {"views": [{"idx": 1, "name": "Вид", "kind": "proj", "bbox_paper": [0, 0, 100, 50],
                        "has_geometry": True, "scale": 0.5}],
             "dims": [{"view": 1, "id": "lin0", "kind": "lin", "text": "80", "sign": 1,
                       "text_xy_paper": [50, 25]}], "sys_texts": [], "stamp": {}}
        ch10 = lambda: [c for c in R.evaluate(f, rules) if c["code"] == "Ч10"][0]["ok"]
        self.assertFalse(ch10())
        f["hints"] = {"dia_on_image": True}
        self.assertTrue(ch10())


class TitleHits(unittest.TestCase):
    """Ч19 (шаг 2г): заголовок вида не пересекает обозначения — жёстко (FAIL)."""

    def test_ch19_fail_and_ok(self):
        import drawing_rules as R
        rules = R.load_rules()
        base = {"views": [], "dims": [], "sys_texts": [], "stamp": {}}
        bad = [c for c in R.evaluate(dict(base, title_hits=["заголовок вида 2 «Разрез Е-Е» × v1.lin9"]), rules)
               if c["code"] == "Ч19"]
        self.assertFalse(bad[0]["ok"])
        ok = [c for c in R.evaluate(dict(base, title_hits=[]), rules) if c["code"] == "Ч19"]
        self.assertTrue(ok[0]["ok"])

    def test_seg_hits_rect(self):
        import drawing_rules as R
        self.assertTrue(R.seg_hits_rect((0, 5, 20, 5), (5, 0, 10, 10)))
        self.assertFalse(R.seg_hits_rect((0, 20, 20, 20), (5, 0, 10, 10)))

    def test_ch18_ignores_form_tolerances(self):
        import drawing_rules as R
        rules = R.load_rules()
        f = {"views": [{"idx": 1, "name": "В", "kind": "proj", "bbox_paper": [0, 0, 1, 1]}], "dims": [],
             "sys_texts": [], "stamp": {}, "tolerances": [{"view": 1, "id": "tol0", "sign": 28}]}
        self.assertFalse([c for c in R.evaluate(f, rules) if c["code"] == "Ч18"])


class AnnotationHits(unittest.TestCase):
    def test_letter_on_dim_text_and_own_lines_ignored(self):
        import drawing_rules as R
        parts = {"v1.lin0": ([(30, 10, 30, 60)], [(25, 30, 30, 40)]),       # Ø110: линия + текст
                 "v1.cut0": ([(40, 35, 48, 35)], [(28, 31, 32, 37)]),       # буква «Е» на тексте Ø110
                 "v1.lin1": ([(100, 10, 100, 60)], [(95, 30, 100, 40)])}
        hits = R.annotation_hits(parts)
        self.assertIn("v1.cut0 × v1.lin0", hits)
        self.assertFalse(any(h.startswith("v1.lin1") for h in hits))       # свой текст на своей линии — норма
