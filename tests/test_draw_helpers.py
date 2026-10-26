# tests/test_draw_helpers.py — чистые функции jobs/_draw.py (без КОМПАС).
# Числа ТТ — живой Э3 (docs/experiments-2d.md, «Э3», А3 с двумя видами).
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))
import _draw as D


class ClipRect(unittest.TestCase):
    def test_detail_plate_4to1(self):
        # живой замер 14b, Пластина прижимная, выносной Б 4:1: геометрия в СК вида
        # (2,-19)-(968,-3) — вся пластина; круг выносного (69,-11), r = 2,0/0,25 = 8
        r = D.clip_rect((2.0, -19.0, 968.0, -3.0), D.circle_rect(69.0, -11.0, 8.0))
        self.assertEqual(r, (61.0, -19.0, 77.0, -3.0))
    def test_disjoint(self):
        self.assertIsNone(D.clip_rect((0, 0, 1, 1), (2, 2, 3, 3)))


class TitleLift(unittest.TestCase):
    def test_stoika_section_title_under_d90(self):
        # живой замер, Стойка корпуса: «А-А» (130,246)-(150,260.3), Ø90 до 268.3
        title = (130.1, 246.0, 149.9, 260.3)
        rects = [(95.0, 242.0, 185.0, 268.3), (153.5, 190.0, 201.5, 260.3)]
        self.assertAlmostEqual(D.title_lift(title, rects), 25.3)
    def test_already_above(self):
        self.assertLessEqual(D.title_lift((0, 280, 10, 290), [(0, 200, 10, 270)]), 0)
        self.assertEqual(D.title_lift((0, 280, 10, 290), []), 0.0)
    def test_side_block_does_not_lift(self):
        # блок полки правее заголовка (эталон: Ø85 слева-сверху от зоны А) — подъём не нужен
        self.assertEqual(D.title_lift((130.0, 246.0, 150.0, 260.3), [(160.0, 240.0, 200.0, 280.0)]), 0.0)
        self.assertGreater(D.title_lift((130.0, 246.0, 150.0, 260.3), [(140.0, 240.0, 200.0, 280.0)]), 0)


class TTArea(unittest.TestCase):
    def test_a4_vertical(self):
        a = D.tt_area(4, True)
        self.assertEqual((a["x1"], a["x2"], a["y1"], a["ytop"]), (25.0, 200.0, 65.0, 285.0))
        self.assertEqual(a["frame"], (20.0, 5.0, 205.0, 292.0))
    def test_a3_horizontal(self):
        a = D.tt_area(3, False)
        self.assertEqual((a["x1"], a["x2"], a["y1"], a["ytop"]), (235.0, 410.0, 65.0, 285.0))
        self.assertEqual(a["frame"], (20.0, 5.0, 415.0, 292.0))

class TTBox(unittest.TestCase):
    def test_height_7k_plus_4(self):
        # Э3: k=4 -> (235,65,410,97); k=18 -> верх 195; k=20 -> верх 209
        self.assertEqual(D.tt_box(235.0, 410.0, 4), (235.0, 65.0, 410.0, 97.0))
        self.assertEqual(D.tt_box(235.0, 410.0, 18)[3], 195.0)
        self.assertEqual(D.tt_box(255.0, 410.0, 20), (255.0, 65.0, 410.0, 209.0))

class Rects(unittest.TestCase):
    def test_inside_frame(self):
        f = (20.0, 5.0, 415.0, 292.0)
        self.assertTrue(D.rect_inside((235, 65, 410, 97), f))
        self.assertFalse(D.rect_inside((-180, 20, -5, 277), f))   # AutoPlacement на А4
    def test_cross_with_gap(self):
        plan, main = (35, 150, 245, 160), (35, 200, 245, 209)
        block = (235, 65, 410, 195)
        self.assertTrue(D.rects_cross(block, plan, 5.0))
        self.assertFalse(D.rects_cross(block, main, 5.0))   # 200 = 195 + 5: касание не считается

