# tests/test_plate_template.py — анализ узоров (model_summary.patterns) и шаблон
# класса «плита» (templates/plate.py) на сводке реальной модели «Плита опорная» (задача 16).
import copy, json, os, sys, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "templates"))
import model_summary as M
import plate
import server

FIX = os.path.join(HERE, "fixtures", "plate_summary.json")
with open(FIX, encoding="utf-8") as f:
    SUMMARY = json.load(f)


def by_kind(pat, kind, d=None):
    return [g for g in pat["groups"] if g["kind"] == kind and (d is None or abs(g["d"] - d) < 0.01)]


class Patterns(unittest.TestCase):
    def setUp(self):
        self.pat = M.patterns(SUMMARY)

    def test_plate_frame(self):
        p = self.pat
        self.assertEqual((p["axis"], p["u"], p["v"]), ("z", "x", "y"))
        self.assertAlmostEqual(p["L"], 2959.12, 2); self.assertAlmostEqual(p["W"], 2300.0, 2)
        self.assertAlmostEqual(p["thickness"], 8.0, 3); self.assertEqual(p["center"], [0.0, 0.0])

    def test_pcd_nodes_with_places(self):
        g = by_kind(self.pat, "pcd", 12)[0]
        self.assertEqual((g["n"], g["places"]), (8, 2))
        self.assertAlmostEqual(g["pcd_d"], 121.0, 1)
        self.assertEqual(g["angles"], [45.0, 75.0, 105.0, 135.0, 225.0, 255.0, 285.0, 315.0])
        self.assertEqual(sorted(c[0] for c in g["centers"]), [-1200.56, 1200.56])

    def test_square_rect_pairs_singles(self):
        sq = by_kind(self.pat, "square", 150)[0]
        self.assertAlmostEqual(sq["side"], 1127.27, 2)
        rc = by_kind(self.pat, "rect", 120)[0]
        self.assertAlmostEqual(rc["a"], 1250.028, 3); self.assertAlmostEqual(rc["b"], 300.166, 3)
        p325 = by_kind(self.pat, "pair", 325)[0]
        self.assertEqual((p325["axis"], p325["half"]), ("v", 752.0))
        p205 = by_kind(self.pat, "pair", 205)[0]
        self.assertEqual((p205["axis"], p205["half"]), ("u", 752.0))
        self.assertEqual(by_kind(self.pat, "single", 700)[0]["center"], [0.0, 0.0])
        self.assertEqual(by_kind(self.pat, "single", 85)[0]["center"], [1220.892, 704.882])

    def test_slots_and_notches(self):
        sl = self.pat["slots"]
        self.assertEqual(len(sl), 8)
        self.assertTrue(all(abs(s["w"] - 10.5) < 0.01 and abs(s["flat"] - 55) < 0.01 for s in sl))
        self.assertEqual(sorted({s["along"] for s in sl}), ["u", "v"])
        nt = self.pat["notches"]
        self.assertEqual(len(nt), 4)
        self.assertTrue(all(abs(n["depth"] - 90) < 0.01 and abs(n["width"] - 180) < 0.01 for n in nt))
        self.assertEqual(sorted({n["edge"] for n in nt}), ["+u", "-u"])

    def test_classify_row(self):
        row = M._classify([(10, 0), (30, 0), (50, 0), (70, 0)])
        self.assertEqual((row["kind"], row["axis"], row["pitch"], row["n"]), ("row", "u", 20.0, 4))
        self.assertEqual((row["start"], row["end"]), (10.0, 70.0))

    def test_cluster_fallback(self):
        c = M._classify([(0, 0), (10, 3), (25, 7)])
        self.assertEqual(c["kind"], "cluster")


