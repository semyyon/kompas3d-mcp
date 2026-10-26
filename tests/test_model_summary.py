# tests/test_model_summary.py
"""model_summary: сводка геометрии детали из синтетических списков граней (без COM)."""
import json, math, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import model_summary as MS


def ring(c, axis, r, start=0.0, span=2 * math.pi):
    """3 точки окружности радиуса r вокруг оси (axis — 'x'|'y'|'z') с центром c —
    как их снимает server.py: u0, u0+span/3, u0+2span/3."""
    k = "xyz".index(axis)
    i, j = [m for m in range(3) if m != k]
    pts = []
    for t in (start, start + span / 3, start + 2 * span / 3):
        p = list(c)
        p[i] += r * math.cos(t)
        p[j] += r * math.sin(t)
        pts.append(p)
    return pts


def cyl(c0, c1, d, axis="z", inner=True, span=2 * math.pi):
    """Цилиндрическая грань от центра c0 до центра c1. inner — нормаль внутрь (отверстие)."""
    r = d / 2
    k = "xyz".index(axis)
    i = [m for m in range(3) if m != k][0]
    mid = [(a + b) / 2 for a, b in zip(c0, c1)]
    pt = list(mid); pt[i] += r
    nrm = [0.0, 0.0, 0.0]; nrm[i] = -1.0 if inner else 1.0
    gab = [min(a, b) - (0 if m == k else r) for m, (a, b) in enumerate(zip(c0, c1))] +           [max(a, b) + (0 if m == k else r) for m, (a, b) in enumerate(zip(c0, c1))]
    return {"t": "cyl", "r": r, "span": span, "ring0": ring(c0, axis, r, 0, span),
            "ring1": ring(c1, axis, r, 0, span), "pt": pt, "nrm": nrm, "gab": gab}


def plane(gab, nrm, area):
    return {"t": "plane", "gab": list(gab), "nrm": list(nrm), "area": area}


def box(x2, y2, z2, x1=0.0, y1=0.0, z1=0.0, holes_area=0.0):
    """6 граней бруска; holes_area вычитается из верхней и нижней."""
    return [plane([x1, y1, z2, x2, y2, z2], [0, 0, 1], (x2 - x1) * (y2 - y1) - holes_area),
            plane([x1, y1, z1, x2, y2, z1], [0, 0, -1], (x2 - x1) * (y2 - y1) - holes_area),
            plane([x1, y1, z1, x1, y2, z2], [-1, 0, 0], (y2 - y1) * (z2 - z1)),
            plane([x2, y1, z1, x2, y2, z2], [1, 0, 0], (y2 - y1) * (z2 - z1)),
            plane([x1, y1, z1, x2, y1, z2], [0, -1, 0], (x2 - x1) * (z2 - z1)),
            plane([x1, y2, z1, x2, y2, z2], [0, 1, 0], (x2 - x1) * (z2 - z1))]


class Geometry(unittest.TestCase):
    def test_r3_rounds_and_kills_negative_zero(self):
        self.assertEqual(MS.r3(52.50000000001), 52.5)
        self.assertEqual(MS.r3(-1e-17), 0.0)
        self.assertEqual(str(MS.r3(-1e-17)), "0.0")

    def test_circle_center_from_three_points(self):
        c = MS.circle_center(*ring([52.5, 0, 9], "z", 1.65))
        self.assertEqual([MS.r3(v) for v in c], [52.5, 0.0, 9.0])
        # дуга 90° (не полная окружность) — центр тот же
        c = MS.circle_center(*ring([10, -3, 2], "x", 4, start=1.0, span=math.pi / 2))
        self.assertEqual([MS.r3(v) for v in c], [10.0, -3.0, 2.0])

    def test_bbox_is_union_of_face_boxes(self):
        faces = box(210, 5, 9, y1=-5) + [cyl([52.5, 0, 0], [52.5, 0, 9], 3.3)]
        self.assertEqual(MS.bbox_of(faces), [0.0, -5.0, 0.0, 210.0, 5.0, 9.0])