class TTFallback(unittest.TestCase):
    VIEWS = [(35, 200, 245, 209), (35, 150, 245, 160)]
    def test_no_cross_no_fallback(self):
        self.assertIsNone(D.tt_fallback_x1((235, 65, 410, 139), self.VIEWS))
    def test_cross_moves_right_of_views(self):
        # Э3, 15 пунктов: блок задел план -> x1 = 245 + 10 = 255
        self.assertEqual(D.tt_fallback_x1((235, 65, 410, 195), self.VIEWS), 255.0)
    def test_only_views_in_block_band_count(self):
        # вид выше полосы блока (y 250..260) правее — не влияет на x1
        views = self.VIEWS + [(35, 250, 300, 260)]
        self.assertEqual(D.tt_fallback_x1((235, 65, 410, 195), views), 255.0)
    def test_fallback_too_narrow_raises(self):
        # правый край видов 345 -> x1 355 > 410 - 100: блок уже минимума -> RuntimeError
        views = [(35, 150, 345, 160)]
        with self.assertRaises(RuntimeError):
            D.tt_fallback_x1((235, 65, 410, 195), views, x2=410.0)
        self.assertEqual(D.tt_fallback_x1((235, 65, 410, 195), self.VIEWS, x2=410.0), 255.0)

class LinOrientation(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(D.lin_orientation(2, None, 10.0), 2)
    def test_y3_only_is_horizontal(self):
        self.assertEqual(D.lin_orientation(None, None, -15.0), 1)
    def test_x3_only_is_vertical(self):
        self.assertEqual(D.lin_orientation(None, 222.0, None), 2)
    def test_ambiguous_raises(self):
        with self.assertRaises(ValueError):
            D.lin_orientation(None, 1.0, 2.0)
        with self.assertRaises(ValueError):
            D.lin_orientation(None, None, None)

class DiaSign(unittest.TestCase):
    def test_diameter_prefix_becomes_sign(self):
        self.assertEqual(D.split_dia_sign("Ø"), ("", True))
        self.assertEqual(D.split_dia_sign("2 отв. Ø"), ("2 отв. ", True))
        self.assertEqual(D.split_dia_sign("⌀"), ("", True))
    def test_plain_prefix_untouched(self):
        self.assertEqual(D.split_dia_sign("2 отв. "), ("2 отв. ", False))
        self.assertEqual(D.split_dia_sign(""), ("", False))

class GostText(unittest.TestCase):
    def test_times_and_dashes(self):
        # «×» рисуется «Ч», «–»/«—» — квадратом (память чертёжника)
        self.assertEqual(D.gost_text("М10-6Hх12"), "М10-6Hх12")
        self.assertEqual(D.gost_text("М10-6H×12"), "М10-6Hх12")
        self.assertEqual(D.gost_text("ГОСТ 30893.1 – m — тест"), "ГОСТ 30893.1 - m - тест")

class StampCell(unittest.TestCase):
    def test_empty(self):
        self.assertTrue(D.stamp_cell_empty(""))
        self.assertTrue(D.stamp_cell_empty("  "))
        self.assertTrue(D.stamp_cell_empty(None))
    def test_field_with_value_is_filled(self):
        # графа «Масштаб» после AddStandartViews — поле-ссылка
        self.assertFalse(D.stamp_cell_empty("^(#5~1:2,5)1;288314011823.000000;1~"))
        self.assertFalse(D.stamp_cell_empty("0,15"))
    def test_override_name_marking_material_always(self):
        # задача 13a: явные 1/2/3 перебивают значения модели; 5–8 — только пустые
        for n in (1, 2, 3):
            self.assertTrue(D.stamp_should_write(n, "PRJ.02.016"))
            self.assertTrue(D.stamp_should_write(n, ""))
        self.assertFalse(D.stamp_should_write(6, "^(#5~1:2,5)1;288314011823.000000;1~"))
        self.assertTrue(D.stamp_should_write(7, ""))


def _rect(x0, y0, x1, y1):
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


def _circle(xc, yc, r):
    return D._polyline(D.arc_points(xc, yc, r, 0.0, 6.283185307179586, 24))


class DetailShelf(unittest.TestCase):
    # задача 14c: буква выносного не на контуре/штриховке; бумажные мм
    def test_default_kept_when_clear(self):
        segs = _rect(0, 0, 50, 20)
        sx, sy = D.detail_shelf(45.0, 10.0, 4.0, segs, (0, 0, 50, 20))
        self.assertEqual((sx, sy), (45.0 + 4 + 5, 10.0 + 4 + 5))   # как раньше: xc+r+5

    def test_section_012_default_on_hatching_moves_below(self):
        # .012, разрез А-А 2:1 у (60,125): круг Б у (76,126) R 4, материал до y 150;
        # умолчание (85,135) — буква на штриховке (ревьюер), ушла под вид
        segs = _rect(60, 125, 120, 150)
        sx, sy = D.detail_shelf(76.0, 126.0, 4.0, segs, (60, 125, 199.4, 150))
        self.assertEqual(sx, 85.0)
        self.assertLess(sy + D.SHELF_BOX_H, 125.0)

    def test_bushing_014_ring_moves_off_material(self):
        # .014, вид сверху 5:1 у (200,190): кольцо R 22,5 / 13,25; Б у (200,172) R 7
        segs = _circle(200, 190, 22.5) + _circle(200, 190, 13.25)
        sx, sy = D.detail_shelf(200.0, 172.0, 7.0, segs, (177.5, 167.5, 222.5, 212.5))
        box = (sx - 1, sy - 1, sx + D.SHELF_BOX_W + 1, sy + D.SHELF_BOX_H + 1)
        R = D._rules()
        self.assertFalse(any(R.seg_rect_cross(s_, box) for s_ in segs))
        self.assertFalse(R.rects_cross(box, (177.5, 167.5, 222.5, 212.5)))

    def test_avoids_side_of_view_dims(self):
        # .011: полоса 12 мм, Б у нижней кромки, размеры вида — снизу (буква легла на «145,3»)
        segs = _rect(0, 0, 200, 12)
        self.assertEqual(D.detail_shelf(70.0, 0.0, 1.5, segs, (0, 0, 200, 12)), (76.5, -21.8))
        self.assertEqual(D.detail_shelf(70.0, 0.0, 1.5, segs, (0, 0, 200, 12), avoid={"bottom"}),
                         (86.5, 16.5))

    def test_odd_crossings(self):
        segs = _rect(0, 0, 10, 10) + _rect(3, 3, 7, 7)       # кольцо: дыра в середине
        self.assertTrue(D.odd_crossings(1.5, 5.0, segs))
        self.assertFalse(D.odd_crossings(5.0, 5.0, segs))
        self.assertFalse(D.odd_crossings(15.0, 5.0, segs))


class PaperToView(unittest.TestCase):
    def test_roundtrip(self):
        import types
        for a in (0.0, 90.0, -30.0):
            v = types.SimpleNamespace(X=60.0, Y=125.0, Scale=2.0, Angle=a)
            px, py = D.view_to_paper(v, 12.5, 5.0)
            x, y = D.paper_to_view(v, px, py)
            self.assertAlmostEqual(x, 12.5, 9); self.assertAlmostEqual(y, 5.0, 9)


class ShelfDir(unittest.TestCase):                # задача 14d: полка при любом угле
    def test_dia_angles(self):
        import math
        want = {315: 1, 45: 1, 0: 1, 90: 1, 270: 1, 225: -1, 135: -1, 180: -1}
        for a, d in want.items():
            self.assertEqual(D.shelf_dir(math.cos(math.radians(a))), d, a)

    def test_leader_away_from_arrow(self):
        self.assertEqual(D.shelf_dir(15.0), 1)       # полка правее стрелки — вправо
        self.assertEqual(D.shelf_dir(-15.0), -1)     # левее — влево (не на ветвь)
        self.assertEqual(D.shelf_dir(0.0), 1)


def rect(x0, y0, x1, y1):
    return [("seg", x0, y0, x1, y0), ("seg", x1, y0, x1, y1), ("seg", x1, y1, x0, y1),
            ("seg", x0, y1, x0, y0)]


class SymmetryAxes(unittest.TestCase):            # задача 15a: оси симметрии проекций
    def test_stoika_plan_both_axes_3mm_over(self):
        # «Снизу 1» 1:1: контур R45 + ушки R10 на ±55, отверстия ±25 / ±55 (замер задачи 15a)
        prims = [("circ", x, y, r) for x, y, r in ((0, -25, 5.5), (55, 0, 3), (25, 0, 5.5),
                 (0, 25, 11), (-25, 0, 5.5), (-25, 0, 11), (0, -25, 11), (25, 0, 11),
                 (-55, 0, 3), (0, 25, 5.5))]
        prims += [("arc", 55, 0, 10, -90.0, 180.0), ("arc", -55, 0, 10, 90.0, 180.0),
                  ("arc", 0, 0, 45, 12.84, 154.32), ("arc", 0, 0, 45, 192.84, 154.32),
                  ("seg", 43.87, 10, 55, 10), ("seg", 43.87, -10, 55, -10),
                  ("seg", -43.87, 10, -55, 10), ("seg", -43.87, -10, -55, -10)]
        got = D.symmetry_axes(prims, 1.0)
        self.assertEqual(sorted(got), [(-68.0, 0.0, 68.0, 0.0), (0.0, -48.0, 0.0, 48.0)])
    def test_asymmetric_hole_keeps_one_axis(self):
        prims = rect(0, 0, 100, 40) + [("circ", 20, 20, 3)]
        self.assertEqual(D.symmetry_axes(prims, 0.5), [(-6.0, 20.0, 106.0, 20.0)])
    def test_thin_edge_view_no_axes(self):
        # пилот PRJB.001.100.002 «Спереди 1» 1:2,5: торец 624×6 — без осей (6·0,4 < 10 мм)
        self.assertEqual(D.symmetry_axes(rect(-317, 0, 307, 6), 0.4), [])
    def test_existing_axis_not_duplicated(self):
        prims = rect(0, 0, 100, 40)
        got = D.symmetry_axes(prims, 1.0, have=[(-3, 20, 103, 20)])
        self.assertEqual(got, [(50.0, -3.0, 50.0, 43.0)])


class AxisJutAndRadArrow(unittest.TestCase):     # задача 15b, живые замеры Стойки 1:1
    def test_axes_overshoot_3mm(self):
        # ГОСТ 2.303: 2–5 мм; умолчание КОМПАС JutLength 2 (разрез А-А: 1,9–2,1 мм на 300 dpi)
        self.assertEqual(D.AXIS_JUT, 3.0)
        self.assertEqual(D.SYM_AXIS_OVERRUN, D.AXIS_JUT)
    def test_rad_arrow_outside(self):
        # ksDimArrowOutside: остриё на дуге снаружи, к центру (Р19); 0 (Inside) — стрелка
        # внутри дуги остриём наружу, линия идёт сквозь дугу к центру (R10 ушка)
        self.assertEqual(D.RAD_ARROW_POS, 1)


if __name__ == "__main__":
    unittest.main()


class AxisGaps(unittest.TestCase):
    def test_shaft_axis_broken_on_thread(self):
        # Вал_v6: автоосевая КОМПАС 0…457,2 и 505…579 — перемычка через участок резьбы
        self.assertEqual(D.axis_gaps([(0, 0, 457.2, 0), (505, 0, 579, 0)]), [(457.2, 0.0, 505, 0.0)])
    def test_far_axes_not_joined(self):
        self.assertEqual(D.axis_gaps([(0, 0, 10, 0), (100, 0, 110, 0)]), [])
    def test_vertical_and_parallel(self):
        self.assertEqual(D.axis_gaps([(5, -10, 5, 10), (5, 20, 5, 30)]), [(5.0, 10, 5.0, 20)])
        self.assertEqual(D.axis_gaps([(0, 0, 50, 0), (60, 5, 100, 5)]), [])


class TTKeep(unittest.TestCase):
    def test_gost_number_not_split(self):
        s = D.tt_keep("1. Неуказанные — по ГОСТ 30893.1: H14. ГОСТ Р 2.308")
        self.assertIn("ГОСТ 30893.1", s)
        self.assertIn("ГОСТ Р 2.308", s)
        self.assertEqual(D.tt_keep("Острые кромки притупить."), "Острые кромки притупить.")


class BreakView(unittest.TestCase):
    """Разрыв вида (пилот PRJ.02 .008): зона 293…677 модели, зазор 10 мм листа при 1:2,5 = 25."""
    Z = [(293.0, 677.0, 25.0)]

    def test_break_u_shifts_right_part(self):
        self.assertEqual(D.break_u(self.Z, 100.0), 100.0)
        self.assertAlmostEqual(D.break_u(self.Z, 709.0), 709.0 - 384.0 + 25.0)
        self.assertAlmostEqual(D.break_u(self.Z, 970.0), 611.0)
        self.assertIsNone(D.break_u(self.Z, 485.0))
        self.assertEqual(D.break_u([], 485.0), 485.0)

    def test_segment_across_break_splits(self):
        parts = D._bk_seg(self.Z, 2.0, 60.0, 968.0, 60.0)
        self.assertEqual(len(parts), 2)
        (a1, _, b1, _), (a2, _, b2, _) = parts
        self.assertEqual((a1, b1), (2.0, 293.0))
        self.assertAlmostEqual(a2, 293.0 + 25.0)
        self.assertAlmostEqual(b2, 968.0 - 359.0)

    def test_segment_inside_break_drops(self):
        self.assertEqual(D._bk_seg(self.Z, 300.0, 0.0, 600.0, 0.0), [])

    def test_vertical_segment_right_of_break(self):
        (x1, y1, x2, y2), = D._bk_seg(self.Z, 965.0, -55.0, 965.0, 55.0)
        self.assertAlmostEqual(x1, 606.0)
        self.assertEqual((y1, y2), (-55.0, 55.0))


class Deviations(unittest.TestCase):
    def test_parts(self):
        self.assertEqual(D.dev_parts("+0,2"), ("+0,2", "0"))
        self.assertEqual(D.dev_parts("-0,1"), ("0", "-0,1"))
        self.assertEqual(D.dev_parts("±0,05"), ("+0,05", "-0,05"))
        self.assertEqual(D.dev_parts("+0.2/-0.1"), ("+0,2", "-0,1"))
        self.assertEqual(D.dev_parts("−0,1"), ("0", "-0,1"))


class TimesSign(unittest.TestCase):
    class Line:
        """Заглушка ITextLine: элементы со Str и шрифтом."""
        def __init__(self):
            self.items, self.Str = [], ""

        def Add(self):
            it = type("It", (), {"Str": "", "Update": lambda self: None})()
            self.items.append(it)
            return it

    def test_times_split_by_font(self):
        ln = self.Line()
        orig = D.CastTo
        D.CastTo = lambda o, name: o           # ITextFont — тот же объект-заглушка
        try:
            D.set_text(ln, "15×64=")
        finally:
            D.CastTo = orig
        self.assertEqual([(i.Str, i.FontName) for i in ln.items],
                         [("15", D.GOST_FONT), ("×", D.TIMES_FONT), ("64=", D.GOST_FONT)])

    def test_plain_text_keeps_gost_subst(self):
        ln = self.Line()
        D.set_text(ln, "2-0,1")
        self.assertEqual(ln.Str, "2-0,1")
