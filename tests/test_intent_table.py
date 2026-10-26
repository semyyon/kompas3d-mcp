# tests/test_intent_table.py — таблица замысла (этап 2б, docs/drawings-rework-2026-10-19.md)
import copy, os, sys, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import intent_table as I

GOOD = {
    "part": {"marking": "X.001", "name": "Вал", "material": "Сталь 45", "model": "x.m3d"},
    "features": [
        {"id": "F0", "kind": "торец", "geom": {"x": 0}, "function": "упорный", "source": "модель"},
        {"id": "F1", "kind": "шейка", "geom": {"d": 40, "x0": 0, "x1": 20},
         "function": "посадочная под подшипник", "source": "вопрос Q1"},
    ],
    "bases": [{"id": "А", "feature": "F1", "role": "конструкторская", "why": "ось подшипников"}],
    "dims": [
        {"id": "D1", "features": ["F1"], "type": "dia", "value": 40, "model_value": 40.0,
         "from": "ось", "scheme": "от базы", "tolerance": "k6", "view": "V1", "why": "подшипник",
         "tol_source": "вопрос Q1"},
        {"id": "D2", "features": ["F1"], "type": "lin", "value": 20, "model_value": 20.0,
         "from": "F0", "scheme": "цепь", "tolerance": None, "view": "V1", "why": "длина шейки"},
    ],
    "gdt": [{"id": "G1", "feature": "F1", "kind": "радиальное биение", "value": "0,02", "base": "А",
             "why": "подшипник", "source": "ГОСТ 3325-85 табл. 4"}],
    "roughness": [{"feature": "F1", "ra": "0,8", "why": "подшипник"}],
    "views": [{"id": "V1", "type": "главный", "shows": ["F0", "F1"], "scale": "1:1", "why": "вал"}],
    "questions": [{"id": "Q1", "text": "Что на F1?", "options": ["подшипник", "свободно"],
                   "default": "подшипник", "affects": ["D1", "G1"]}],
}


class Validate(unittest.TestCase):
    def test_good_table_ok(self):
        self.assertEqual(I.validate(GOOD), [])

    def broken(self, fn):
        t = copy.deepcopy(GOOD)
        fn(t)
        return I.validate(t)

    def test_fit_surface_needs_tolerance_and_ra(self):
        e = self.broken(lambda t: (t["dims"][0].update(tolerance=None), t.update(roughness=[])))
        self.assertTrue(any("без допуска" in x for x in e))
        self.assertTrue(any("без Ra" in x for x in e))

    def test_duplicate_in_other_view(self):
        def f(t):
            t["views"].append({"id": "V2", "type": "выносной", "shows": ["F1"], "why": "x"})
            t["dims"].append(dict(t["dims"][0], id="D3", view="V2"))
        self.assertTrue(any("повтор" in x for x in self.broken(f)))

    def test_bad_refs(self):
        e = self.broken(lambda t: (t["dims"][1].update(**{"from": "база Б", "view": "V9"}),
                                   t["gdt"][0].update(base="Б")))
        self.assertTrue(any("нет базы Б" in x for x in e))
        self.assertTrue(any("нет вида V9" in x for x in e))

    def test_model_mismatch_needs_reason(self):
        e = self.broken(lambda t: t["dims"][1].update(value=21))
        self.assertTrue(any("≠ модели" in x for x in e))
        e = self.broken(lambda t: t["dims"][1].update(value=21, why="округлён до конструкторского"))
        self.assertEqual(e, [])

    def test_question_needs_options(self):
        e = self.broken(lambda t: t["questions"][0].update(options=["одно"]))
        self.assertTrue(any("двух вариантов" in x for x in e))

    def test_md_has_sections_and_questions(self):
        md = I.to_md(GOOD)
        for s in ("## Элементы", "## Размеры", "k6", "## Вопросы", "Q1", "радиальное биение"):
            self.assertIn(s, md)


class RealRun(unittest.TestCase):
    """Таблица вала ACME.107.002.004 от агента drawing-intent (2026-10-19): формат держится,
    посадочные шейки с допуском и Ra, вопросы с вариантами."""

    def test_shaft_intent_valid(self):
        import json
        with open(os.path.join(HERE, "fixtures", "val.intent.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertEqual(I.validate(t), [])
        self.assertGreaterEqual(len(t["gdt"]), 3)
        self.assertTrue(all(len(q["options"]) >= 2 for q in t["questions"]))



class Sources(unittest.TestCase):
    """Источник допуска (решение пользователя 2026-10-19): ГОСТ с таблицей, расчёт или вопрос."""

    def with_src(self, src, tol_src="вопрос Q1"):
        t = copy.deepcopy(GOOD)
        t["gdt"][0]["source"] = src
        t["dims"][0]["tol_source"] = tol_src
        return I.validate(t)

    def test_missing_or_typical_source_fails(self):
        self.assertTrue(any("нет источника" in e for e in self.with_src(None)))
        self.assertTrue(any("не источник" in e for e in self.with_src("типовое для подшипников")))
        self.assertTrue(any("не источник" in e for e in self.with_src("≈IT6/4")))
        self.assertTrue(any("не в форме" in e for e in self.with_src("ГОСТ 3325")))      # без таблицы
        self.assertTrue(any("несуществующий" in e for e in self.with_src("вопрос Q9")))
        self.assertTrue(any("допуск k6" in e for e in self.with_src("вопрос Q1", tol_src="")))

    def test_gost_calc_question_ok(self):
        for src in ("ГОСТ 3325-85 табл. 4", "ГОСТ 24643-81, табл. 3, степень 6",
                    "расчёт: T = 0,25·IT6 = 0,25·19 = 4,75 мкм", "вопрос Q1"):
            self.assertEqual(self.with_src(src), [], src)

    def test_md_shows_source(self):
        t = copy.deepcopy(GOOD)
        t["gdt"][0]["source"] = "ГОСТ 3325-85 табл. 4"
        self.assertIn("ГОСТ 3325-85 табл. 4", I.to_md(t))
        self.assertIn("источник", I.to_md(t))


if __name__ == "__main__":
    unittest.main()