class Holes(unittest.TestCase):
    def test_spacer_through_holes_grouped(self):
        # Проставка 210х10 (живые числа 2026-10-14): 210×10×9, 2 отв. Ø3,3 насквозь по Z
        faces = box(210, 5, 9, y1=-5, holes_area=2 * math.pi * 1.65 ** 2) + [
            cyl([52.5, 0, 0], [52.5, 0, 9], 3.3), cyl([157.5, 0, 0], [157.5, 0, 9], 3.3)]
        s = MS.summarize(faces, drill_to_thread={"3.3": "резьба М4"})
        self.assertEqual(len(s["holes"]), 1)
        g = s["holes"][0]
        self.assertEqual(g["d"], 3.3)
        self.assertEqual(g["n"], 2)
        self.assertEqual(g["axis"], [0.0, 0.0, 1.0])
        self.assertEqual(g["depth"], 9.0)
        self.assertTrue(g["through"])
        self.assertEqual(g["entry"], "z=9.0")
        self.assertEqual(g["centers_top"], [[52.5, 0.0, 9.0], [157.5, 0.0, 9.0]])
        self.assertEqual(s["bosses"], [])
        self.assertTrue(any("Ø3,3" in n and "М4" in n for n in s["notes"]), s["notes"])

    def test_blind_hole_flat_bottom_from_top(self):
        # брусок 30×20×20, Ø3,3 глубиной 8 сверху; дно — плоский диск Ø3,3 на z=12
        faces = box(30, 20, 20) + [
            cyl([15, 10, 12], [15, 10, 20], 3.3),
            plane([13.35, 8.35, 12, 16.65, 11.65, 12], [0, 0, 1], math.pi * 1.65 ** 2)]
        g = MS.summarize(faces)["holes"][0]
        self.assertFalse(g["through"])
        self.assertEqual(g["depth"], 8.0)
        self.assertEqual(g["axis"], [0.0, 0.0, 1.0])          # от дна к входу
        self.assertEqual(g["entry"], "z=20.0")
        self.assertEqual(g["centers_top"], [[15.0, 10.0, 20.0]])

    def test_blind_hole_from_bottom_with_drill_cone(self):
        # вход снизу (z=0), конус сверла у дна на z=8..9
        faces = box(30, 20, 20) + [
            cyl([15, 10, 0], [15, 10, 8], 3.3),
            {"t": "cone", "gab": [13.35, 8.35, 8, 16.65, 11.65, 8.95]}]
        g = MS.summarize(faces)["holes"][0]
        self.assertFalse(g["through"])
        self.assertEqual(g["axis"], [0.0, 0.0, -1.0])
        self.assertEqual(g["entry"], "z=0.0")
        self.assertEqual(g["centers_top"], [[15.0, 10.0, 0.0]])
        self.assertEqual(g["depth"], 8.0)

    def test_through_hole_from_pocket_floor_is_through(self):
        # отверстие от дна кармана (z=6) до низа (z=0): концы не на габарите сверху,
        # но заглушки нет ни с одной стороны → сквозное
        faces = box(30, 20, 20) + [
            plane([5, 5, 6, 25, 15, 6], [0, 0, 1], 200 - math.pi * 1.65 ** 2),
            cyl([15, 10, 0], [15, 10, 6], 3.3)]
        g = MS.summarize(faces)["holes"][0]
        self.assertTrue(g["through"])
        self.assertEqual(g["entry"], "z=6.0")

    def test_counterbore_step_is_blind_and_drill_is_through(self):
        # Ø6 глубиной 3 сверху (кольцо-уступ Ø6 на z=17), дальше Ø3,3 насквозь
        faces = box(30, 20, 20) + [
            cyl([15, 10, 17], [15, 10, 20], 6.0),
            plane([12, 7, 17, 18, 13, 17], [0, 0, 1], math.pi * (9 - 1.65 ** 2)),
            cyl([15, 10, 0], [15, 10, 17], 3.3)]
        holes = {g["d"]: g for g in MS.summarize(faces)["holes"]}
        self.assertFalse(holes[6.0]["through"])
        self.assertEqual(holes[6.0]["depth"], 3.0)
        self.assertTrue(holes[3.3]["through"])

    def test_different_depth_or_entry_split_groups(self):
        faces = box(100, 20, 20) + [
            cyl([10, 10, 12], [10, 10, 20], 3.3),
            plane([8.35, 8.35, 12, 11.65, 11.65, 12], [0, 0, 1], 8.55),
            cyl([30, 10, 10], [30, 10, 20], 3.3),
            plane([28.35, 8.35, 10, 31.65, 11.65, 10], [0, 0, 1], 8.55),
            cyl([50, 10, 12], [50, 10, 20], 3.3),
            plane([48.35, 8.35, 12, 51.65, 11.65, 12], [0, 0, 1], 8.55)]
        hs = MS.summarize(faces)["holes"]
        self.assertEqual(sorted((g["depth"], g["n"]) for g in hs), [(8.0, 2), (10.0, 1)])

    def test_side_hole_axis_x(self):
        faces = box(30, 20, 20) + [
            cyl([20, 10, 10], [30, 10, 10], 4.2, axis="x"),
            plane([20, 7.9, 7.9, 20, 12.1, 12.1], [1, 0, 0], 13.85)]
        g = MS.summarize(faces, drill_to_thread={"4.2": "резьба М5"})["holes"][0]
        self.assertEqual(g["axis"], [1.0, 0.0, 0.0])
        self.assertEqual(g["entry"], "x=30.0")
        self.assertEqual(g["depth"], 10.0)
        self.assertFalse(g["through"])

    def test_boss_and_partial_arcs(self):
        faces = box(30, 20, 20) + [
            cyl([15, 10, 20], [15, 10, 30], 8.0, inner=False),
            cyl([5, 5, 0], [5, 5, 20], 3.0, span=math.pi / 2),
            cyl([25, 5, 0], [25, 5, 20], 3.0, span=math.pi / 2)]
        s = MS.summarize(faces)
        self.assertEqual(s["holes"], [])
        self.assertEqual(len(s["bosses"]), 1)
        b = s["bosses"][0]
        self.assertEqual((b["d"], b["n"], b["length"]), (8.0, 1, 10.0))
        self.assertEqual(b["centers_top"], [[15.0, 10.0, 30.0]])
        self.assertEqual(s["arcs"], [{"r": 1.5, "n": 2, "inner": True}])


