# tests/test_spec_plate.py — spec v2 (задача 16): rotate, прямоугольный выносной,
# title_prefix, угловые/pcd, рамка, □, shift, tol, ra: null, графы штампа, выноски под полкой.
import copy, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "schemas", "examples", "plate.json")
SPACER = os.path.join(os.path.dirname(__file__), "..", "schemas", "examples", "spacer.json")


class SpecV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = os.path.join(self.tmp.name, "Плита.m3d")
        open(self.model, "wb").close()
        self.spec = {
            "model": self.model, "cdw": os.path.join(self.tmp.name, "Плита.cdw"),
            "format": 3, "vertical": False, "scale": "1:15",
            "views": [{"key": "main", "orient": "#Сверху", "x": 190, "y": 155, "rotate": 90},
                      {"key": "А", "type": "detail", "from": "main", "center": [1200, 0], "form": "rect",
                       "w": 400, "h": 400, "scale": "1:5", "x": 120, "y": 170, "sheet": 2,
                       "title_prefix": "2 места"}],
            "dims": [{"view": "main", "id": "L", "kind": "lin", "side": "top", "round": True,
                      "ref": {"type": "points", "p1": [-1479, 1150, 8], "p2": [1479, 1150, 8]}},
                     {"view": "main", "id": "SQ", "kind": "lin", "square": True, "frame": True, "shift": -10,
                      "ref": {"type": "points", "p1": [-563, -563, 8], "p2": [563, -563, 8]}},
                     {"view": "А", "id": "PCD", "kind": "dia", "tol": {"value": "1"},
                      "ref": {"type": "pcd", "center": [1200, 0, 8], "d": 121,
                              "rays": [[1243, 42, 8], [1216, 58, 8], [1157, 42, 8]]}},
                     {"view": "А", "id": "A45", "kind": "ang", "frame": True,
                      "ref": {"type": "rays", "pcd": "PCD", "i1": 0, "i2": 2}},
                     {"view": "А", "id": "AX", "kind": "ang", "radius": 20, "direction": True,
                      "ref": {"type": "angle", "center": [1200, 0, 8], "p1": [1243, 42, 8], "p2": [1157, 42, 8]}},
                     {"view": "А", "id": "D12", "kind": "dia", "prefix": "8 отв.",
                      "tol": {"sign": "pos", "value": "0,5", "dia_sign": True},
                      "ref": {"type": "circle", "center": [1243, 42, 8], "d": 12}}],
            "leaders": [{"view": "main", "at": [-1300, 1000, 8], "text": "s8", "arrow": "dot", "under": "лист"}],
            "tt": ["1 Общие допуски по ГОСТ 30893.1-m"],
            "ra": None,
            "stamp": {"marking": "ACME.100.04.00.001", "name": "Плита основания",
                      "material": "Сталь 40 ГОСТ 1050-2013", "company": "Acme Engineering",
                      "first_use": "ACME.100.04.00.000 СБ"}}

    def tearDown(self):
        self.tmp.cleanup()

    def errs(self, spec=None):
        return server.validate_spec(spec if spec is not None else self.spec)

    def test_full_v2_spec_valid(self):
        self.assertEqual(self.errs(), [])

    def test_rotate_with_projection_rejected(self):
        s = copy.deepcopy(self.spec)
        s["views"].insert(1, {"key": "top", "orient": "#Спереди", "x": 190, "y": 60})
        e = self.errs(s)
        self.assertTrue(any("rotate" in x for x in e), e)

    def test_rotate_bad_value_rejected(self):
        s = copy.deepcopy(self.spec); s["views"][0]["rotate"] = 45
        self.assertTrue(any("rotate" in x for x in self.errs(s)))

    def test_detail_rect_needs_w_h(self):
        s = copy.deepcopy(self.spec); del s["views"][1]["h"]
        self.assertTrue(any("form rect" in x for x in self.errs(s)))

    def test_detail_circle_needs_r(self):
        s = copy.deepcopy(self.spec); v = s["views"][1]
        del v["form"]; del v["w"]; del v["h"]
        self.assertTrue(any("требует r" in x for x in self.errs(s)))
        v["r"] = 200
        self.assertEqual(self.errs(s), [])

    def test_ang_requires_rays_or_angle(self):
        s = copy.deepcopy(self.spec)
        s["dims"][3]["ref"] = {"type": "circle", "center": [0, 0, 0], "d": 5}
        self.assertTrue(any("kind ang" in x for x in self.errs(s)))

    def test_rays_reference_missing_pcd(self):
        s = copy.deepcopy(self.spec); s["dims"][3]["ref"]["pcd"] = "NOPE"
        self.assertTrue(any("ref.pcd" in x for x in self.errs(s)))

    def test_rays_indices_checked(self):
        s = copy.deepcopy(self.spec); s["dims"][3]["ref"]["i2"] = 7
        self.assertTrue(any("лучей 3" in x for x in self.errs(s)))
        s["dims"][3]["ref"]["i2"] = 0
        self.assertTrue(any("разными" in x for x in self.errs(s)))

    def test_pcd_only_for_dia(self):
        s = copy.deepcopy(self.spec); s["dims"][2]["kind"] = "rad"
        self.assertTrue(any("kind rad" in x for x in self.errs(s)))

    def test_tol_on_ang_rejected_and_bad_sign(self):
        s = copy.deepcopy(self.spec); s["dims"][3]["tol"] = {"value": "1"}
        self.assertTrue(any("угловым" in x for x in self.errs(s)))
        s = copy.deepcopy(self.spec); s["dims"][5]["tol"]["sign"] = "foo"
        self.assertTrue(any("tol.sign" in x for x in self.errs(s)))
        s = copy.deepcopy(self.spec); s["dims"][5]["tol"]["sign"] = 17
        self.assertEqual(self.errs(s), [])
        s = copy.deepcopy(self.spec); s["dims"][5]["tol"]["value"] = "abc"
        self.assertTrue(any("tol" in x and "value" in x for x in self.errs(s)))

    def test_row_only_lin_nonnegative_int(self):
        s = copy.deepcopy(self.spec); s["dims"][5]["row"] = 1          # dims[5] — dia
        self.assertTrue(any("row" in x for x in self.errs(s)))
        i = next(k for k, x in enumerate(self.spec["dims"]) if x["kind"] == "lin")
        s = copy.deepcopy(self.spec); s["dims"][i]["row"] = -1
        self.assertTrue(any("row" in x for x in self.errs(s)))
        s = copy.deepcopy(self.spec); s["dims"][i]["row"] = 3
        self.assertEqual(self.errs(s), [])

    def test_square_and_shift_only_lin(self):
        s = copy.deepcopy(self.spec); s["dims"][5]["square"] = True
        self.assertTrue(any("square" in x for x in self.errs(s)))
        s = copy.deepcopy(self.spec); s["dims"][5]["shift"] = 5
        self.assertTrue(any("shift" in x for x in self.errs(s)))

    def test_ra_null_and_string_ok_number_rejected(self):
        self.assertEqual(self.errs(), [])
        s = copy.deepcopy(self.spec); s["ra"] = "Ra 1,6"
        self.assertEqual(self.errs(s), [])
        s["ra"] = 5
        self.assertTrue(any("ra" in x for x in self.errs(s)))

    def test_leader_arrow_enum(self):
        s = copy.deepcopy(self.spec); s["leaders"][0]["arrow"] = "foo"
        self.assertTrue(any("arrow" in x for x in self.errs(s)))

    def test_title_prefix_on_section_too(self):
        s = copy.deepcopy(self.spec)
        s["views"].append({"key": "В-В", "type": "section", "from": "main", "line": [[0, -2000], [0, 2000]],
                           "x": 300, "y": 100, "title_prefix": "2 места"})
        self.assertEqual(self.errs(s), [])

    def test_example_plate_fails_only_on_placeholder_paths(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            e = server.validate_spec(json.load(f))
        self.assertTrue(e)
        self.assertTrue(all(x.startswith(("model:", "cdw:")) for x in e), e)

    def test_spacer_example_still_valid(self):
        with open(SPACER, encoding="utf-8") as f:
            e = server.validate_spec(json.load(f))
        self.assertTrue(all(x.startswith(("model:", "cdw:")) for x in e), e)

    def test_spec_hints_from_sidecar(self):
        cdw = os.path.join(self.tmp.name, "Плита.draft.cdw")
        with open(os.path.join(self.tmp.name, "Плита.spec.json"), "w", encoding="utf-8") as f:
            json.dump(self.spec, f)
        self.assertEqual(server._spec_hints(cdw), {"ra_declined": True})
        self.spec["ra"] = "Ra 1,6"
        with open(os.path.join(self.tmp.name, "Плита.spec.json"), "w", encoding="utf-8") as f:
            json.dump(self.spec, f)
        self.assertEqual(server._spec_hints(cdw), {})
        self.assertEqual(server._spec_hints(os.path.join(self.tmp.name, "Нет.cdw")), {})


if __name__ == "__main__":
    unittest.main()
