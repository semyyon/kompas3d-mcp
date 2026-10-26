# tests/test_rules_plate.py — правила задачи 16 (скачок «плита»):
# разметка рамки, Ч04 ГОСТ-форма, Ч13 отказ от Ra, Ч16 выносные, Ч17 счёт отверстий,
# Ч18 рамки↔допуски (предупреждение), Ч12 оценочные позиции, pos_tol_for_d.
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import drawing_rules as R
from test_drawing_rules import facts, fails, RULES


def checks(f):
    return {c["code"]: c for c in R.evaluate(f, RULES)}


class PlainText(unittest.TestCase):
    def test_frame_markup_removed(self):
        self.assertEqual(R.plain_dim_text("@80~$(67,5)$"), "67,5")
        self.assertEqual(R.plain_dim_text("@80~$(752)$"), "752")
    def test_degree_symbol(self):
        self.assertEqual(R.plain_dim_text("@80~$(45@1~)$"), "45°")
        self.assertEqual(R.plain_dim_text("90@1~"), "90°")
    def test_plain_passthrough(self):
        self.assertEqual(R.plain_dim_text("752"), "752")
        self.assertEqual(R.plain_dim_text(""), "")
        self.assertEqual(R.plain_dim_text(None), "")
    def test_is_framed(self):
        self.assertTrue(R.is_framed("@80~$(752)$")); self.assertFalse(R.is_framed("752"))
    def test_ch09_ignores_markup(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "@80~$(67,5)$", "text_xy_paper": [20, 60]}])
        self.assertNotIn("Ч09", fails(f))
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "@80~$(67,55)$", "text_xy_paper": [20, 60]}])
        self.assertIn("Ч09", fails(f))


class Ch04GostForm(unittest.TestCase):
    def test_steel40_listed(self):
        f = facts(); f["stamp"]["material"] = "Сталь 40 ГОСТ 1050-2013"
        self.assertNotIn("Ч04", fails(f))
    def test_any_gost_form_accepted(self):
        f = facts(); f["stamp"]["material"] = "Д16 ГОСТ 4784-2019"
        self.assertNotIn("Ч04", fails(f))
        f["stamp"]["material"] = "Капролон В ТУ 6-05-988-87"
        self.assertNotIn("Ч04", fails(f))
    def test_no_standard_still_fails(self):
        f = facts(); f["stamp"]["material"] = "Чугун"
        self.assertIn("Ч04", fails(f))
        f["stamp"]["material"] = "Титан ВТ1-0"
        self.assertIn("Ч04", fails(f))
    def test_rule_switch_off(self):
        rules = dict(RULES, material_gost_form=False)
        self.assertFalse(R.is_gost_form("Д16 ГОСТ 4784-2019", rules))


class Ch13Declined(unittest.TestCase):
    def test_declined_passes_with_note(self):
        f = facts(sys_texts=[]); f["hints"] = {"ra_declined": True}
        c = checks(f)["Ч13"]
        self.assertTrue(c["ok"]); self.assertIn("по решению", c["detail"])
    def test_declined_but_sign_present_fails(self):
        f = facts(sys_texts=["Ra 1,6"]); f["hints"] = {"ra_declined": True}
        self.assertIn("Ч13", fails(f))
    def test_without_hint_sign_required(self):
        f = facts(sys_texts=[])
        self.assertIn("Ч13", fails(f))


def detail_facts(circles, markers=(), dims=()):
    f = facts(dims=list(dims))
    f["views"].append({"idx": 3, "name": "Выносной элемент А", "kind": "detail", "scale": 0.2,
                       "scale_shown": True, "has_geometry": True, "bbox_paper": [200, 50, 300, 100],
                       "circles": circles, "arcs": [], "centre_markers": list(markers), "axes": []})
    return f


class Ch16Detail(unittest.TestCase):
    def test_detail_holes_without_marks_fail(self):
        f = detail_facts([[10, 10, 6], [30, 10, 6]])
        c = checks(f)["Ч16"]
        self.assertFalse(c["ok"]); self.assertIn("v3", c["object"])
    def test_detail_holes_with_marks_pass(self):
        f = detail_facts([[10, 10, 6], [30, 10, 6]], markers=[[10, 10, 2], [30, 10, 2]])
        self.assertNotIn("Ч16", fails(f))
    def test_section_not_judged(self):
        f = detail_facts([[10, 10, 6]]); f["views"][-1]["kind"] = "section"
        self.assertNotIn("Ч16", fails(f))