class PlanesNotesLimits(unittest.TestCase):
    def test_planes_levels_per_axis(self):
        faces = box(30, 20, 20) + [plane([5, 5, 6, 25, 15, 6], [0, 0, 1], 200.0),
                                   plane([0, 0, 0, 1, 1, 1], [0.6, 0.8, 0], 1.0)]
        p = MS.summarize(faces)["planes"]
        self.assertEqual(p["z"], [[0.0, 1, 600.0], [6.0, 1, 200.0], [20.0, 1, 600.0]])
        self.assertEqual(p["x"], [[0.0, 1, 400.0], [30.0, 1, 400.0]])
        self.assertEqual(p["other"], 1)

    def test_planes_capped_by_area(self):
        faces = [plane([0, 0, z, 10, 10, z], [0, 0, 1], 1.0 + z) for z in range(30)]
        p = MS.summarize(faces, max_levels=5)["planes"]
        self.assertEqual([lv[0] for lv in p["z"]], [25.0, 26.0, 27.0, 28.0, 29.0])
        self.assertIn("z", p["capped"])

    def test_max_items_truncates_centers_and_says_so(self):
        faces = box(1000, 20, 10) + [cyl([10 + 10 * i, 10, 0], [10 + 10 * i, 10, 10], 3.3)
                                     for i in range(50)]
        s = MS.summarize(faces, max_items=20)
        g = s["holes"][0]
        self.assertEqual(g["n"], 50)
        self.assertEqual(len(g["centers_top"]), 20)
        self.assertIn("max_items=20", s["truncated"])

    def test_drill_to_thread_notes_use_rules_table(self):
        faces = box(30, 20, 20) + [cyl([15, 10, 0], [15, 10, 20], 5.2)]
        rules = MS.load_drill_to_thread()
        self.assertIn("3.3", rules)
        s = MS.summarize(faces, drill_to_thread=rules)
        self.assertTrue(any("Ø5,2" in n and "вставк" in n for n in s["notes"]), s["notes"])

    def test_compact_json_for_typical_part(self):
        faces = box(210, 5, 9, y1=-5) + [cyl([52.5, 0, 0], [52.5, 0, 9], 3.3),
                                         cyl([157.5, 0, 0], [157.5, 0, 9], 3.3)]
        txt = json.dumps(MS.summarize(faces), ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(txt), 1500)


if __name__ == "__main__":
    unittest.main()


class Threads(unittest.TestCase):
    """Условные резьбы (ISymbols3DContainer.Threads) — вал ACME.107.002.004: M76×2 на гладком
    цилиндре, без них drawing-intent принял резьбу за шейку под ступицу (2026-10-19)."""

    def test_shaft_thread_fine_pitch(self):
        import model_summary as MS
        raw = [{"d": 76.0, "pitch": 2.0, "internal": False, "left": False, "length": 42.8,
                "gab": [462.2, -38.0, -38.0, 505.0, 38.0, 37.67], "std": "cil0_24705-2004 fine"}]
        t = MS.threads(raw)[0]
        self.assertEqual(t["designation"], "M76×2")
        self.assertEqual(t["tol_default"], "6g")
        self.assertEqual(t["axis"], "x")
        self.assertEqual(t["span"], [462.2, 505.0])

    def test_coarse_pitch_omitted_and_left_internal(self):
        import model_summary as MS
        t = MS.threads([{"d": 8, "pitch": 1.25, "internal": True, "left": True, "length": 12}])[0]
        self.assertEqual(t["designation"], "M8LH")
        self.assertEqual(t["tol_default"], "6H")
