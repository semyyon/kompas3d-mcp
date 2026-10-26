# tests/test_draw_v5.py — пакет П1 плана v5 (закон серии КД_v3/РЕШЕНИЯ_СЕРИИ.md п. 7, 8):
# чистые функции jobs/_draw.py (масса, заголовок вида, перенос наименования, спецзнаки)
# и ключи project_rules.json. Без КОМПАС.
import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jobs"))
import _draw as D

RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "project_rules.json")


class ProjectRulesV5(unittest.TestCase):
    def setUp(self):
        with open(RULES_PATH, encoding="utf-8") as f:
            self.r = json.load(f)
    def test_keys(self):
        for k in ("ra_by_material", "mass_format", "stamp_defaults", "stainless", "tt_templates", "nonround_allow"):
            self.assertIn(k, self.r)
    def test_ra_by_material(self):
        ra = self.r["ra_by_material"]
        self.assertEqual((ra["metal"], ra["plastic"]), ("Ra 1,6", "Ra 6,3"))
        self.assertEqual(ra["semi_finished"], {"sign": "без обработки", "ends": "Ra 3,2"})
        self.assertTrue(any(m in "Капролон ПА-6 ТУ" for m in ra["plastic_marks"]))
        self.assertTrue(any(m in "Труба 16×1 ГОСТ 617-2006" for m in ra["semi_finished_marks"]))
    def test_law_values(self):
        self.assertEqual(self.r["mass_format"], {"unit": "kg", "digits": 2, "small_g": 5, "small_digits": 3})
        self.assertEqual(self.r["stamp_defaults"]["first_use"], "")
        self.assertEqual(self.r["stainless"]["stamp"], "12Х18Н10Т ГОСТ 5632-2014")
        self.assertIn("AISI 304", self.r["stainless"]["tt"])
        self.assertIn(42.25, self.r["nonround_allow"])
    def test_tt_templates(self):
        tt = self.r["tt_templates"]
        self.assertEqual(tt["general_tol"], "Общие допуски по ГОСТ 30893.1-m.")
        self.assertEqual(tt["copper"], "Медь М1 ГОСТ 859-2014")
        self.assertIn("0,6 МПа", tt["pressure_test"])
        joined = " ".join(tt.values())
        for bad in ("М0б", "1 МПа", "нагартован"):
            self.assertNotIn(bad, joined)


class MassText(unittest.TestCase):
    def test_kg_two_digits(self):
        # закон п. 8: «0,05», не «48,24 г»; «2,57»
        self.assertEqual(D.mass_text(2570), "2,57")
        self.assertEqual(D.mass_text(48.24), "0,05")
        self.assertEqual(D.mass_text(5), "0,01")
    def test_small_three_digits(self):
        # < 5 г — три знака: втулка .015 «0,001», «0,004»
        self.assertEqual(D.mass_text(4.2), "0,004")
        self.assertEqual(D.mass_text(1.0), "0,001")
        self.assertEqual((D.mass_digits(4.99), D.mass_digits(5.0)), (3, 2))


class ViewTitle(unittest.TestCase):
    def test_law_examples(self):
        self.assertEqual(D.view_title("А-А", "1:1"), "А-А (1:1)")
        self.assertEqual(D.view_title("Б", 4.0), "Б (4:1)")
        self.assertEqual(D.view_title("В", 4, "4 места"), "4 места В (4:1)")
        self.assertEqual(D.view_title("А-А", 0.4), "А-А (1:2,5)")
        self.assertEqual(D.view_title("А-А"), "А-А")
    def test_regex(self):
        for t in ("А-А (1:1)", "Б (4:1)", "4 места В (4:1)", "А-А", "Б (2,5:1)"):
            self.assertTrue(D.VIEW_TITLE_RE.match(t), t)
        for t in ("А-А(2:1)", "A-A (1:1)", "4 места В(4:1)", "(1:4)"):
            self.assertFalse(D.VIEW_TITLE_RE.match(t), t)
    def test_scale_text(self):
        self.assertEqual([D.scale_text(s) for s in (1.0, 10, 0.25, 0.1, "2,5:1")],
                         ["1:1", "10:1", "1:4", "1:10", "2,5:1"])


class NameLines(unittest.TestCase):
    def test_hyphen_word_kept(self):
        self.assertEqual(D.name_lines("Проставка-полюсник 200х10"), ["Проставка-полюсник", "200х10"])
        self.assertEqual(D.name_lines("Проставка-полюсник 70х10"), ["Проставка-полюсник", "70х10"])
    def test_by_words(self):
        self.assertEqual(D.name_lines("Втулка резьбовая закладная М4"), ["Втулка резьбовая", "закладная М4"])
        self.assertEqual(D.name_lines("Направляющая торцевая левая"), ["Направляющая торцевая", "левая"])
    def test_short_and_long_word(self):
        self.assertEqual(D.name_lines("Мишень"), ["Мишень"])
        self.assertEqual(D.name_lines("Ярмо магнитное"), ["Ярмо магнитное"])
        self.assertEqual(D.name_lines("Сверхдлинноеслововбольшестрокиштампа М4"),
                         ["Сверхдлинноеслововбольшестрокиштампа", "М4"])


class TextSegs(unittest.TestCase):
    def test_counterbore_suffix(self):
        # .005: «⌴Ø12,6↧0,6» — Ø спецзнаком, ↧ шрифтом глубины, ⌴ текстом (приёма нет)
        self.assertEqual(D.text_segs("⌴Ø12,6↧0,6"),
                         [("text", "⌴"), ("dia",), ("text", "12,6"), ("depth",), ("text", "0,6")])
    def test_times_and_plain(self):
        self.assertEqual(D.text_segs("2×45°"), [("text", "2"), ("times",), ("text", "45°")])
        self.assertEqual(D.text_segs("*"), [("text", "*")])
        self.assertEqual(D.text_segs("4 отв. ⌀"), [("text", "4 отв. "), ("dia",)])
    def test_thread_blind(self):
        self.assertEqual(D.text_segs("↧8 / Ø3,3↧11"),
                         [("depth",), ("text", "8 / "), ("dia",), ("text", "3,3"), ("depth",), ("text", "11")])


if __name__ == "__main__":
    unittest.main()