class Ch17GroupCount(unittest.TestCase):
    def test_count_exceeds_holes_fails(self):
        f = detail_facts([[10, 10, 6]] * 8, markers=[[10, 10, 2]] * 8,
                         dims=[{"view": 3, "id": "dia0", "kind": "dia", "text": "12", "prefix": "16 отв. "}])
        c = checks(f)["Ч17"]
        self.assertFalse(c["ok"]); self.assertIn("v3.dia0", c["object"]); self.assertIn("2 места", c["fix_hint"])
    def test_count_matches_passes(self):
        f = detail_facts([[10, 10, 6]] * 8, markers=[[10, 10, 2]] * 8,
                         dims=[{"view": 3, "id": "dia0", "kind": "dia", "text": "12", "prefix": "8 отв."}])
        self.assertNotIn("Ч17", fails(f))
        self.assertIn("групп проверено: 1", checks(f)["Ч17"]["detail"])
    def test_thread_and_lin_skipped(self):
        f = detail_facts([[10, 10, 6]] * 2, markers=[[10, 10, 2]] * 2,
                         dims=[{"view": 3, "id": "dia0", "kind": "dia", "text": "М4-6H", "prefix": "8 отв. "},
                               {"view": 3, "id": "lin0", "kind": "lin", "text": "12", "prefix": "8 отв. ",
                                "text_xy_paper": [250, 110]}])
        self.assertNotIn("Ч17", fails(f))


class Ch18FramesTolerances(unittest.TestCase):
    def test_frames_without_tolerances_warn_only(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "@80~$(752)$", "text_xy_paper": [20, 60]}])
        c = checks(f)["Ч18"]
        self.assertTrue(c["ok"]); self.assertTrue(c["warning"]); self.assertIn("v1.lin0", c["object"])
        self.assertNotIn("Ч18", fails(f))
    def test_frames_with_tolerances_clean(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "752", "frame": True, "text_xy_paper": [20, 60]}],
                  tolerances=[{"view": 1, "sign": 27, "value": "1"}])
        c = checks(f)["Ч18"]
        self.assertTrue(c["ok"]); self.assertFalse(c["warning"])
    def test_absent_both_not_reported(self):
        self.assertNotIn("Ч18", checks(facts()))


class Ch12Estimated(unittest.TestCase):
    def test_estimated_overlap_is_soft(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [20, 60]},
                        {"view": 1, "id": "dia0", "kind": "dia", "text": "12", "text_xy_paper": [22, 61], "est": True}])
        c = checks(f)["Ч12"]
        self.assertTrue(c["ok"]); self.assertIn("возможно", c["detail"]); self.assertIn("v1.lin0~v1.dia0", c["detail"])
    def test_exact_dia_overlap_is_hard(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "100", "text_xy_paper": [20, 60]},
                        {"view": 1, "id": "dia0", "kind": "dia", "text": "12", "text_xy_paper": [22, 61]}])
        self.assertIn("Ч12", fails(f))


class PosTol(unittest.TestCase):
    def test_table(self):
        self.assertEqual(R.pos_tol_for_d(8, RULES), "0,5")
        self.assertEqual(R.pos_tol_for_d(15, RULES), "0,5")
        self.assertEqual(R.pos_tol_for_d(100, RULES), "1")
        self.assertEqual(R.pos_tol_for_d(650, RULES), "2")
    def test_default_without_table(self):
        self.assertEqual(R.pos_tol_for_d(650, {}), "2")


class SnapshotFields(unittest.TestCase):
    def test_snapshot_strips_markup_and_marks_frame(self):
        f = facts(dims=[{"view": 1, "id": "lin0", "kind": "lin", "text": "@80~$(752)$", "prefix": "", "text_xy_paper": [20, 60]},
                        {"view": 1, "id": "dia0", "kind": "dia", "text": "12", "prefix": "8 отв.", "text_xy_paper": [40, 60], "est": True}])
        s = R.snapshot_from_facts(f, fields="dims", rules=RULES)
        d0, d1 = s["dims"]
        self.assertEqual(d0["t"], "752"); self.assertTrue(d0["frame"])
        self.assertEqual(d1["pre"], "8 отв."); self.assertTrue(d1["est"])


if __name__ == "__main__":
    unittest.main()
