# tests/test_snapshot_format.py
import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import drawing_rules as R

RULES = R.load_rules()


def facts(ndims=3):
    dims = []
    for k in range(ndims):
        v = 1 + k % 2
        dims.append({"view": v, "id": f"lin{k}", "kind": "lin", "text": f"{100 + k}",
                     "text_xy_paper": [61.23456 + 20 * k, 30.98765]})
    return {"formats": [3],
            "stamp": {"name": "Проставка", "marking": "PRJ.02.019",
                      "material": "Сталь 10 ГОСТ 1050-2013", "mass": "0,05", "scale": "1:2"},
            "views": [{"idx": 0, "name": "Системный вид", "kind": "system", "scale": 1.0,
                       "scale_shown": False, "has_geometry": False, "bbox_paper": None},
                      {"idx": 1, "name": "Вид спереди", "kind": "proj", "scale": 0.5,
                       "scale_shown": False, "has_geometry": True,
                       "bbox_paper": [50.04, 50.06, 150.55, 100.0]},
                      {"idx": 2, "name": "Вид сверху", "kind": "proj", "scale": 0.5,
                       "scale_shown": False, "has_geometry": True,
                       "bbox_paper": [50, 120, 150, 140]}],
            "dims": dims,
            "leaders": [{"view": 2, "id": "led0", "text": "4 отв. М8-6H"}],
            "sys_texts": ["Ra 1,6"], "tt_lines": ["1. Острые кромки притупить."]}


class T(unittest.TestCase):
    def snap(self, f=None, view=-1, fields="views,dims,texts,conflicts"):
        return R.snapshot_from_facts(f or facts(), view, fields, RULES)

    def test_default_has_all_sections_and_stamp(self):
        s = self.snap()
        for k in ("stamp", "views", "dims", "texts", "conflicts"):
            self.assertIn(k, s)
        self.assertEqual(s["stamp"]["marking"], "PRJ.02.019")
        self.assertEqual(len(s["views"]), 3)
        self.assertEqual(len(s["dims"]), 3)

    def test_fields_filter(self):
        s = self.snap(fields="dims")
        self.assertIn("dims", s)
        for k in ("views", "texts", "conflicts"):
            self.assertNotIn(k, s)
        self.assertIn("stamp", s)          # штамп - всегда (идентификация чертежа)

    def test_unknown_field_ignored(self):
        self.assertIn("views", self.snap(fields=" views , bogus "))

    def test_view_filter(self):
        s = self.snap(view=2)
        self.assertEqual([v["i"] for v in s["views"]], [2])
        self.assertTrue(all(d["v"] == 2 for d in s["dims"]))
        self.assertEqual(s["texts"]["leaders"], [{"v": 2, "id": "led0", "t": "4 отв. М8-6H"}])
        self.assertNotIn("tt", s["texts"])  # ТТ и системные тексты - не этого вида
        s0 = self.snap(view=0)
        self.assertEqual(s0["texts"]["sys"], ["Ra 1,6"])
        self.assertEqual(s0["texts"]["tt"], ["1. Острые кромки притупить."])

    def test_rounding_to_tenth(self):
        s = self.snap()
        self.assertEqual(s["dims"][0]["xy"], [61.2, 31.0])
        self.assertEqual(s["views"][1]["bb"], [50.0, 50.1, 150.6, 100.0])

    def test_scale_as_text(self):
        self.assertEqual(self.snap()["views"][1]["scale"], "1:2")

    def test_conflicts_global_and_view_filtered(self):
        f = facts()
        f["stamp"]["marking"] = ""                                   # Ч03, глобальный
        f["dims"][0]["text_xy_paper"] = [100.0, 70.0]                # Ч10: внутри bbox вида 1
        s = self.snap(f)
        codes = {c["code"] for c in s["conflicts"]}
        self.assertIn("Ч03", codes)
        self.assertIn("Ч10", codes)
        c10 = [c for c in s["conflicts"] if c["code"] == "Ч10"][0]
        self.assertEqual(c10["obj"], "v1.lin0")
        self.assertTrue(c10["fix"])
        s1 = self.snap(f, view=1)
        self.assertEqual({c["code"] for c in s1["conflicts"]}, {"Ч10"})
        s2 = self.snap(f, view=2)
        self.assertEqual(s2["conflicts"], [])

    def test_view_filter_does_not_match_prefix(self):
        f = facts()
        f["views"].append(dict(f["views"][1], idx=10, bbox_paper=[200, 50, 300, 100]))
        f["dims"][0]["view"] = 10
        f["dims"][0]["text_xy_paper"] = [250.0, 70.0]
        s = self.snap(f, view=1)
        self.assertEqual(s["conflicts"], [])          # v10 - не v1

    def test_40_dims_under_4000_chars(self):
        out = json.dumps(self.snap(facts(40)), ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(out), 4000, len(out))


    def test_sheet_per_view(self):               # задача 14d
        f = facts()
        f["views"][1]["sheet"] = 2
        s = self.snap(f)
        self.assertEqual(s["views"][1]["sheet"], 2)


if __name__ == "__main__":
    unittest.main()
