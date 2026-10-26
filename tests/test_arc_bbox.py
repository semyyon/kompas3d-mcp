# tests/test_arc_bbox.py — габарит дуги в view_content (_draw.arc_bbox_points, сборка .012)
import math, os, sys, types, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "jobs"))
import _draw as D


def arc(xc, yc, r, a1, a3, a2):
    """Дуга с центром, радиусом и углами начала, середины, конца (градусы)."""
    p = lambda a: (xc + r * math.cos(math.radians(a)), yc + r * math.sin(math.radians(a)))
    (x1, y1), (x3, y3), (x2, y2) = p(a1), p(a3), p(a2)
    return types.SimpleNamespace(Xc=xc, Yc=yc, Radius=r, X1=x1, Y1=y1, X2=x2, Y2=y2, X3=x3, Y3=y3)


class ArcBBox(unittest.TestCase):
    def test_upper_sliver_does_not_reach_bottom(self):
        xs, ys = D.arc_bbox_points(arc(0, 0, 11, 60, 90, 120))     # верхний сливер Ø22
        self.assertAlmostEqual(max(ys), 11.0)
        self.assertGreater(min(ys), 9.0)                            # не −11, как полная окружность

    def test_clockwise_points_order_same_arc(self):
        xs, ys = D.arc_bbox_points(arc(0, 0, 5, 120, 90, 60))      # тот же ход в обратном порядке
        self.assertAlmostEqual(max(ys), 5.0)
        self.assertGreater(min(ys), 4.0)

    def test_arc_across_zero_angle_gets_right_extreme(self):
        xs, ys = D.arc_bbox_points(arc(0, 0, 3, -30, 0, 30))
        self.assertAlmostEqual(max(xs), 3.0)
        self.assertGreater(min(xs), 2.5)


if __name__ == "__main__":
    unittest.main()
