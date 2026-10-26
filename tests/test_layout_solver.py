# tests/test_layout_solver.py — решатель «растр занятости» (план v5, П2) на дампах
# серии КД_v3 (fixtures/003_s … 011_s). Числа — по drawing_metric.
import copy, json, os, sys, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import drawing_metric as M
import layout_solver as LS

FIX = os.path.join(HERE, "fixtures")


def load(name):
    with open(os.path.join(FIX, name + ".geom.json"), encoding="utf-8") as f:
        return json.load(f)


_CACHE = {}


def run(name):
    """(метрика до, после, правки) — решатель на дампе; результат кэшируется на модуль."""
    if name in _CACHE:
        return _CACHE[name]
    geom = load(name)
    frozen = copy.deepcopy(geom)
    moves, after = LS.simulate(geom)
    assert geom == frozen, "решатель изменил входной дамп"
    _CACHE[name] = (M.evaluate(geom)["total"], M.evaluate(after)["total"], moves)
    return _CACHE[name]


def counts(a):
    return len(a.get("segs", [])), len(a.get("texts", []))


class Series(unittest.TestCase):
    def test_003_cross_geom_down(self):
        # остаток 8 — выносные от центров отверстий через контур (lin0 4, lin2 3) и ножка резьбы:
        # позиционным сдвигом не снимаются; план v5 ждал ≤ 6
        b, a, moves = run("003_s")
        self.assertEqual(b["cross_geom"], 12)
        self.assertLessEqual(a["cross_geom"], 8)
        self.assertLessEqual(a["cross_ann"], b["cross_ann"])
        self.assertLessEqual(a["text_hits"], b["text_hits"])
        self.assertTrue(moves)

    def test_016_cross_geom_down(self):
        # остаток 8: lin0/lin1 от центров отверстий (7) и ножка Ø (1); план v5 ждал ≤ 5
        b, a, moves = run("016_s")
        self.assertEqual(b["cross_geom"], 10)
        self.assertLessEqual(a["cross_geom"], 8)
        self.assertLessEqual(a["cross_ann"], b["cross_ann"])
        self.assertIn("v1.lin2", [m["ann_id"] for m in moves])      # перенос на сторону привязок

    def test_006_flips(self):
        b, a, moves = run("006_s")
        self.assertEqual(b["cross_geom"], 39)
        self.assertLessEqual(a["cross_geom"], 27)
        self.assertEqual(a["cross_ann"], 0)

    def test_moves_monotone_everywhere(self):
        for name in ("003_s", "016_s", "004_s", "006_s", "009_s", "011_s"):
            with self.subTest(name=name):
                b, a, moves = run(name)
                self.assertLessEqual(a["defects"], b["defects"])
                self.assertLessEqual(a["cross_ann"], b["cross_ann"])
                self.assertLessEqual(a["text_hits"], b["text_hits"])
                for m in moves:
                    self.assertLess(m["after"], m["before"], m)
                    self.assertIn(m["kind"], ("lin", "dia", "rad", "led", "rem", "thread"))
                    self.assertTrue(set(m["move"]) & {"dx", "shelf", "arrows_out"}, m)
                if moves:
                    self.assertEqual(moves[-1]["after"], a["defects"])

    def test_deterministic(self):
        for name in ("003_s", "016_s", "009_s"):
            m1 = run(name)[2]
            m2 = LS.solve(load(name))
            self.assertEqual(json.dumps(m1, sort_keys=True), json.dumps(m2, sort_keys=True), name)


class Transforms(unittest.TestCase):
    def test_translate_keeps_counts(self):
        g = load("003_s")
        for v in g["views"]:
            for a in v["ann"]:
                b = LS.translate_ann(a, 3.0, -2.5)
                self.assertEqual(counts(a), counts(b))
                if a.get("segs"):
                    self.assertAlmostEqual(b["segs"][0][0] - a["segs"][0][0], 3.0)
                    self.assertAlmostEqual(b["texts"][0][1] - a["texts"][0][1], -2.5) if a.get("texts") else None

    def test_lin_and_shelf_moves_keep_counts(self):
        g = load("016_s")
        v = g["views"][1]
        lin = next(a for a in v["ann"] if a["id"] == "v1.lin2")
        dim, o, ext = LS.lin_parts(lin)
        for b in (LS.lin_perp(lin, 60.0, o, ext), LS.lin_along(lin, -6.0, o)):
            self.assertEqual(counts(lin), counts(b))
        # выносные остались у точек привязки, размерная линия ушла на 60
        b = LS.lin_perp(lin, 60.0, o, ext)
        self.assertEqual(M.lin_dim_line(b)[0][1], dim[1] + 60.0)
        for k in ext:
            self.assertEqual(b["segs"][k][1], lin["segs"][k][1])
        dia = next(a for a in v["ann"] if a["id"] == "v1.dia0")
        p = LS.shelf_parts(dia)
        self.assertIn("C", p)
        self.assertAlmostEqual(p["r"], 4.5, places=1)
        for b in (LS.shelf_move(dia, p, (170.0, 170.0)), LS.arrows_out(dia, p)):
            self.assertEqual(counts(dia), counts(b))
        b = LS.shelf_move(dia, p, (170.0, 170.0))
        self.assertEqual(b["segs"][p["shelf"]][:2], [170.0, 170.0])


if __name__ == "__main__":
    unittest.main()
