# tests/test_layout.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import layout as L
C = (100.0, 100.0, 200.0, 150.0)
class T(unittest.TestCase):
    def test_nested_dims_stack_outward(self):
        r = L.ladder(C, [{"id": "a", "orient": "h", "a": 100, "b": 200, "side": "bottom"},
                         {"id": "b", "orient": "h", "a": 100, "b": 150, "side": "bottom"}])
        off = {x["id"]: x["offset"] for x in r}
        self.assertLess(off["b"], off["a"])          # короткий ближе к контуру
        self.assertGreaterEqual(off["a"] - off["b"], 8.0)
    def test_texts_never_closer_than_min_gap(self):
        dims = [{"id": f"d{i}", "orient": "h", "a": 100, "b": 100 + 10 * (i + 1), "side": "top"} for i in range(6)]
        r = L.ladder(C, dims)
        pts = [x["text_xy"] for x in r if "text_xy" in x]
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                self.assertFalse(abs(pts[i][0] - pts[j][0]) < 7 and abs(pts[i][1] - pts[j][1]) < 7)
    def test_outside_contour(self):
        r = L.ladder(C, [{"id": "a", "orient": "v", "a": 100, "b": 150, "side": None}])
        x, y = r[0]["text_xy"]
        self.assertTrue(x < 100 or x > 200)
    def test_reports_no_room(self):
        dims = [{"id": f"d{i}", "orient": "h", "a": 100, "b": 200, "side": "top"} for i in range(40)]
        r = L.ladder(C, dims, max_offset=60.0)
        self.assertTrue(any("error" in x for x in r))
class Extra(unittest.TestCase):
    def test_obstacle_pushes_to_next_step(self):
        r = L.ladder(C, [{"id": "a", "orient": "h", "a": 100, "b": 200, "side": "bottom"}],
                     obstacles=[[150.0, 90.0]])
        self.assertEqual(r[0]["offset"], 18.0)
    def test_side_must_match_orient(self):
        r = L.ladder(C, [{"id": "a", "orient": "h", "a": 100, "b": 200, "side": "left"}])
        self.assertIn("error", r[0])
    def test_lin_axis(self):
        self.assertEqual(L.lin_axis((0, 0), (10, 3)), ("h", 0, 10))
        self.assertEqual(L.lin_axis((0, 0), (1, 9)), ("v", 0, 9))
        self.assertEqual(L.lin_axis((0, 0), (10, 3), "v"), ("v", 0, 3))
    def test_match_circle(self):
        cs = [(10.0, 0.0, 1.65), (50.0, 0.0, 1.65), (10.0, 0.0, 2.0)]
        self.assertEqual(L.match_circle(cs, 10.2, 0.0, 3.3), (10.0, 0.0, 1.65))
        self.assertEqual(L.match_circle(cs, 10.0, 0.0, 4.0), (10.0, 0.0, 2.0))
        self.assertIsNone(L.match_circle(cs, 10.0, 0.0, 3.5))
        self.assertIsNone(L.match_circle(cs, 30.0, 0.0, 3.3))
    def test_match_circle_takes_arcs(self):
        # дуга ушка Стойки корпуса: R10 у (55, 0), 270…90° через 0°
        arcs = [(55.0, 0.0, 10.0, 55.0, -10.0, 55.0, 10.0, 65.0, 0.0)]
        self.assertEqual(L.match_circle(arcs, 55.1, 0.0, 20.0)[:3], (55.0, 0.0, 10.0))
    def test_arc_angle_midpoint_and_given(self):
        lug = (55.0, 0.0, 10.0, 55.0, -10.0, 55.0, 10.0, 65.0, 0.0)
        self.assertAlmostEqual(L.arc_angle(lug), 0.0)              # середина 270…90
        self.assertAlmostEqual(L.arc_angle(lug, 45), 45.0)         # внутри дуги
        self.assertAlmostEqual(L.arc_angle(lug, 180), 0.0)         # вне дуги → середина
        # те же концы, средняя точка слева: дуга 90…270, середина 180
        left = (55.0, 0.0, 10.0, 55.0, -10.0, 55.0, 10.0, 45.0, 0.0)
        self.assertAlmostEqual(L.arc_angle(left), 180.0)
        self.assertAlmostEqual(L.arc_angle(left, 45), 180.0)
        # четверть 0…90 (концы в любом порядке)
        q = (0.0, 0.0, 5.0, 0.0, 5.0, 5.0, 0.0, 3.5355, 3.5355)
        self.assertAlmostEqual(L.arc_angle(q), 45.0)
if __name__ == "__main__":
    unittest.main()

class Row(unittest.TestCase):
    def test_row_minimum_and_continues(self):
        r = L.ladder(C, [{"id": "a", "orient": "h", "a": 110, "b": 150, "side": "bottom", "row": 3},
                         {"id": "b", "orient": "h", "a": 100, "b": 200, "side": "bottom"}])
        off = {x["id"]: x["offset"] for x in r}
        self.assertEqual(off["a"], 34.0)             # 10 + 8·3: под контуром место полкам выносок
        self.assertEqual(off["b"], 42.0)             # следующий — дальше
