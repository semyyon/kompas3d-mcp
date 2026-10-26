# Ответ kompas_check_drawing внутри kompas_batch: обрезка не должна терять строку JSON.
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import drawing_rules as R
import server  # импорт без COM: только FastMCP и определения функций

RULES = R.load_rules()


def four_fail_facts():
    # как «Основание медное» до исправления: Ч01, Ч06, Ч08, Ч13
    return {"formats": [1], "stamp": {"name": "Основание медное", "marking": "PRJ.02.001",
            "material": "Медь М1 ГОСТ 859-2001", "mass": "11,23",
            "scale": "^(#5~1:2,5)1;288314011823.000000;1~"},
            "views": [{"idx": i, "name": n, "kind": "proj", "scale": 0.4, "scale_shown": False,
                       "has_geometry": True, "bbox_paper": [50, 50, 150, 100]}
                      for i, n in [(1, "Спереди 1"), (2, "Сверху 2"), (3, "Снизу 3"), (4, "Изометрия 4")]],
            "dims": [], "leaders": [], "sys_texts": [], "tt_lines": ["1. Острые кромки притупить."]}


class T(unittest.TestCase):
    def test_four_fail_report_keeps_json_after_clip(self):
        checks = R.evaluate(four_fail_facts(), RULES)
        self.assertEqual({c["code"] for c in checks if not c["ok"]}, {"Ч01", "Ч06", "Ч08", "Ч13"})
        rep = server._check_report(r"C:\tmp\Основание медное.cdw", checks)
        self.assertGreater(len(rep), server.MAX_RESULT_CHARS)          # обрезка действительно нужна
        clipped = server._clip_result(rep)
        last = clipped.rsplit("\n", 1)[-1]
        self.assertTrue(last.startswith("JSON: "))
        self.assertEqual(last, rep.rsplit("\n", 1)[-1])
        self.assertIn("обрезано", clipped)

    def test_short_result_untouched(self):
        self.assertEqual(server._clip_result("Opened: x.cdw"), "Opened: x.cdw")

    def test_long_result_without_json(self):
        s = "x" * (server.MAX_RESULT_CHARS + 10)
        c = server._clip_result(s)
        self.assertTrue(c.startswith("x" * server.MAX_RESULT_CHARS))
        self.assertNotIn("JSON:", c)


if __name__ == "__main__":
    unittest.main()
