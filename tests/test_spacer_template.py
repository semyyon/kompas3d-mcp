# tests/test_spacer_template.py — шаблон класса «проставки» (задача 12)
import os, sys, tempfile, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "templates"))
import server  # validate_spec, без COM
import spacer


def summary(L, W, H, xs, d=3.3, depth=None, through=True, axis=(0.0, 0.0, 1.0), y=None,
            material="Сталь 10 ГОСТ 1050-2013", **extra):
    """Сводка kompas_model_summary для бруска L×W×H (X — длина, Y — ширина ±W/2, Z — высота)
    с рядом отверстий Ø d вдоль X (вход z=H)."""
    y = 0.0 if y is None else y
    s = {"props": {"name": "Проставка", "marking": "PRJ.02.000", "material": material,
                   "mass_g": 100.0, "density": 7.85},
         "bbox": [0.0, -W / 2, 0.0, float(L), W / 2, float(H)], "bodies": 1,
         "holes": [{"d": d, "n": len(xs), "axis": list(axis), "depth": depth or float(H),
                    "through": through, "entry": f"z={float(H)}",
                    "centers_top": [[float(x), y, float(H)] for x in xs]}],
         "bosses": [], "arcs": [],
         "planes": {"x": [[0.0, 1, 1.0], [float(L), 1, 1.0]],
                    "y": [[-W / 2, 1, 1.0], [W / 2, 1, 1.0]],
                    "z": [[0.0, 1, 1.0], [float(H), 1, 1.0]]},
         "notes": []}
    s.update(extra)
    return s


def dim(spec, view, p1, p2):
    """Размер вида view между точками p1, p2 (в любом порядке) или None."""
    for d in spec.get("dims", []):
        if d["view"] != view or d["ref"]["type"] != "points":
            continue
        a, b = d["ref"]["p1"], d["ref"]["p2"]
        if (a, b) == (p1, p2) or (a, b) == (p2, p1):
            return d
    return None


class SpacerTemplate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = os.path.join(self.tmp.name, "Проставка.m3d")
        open(self.model, "wb").close()
        self.cdw = os.path.join(self.tmp.name, "Проставка.cdw")

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, s):
        return spacer.spacer_spec(s, self.cdw, self.model)

    def test_210x10x9_two_through_m4(self):
        sp = self.spec(summary(210, 10, 9, [52.5, 157.5]))
        self.assertEqual(server.validate_spec(sp), [])
        self.assertEqual([v["key"] for v in sp["views"]], ["front", "top"])
        self.assertEqual([v["orient"] for v in sp["views"]], ["#Слева", "#Сверху"])
        self.assertEqual((sp["format"], sp["scale"]), (3, "1:1"))   # 210 + полосы > ширины А4
        self.assertIsNotNone(dim(sp, "front", [0.0, 0.0, 9.0], [210.0, 0.0, 9.0]))   # L
        self.assertIsNotNone(dim(sp, "front", [210.0, 0.0, 0.0], [210.0, 0.0, 9.0]))  # H
        self.assertIsNotNone(dim(sp, "top", [0.0, -5.0, 9.0], [0.0, 5.0, 9.0]))       # W
        self.assertIsNotNone(dim(sp, "top", [0.0, 0.0, 9.0], [52.5, 0.0, 9.0]))       # от базы
        self.assertIsNotNone(dim(sp, "top", [52.5, 0.0, 9.0], [157.5, 0.0, 9.0]))     # межосевое
        self.assertEqual(len(sp["dims"]), 5)            # каждый размер — один раз
        # резьба — на полке выноски, стрелка на окружность Ø3,3 справа (Ø-размер касался кромки, Р13)
        self.assertNotIn("threads", sp)
        self.assertEqual(sp["leaders"], [{"view": "top", "at": [54.15, 0.0, 9.0], "text": "2 отв. М4-6H"}])
        self.assertEqual(sp["tt"], spacer.TT)
        self.assertEqual(sp["ra"], "Ra 1,6")

    def test_views_and_dim_bands_inside_frame(self):
        sp = self.spec(summary(210, 10, 9, [52.5, 157.5]))
        fr, tt = spacer.frame(sp["format"]), spacer.tt_top(sp["format"])
        front, top = sp["views"]
        self.assertGreaterEqual(front["x"] - 18, fr[0])                 # полоса W слева
        self.assertLessEqual(front["x"] + 210 + 18, fr[2])              # полоса H справа
        self.assertLessEqual(front["y"] + 9 + 18, fr[3] - 25)           # L под знаком Ra
        self.assertGreaterEqual(front["y"] - (top["y"] + 5), 30)        # 30–40 мм между видами
        self.assertGreaterEqual(top["y"] - 5 - 26, tt)                  # цепочка над ТТ

    def test_30x20_one_hole_on_a4(self):
        sp = self.spec(summary(30, 20, 9, [15.0]))
        self.assertEqual(server.validate_spec(sp), [])
        self.assertEqual((sp["format"], sp["vertical"], sp["scale"]), (4, True, "1:1"))
        # одно отверстие — без «1 отв.»
        self.assertNotIn("threads", sp)
        self.assertEqual(sp["leaders"], [{"view": "top", "at": [16.65, 0.0, 9.0], "text": "М4-6H"}])
        self.assertIsNotNone(dim(sp, "top", [0.0, 0.0, 9.0], [15.0, 0.0, 9.0]))
        self.assertIsNotNone(dim(sp, "top", [0.0, -10.0, 9.0], [0.0, 10.0, 9.0]))
        self.assertEqual(len(sp["dims"]), 4)            # L, H, W, X1 — межосевого нет

    def test_wide_bar_thread_on_leader_shelf(self):
        sp = self.spec(summary(210, 20, 9, [30.0, 180.0]))
        self.assertEqual(server.validate_spec(sp), [])
        self.assertNotIn("threads", sp)
        self.assertEqual(sp["leaders"][0]["text"], "2 отв. М4-6H")

    def test_blind_holes_not_in_template(self):
        for w in (10, 20):                     # глубины ↧ (ГОСТ 2.311) на полке выноски шаблон не ставит
            with self.subTest(width=w), self.assertRaises(ValueError):
                self.spec(summary(70, w, 9, [15.0, 55.0], depth=8.0, through=False))

    def test_off_center_row_gets_y_dim(self):
        sp = self.spec(summary(100, 20, 9, [20.0, 80.0], y=3.0))
        self.assertEqual(server.validate_spec(sp), [])
        self.assertIsNotNone(dim(sp, "top", [0.0, -10.0, 9.0], [0.0, 3.0, 9.0]))

    def test_not_in_class_raises(self):
        cases = {
            "boss": summary(210, 10, 9, [52.5], bosses=[{"d": 5, "n": 1}]),
            "hole along X": summary(210, 10, 9, [52.5], axis=(1.0, 0.0, 0.0)),
            "insert Ø5,2": summary(210, 10, 9, [52.5], d=5.2),
            "no holes": dict(summary(210, 10, 9, [52.5]), holes=[]),
            "long thin 910×10×3": summary(910, 10, 3, [35.0, 875.0]),
        }
        for name, s in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.spec(s)


if __name__ == "__main__":
    unittest.main()