class PlateSpec(unittest.TestCase):
    def setUp(self):
        self.spec = plate.plate_spec(SUMMARY, "C:/t/Плита.cdw", "C:/t/Плита.m3d",
                                     stamp={"marking": "ACME.100.04.00.001", "name": "Плита основания",
                                            "material": "Сталь 40 ГОСТ 1050-2013",
                                            "company": "Acme Engineering", "first_use": "ACME.100.04.00.000 СБ"})

    def test_structure_valid_except_paths(self):
        e = server.validate_spec(self.spec)
        self.assertTrue(all(x.startswith(("model:", "cdw:")) for x in e), e)
        self.assertEqual(self.spec["scale"], "1:15")
        self.assertIsNone(self.spec["ra"])
        self.assertEqual(self.spec["tt"], plate.TT)
        self.assertEqual(self.spec["stamp"]["company"], "Acme Engineering")

    def test_views_scheme(self):
        v = {x["key"]: x for x in self.spec["views"]}
        self.assertEqual(list(v), ["main", "А", "Б", "В"])
        self.assertEqual(v["main"]["rotate"], 90)
        self.assertEqual((v["А"]["form"], v["А"]["sheet"], v["А"]["title_prefix"]), ("rect", 2, "2 места"))
        self.assertEqual(v["А"]["scale"], "1:5")
        self.assertEqual((v["Б"]["form"], v["Б"]["scale"], v["Б"]["center"]), ("rect", "1:15", [0.0, 0.0]))
        self.assertLess(v["Б"]["w"] / 15, 130); self.assertLess(v["Б"]["h"] / 15, 60)
        self.assertEqual(v["В"]["title_prefix"], "4 места")

    def test_dims_scheme(self):
        d = {x["id"]: x for x in self.spec["dims"]}
        self.assertIn("L", d); self.assertIn("W", d)
        self.assertTrue(d["L"].get("round")); self.assertEqual(d["L"]["side"], "top")
        sq = d["SQ150"]
        self.assertEqual((sq["square"], sq["frame"], sq["text"]), (True, True, "1127,3"))
        self.assertEqual((d["P325"]["frame"], d["P325"]["side"]), (True, "right"))
        self.assertEqual(d["D325"]["prefix"], "2 отв."); self.assertEqual(d["D325"]["tol"], {"value": "2"})
        self.assertEqual(d["D150"]["tol"], {"value": "1"}); self.assertGreater(d["D150"]["leg"], 10)
        self.assertEqual(d["X85"]["frame"], True); self.assertEqual(d["D85"]["tol"], {"value": "1"})
        self.assertNotIn("prefix", d["D85"])
        self.assertEqual(d["PCD"]["ref"]["type"], "pcd"); self.assertEqual(d["PCD"]["tol"], {"value": "1"})
        self.assertEqual(len(d["PCD"]["ref"]["rays"]), 3)
        self.assertEqual((d["ANG1"]["kind"], d["ANG1"]["frame"]), ("ang", True))
        self.assertEqual(d["D12"]["prefix"], "8 отв."); self.assertEqual(d["D12"]["tol"], {"value": "0,5"})
        self.assertEqual(d["A_POS"]["frame"], True)
        self.assertEqual(d["D700"]["tol"], {"value": "2"}); self.assertEqual(d["D205"]["prefix"], "2 отв.")
        self.assertEqual(d["SLOT_W"]["prefix"], "2 паза ")
        self.assertEqual((d["NOTCH_W"]["frame"], d["NOTCH_POS"]["frame"]), (True, True))
        self.assertIn("NOTCH_D", d)

    def test_callouts_spread(self):
        dias = [x for x in self.spec["dims"] if x["kind"] == "dia" and x["view"] == "main"]
        self.assertEqual(len({(x["angle"]) for x in dias}) + 1 >= len(dias), True)
        self.assertTrue(all(x["leg"] >= 10 for x in dias))

    def test_not_a_plate(self):
        s = copy.deepcopy(SUMMARY); s["bbox"][5] = 600.0
        with self.assertRaises(ValueError):
            plate.plate_spec(s, "C:/t/a.cdw", "C:/t/a.m3d")
        s = copy.deepcopy(SUMMARY); s["holes"] = []
        with self.assertRaises(ValueError):
            plate.plate_spec(s, "C:/t/a.cdw", "C:/t/a.m3d")

    def test_places_words(self):
        self.assertEqual(plate._places(2), "2 места"); self.assertEqual(plate._places(5), "5 мест")
        self.assertIsNone(plate._places(1))


if __name__ == "__main__":
    unittest.main()

class Placement(unittest.TestCase):
    """Размещение по эталону (раунд 5): ножки короткие, A_POS на дальнем ряду, блок между контуром
    и размерной линией только между её выносными."""
    @classmethod
    def setUpClass(cls):
        cls.spec = plate.plate_spec(SUMMARY, "C:/t/Плита.cdw", "C:/t/Плита.m3d")

    def test_a_pos_far_row_and_short_legs(self):
        d = {x["id"]: x for x in self.spec["dims"]}
        self.assertEqual(d["A_POS"]["row"], plate.A_POS_ROW)
        legs = [x["leg"] for x in self.spec["dims"] if x["kind"] == "dia"]
        self.assertTrue(all(plate.LEG_MIN <= g <= plate.LEG_MAX for g in legs), legs)
        self.assertLessEqual(sum(legs) / len(legs), 50.0)       # эталон: ножки 30–60 мм

    def test_fits_rows_between_extension_lines(self):
        v = plate._View("t", (0.0, 0.0, 1000.0, 1000.0), 0.2)      # 1:5
        v.add_lin("bottom", (500.0, 0.0), (1000.0, 0.0), row=3)   # ряд 34 мм: 500..1000 по X
        self.assertTrue(v.fits_rows("bottom", 600.0, 800.0, 6.0, 24.0))     # между выносными, ближе линии
        self.assertFalse(v.fits_rows("bottom", 400.0, 800.0, 6.0, 24.0))    # режет выносную x=500
        self.assertFalse(v.fits_rows("bottom", 600.0, 800.0, 20.0, 38.0))   # лежит на размерной линии
        self.assertTrue(v.fits_rows("bottom", 600.0, 800.0, 40.0, 58.0))    # дальше линии
        self.assertTrue(v.fits_rows("bottom", 100.0, 300.0, 6.0, 24.0))     # вне размера — сразу за контур

