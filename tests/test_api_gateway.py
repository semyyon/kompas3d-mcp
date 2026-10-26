# tests/test_api_gateway.py
import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_gateway as G

IDX = {
    "classes": {
        "IModelObject": {"bases": [], "doc": "", "props": {"Name": {"type": "str", "rw": True}},
                         "methods": {"Update": {"args": []}}},
        "IExtrusion": {"bases": ["IModelObject"], "doc": "Интерфейс IExtrusion",
                       "props": {"Depth": {"type": "", "rw": True, "args": ["Normal"]},
                                 "DepthObject": {"type": "", "rw": False, "args": ["Normal"]},
                                 "Direction": {"type": "int", "rw": True}},
                       "methods": {"SetSideParameters": {"args": ["Normal", "ExtrusionType", "Depth"]}}},
        "IParts7": {"bases": [], "doc": "", "props": {"Count": {"type": "int", "rw": False}},
                    "methods": {"Part": {"args": ["Index"]}}},
        "IDocuments": {"bases": [], "doc": "", "props": {}, "methods": {"Open": {"args": ["a", "b", "c"]}}},
    },
    "enums": {"ksObj3dTypeEnum": {"page": "", "values": {"o3d_cutExtrusion": [26, ""]}}},
}


def fake(iface, **attrs):
    cls = type(iface, (), {"_oleobj_": None})
    o = cls()
    for k, v in attrs.items():
        setattr(o, k, v)
    return o


def make_ext():
    calls = []
    e = fake("IExtrusion", Name="Элемент выдавливания:1", Direction=0)
    e.Depth = lambda n: calls.append(("Depth", n)) or 12.5
    e.SetDepth = lambda n, v: calls.append(("SetDepth", n, v))
    e.SetSideParameters = lambda *a: calls.append(("SSP",) + a) or True
    return e, calls


def run(ops, roots):
    return G.run(G.parse_ops(json.dumps(ops)), roots, IDX)


class Parse(unittest.TestCase):
    def test_bad_ops(self):
        for bad in ("[]", "nope", '[{"get":"A","call":"B"}]', '[{"set":"A"}]', '[{"obj":"xx","get":"A"}]'):
            with self.assertRaises(G.GatewayError):
                G.parse_ops(bad)

    def test_help_and_find_need_no_com(self):
        ops = G.parse_ops('[{"help":"IExtrusion"},{"find":"depth"}]')
        self.assertFalse(G.needs_com(ops))
        r = G.run(ops, {}, IDX)
        self.assertTrue(r["ok"])
        self.assertEqual(r["steps"][0]["result"]["props"]["Depth"], "rw(Normal)")
        self.assertIn("IExtrusion.Depth", r["steps"][1]["result"]["members"])


class Run(unittest.TestCase):
    def test_typo_hint_before_call(self):
        e, calls = make_ext()
        r = run([{"obj": "top", "get": "Dept"}], {"top": e})
        self.assertFalse(r["ok"])
        self.assertIn("Похожие: Depth", r["steps"][0]["error"])
        self.assertEqual(calls, [])

    def test_indexed_property(self):
        e, calls = make_ext()
        r = run([{"obj": "top", "get": "Depth", "args": [True]},
                 {"obj": "top", "set": "Depth", "args": [True], "value": 10.0}], {"top": e})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["steps"][0]["result"], 12.5)
        self.assertEqual(calls, [("Depth", True), ("SetDepth", True, 10.0)])
        self.assertTrue(r["changed"])

    def test_readonly_and_argcount(self):
        e, _ = make_ext()
        r = run([{"obj": "top", "set": "DepthObject", "args": [True], "value": 1}], {"top": e})
        self.assertIn("только для чтения", r["steps"][0]["error"])
        r = run([{"obj": "top", "call": "SetSideParameters", "args": [1, 2, 3, 4]}], {"top": e})
        self.assertIn("передано 4", r["steps"][0]["error"])

    def test_refs_const_and_items(self):
        e, calls = make_ext()
        parts = fake("IParts7", Count=3)
        parts.Part = lambda i: e
        r = run([{"obj": "top", "get": "Name", "as": "p"},
                 {"obj": "top", "items": True, "as": "all"},
                 {"obj": "$all.1", "call": "SetSideParameters", "args": [True, {"const": "o3d_cutExtrusion"}, "$$x"]}],
                {"top": parts})
        # Name у IParts7 нет — первая же операция падает с подсказкой
        self.assertFalse(r["ok"])
        r = run([{"obj": "top", "items": True, "as": "all"},
                 {"obj": "$all.1", "call": "SetSideParameters", "args": [True, {"const": "o3d_cutExtrusion"}, "$$x"]}],
                {"top": parts})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["steps"][0]["result"]["count"], 3)
        self.assertEqual(r["steps"][0]["result"]["items"][0], {"_iface": "IExtrusion", "Name": "Элемент выдавливания:1"})
        self.assertEqual(calls[-1], ("SSP", True, 26, "$x"))

    def test_blocked(self):
        docs = fake("IDocuments")
        r = run([{"obj": "app7", "call": "Open", "args": ["a"]}], {"app7": docs})
        self.assertIn("kompas_open_document", r["steps"][0]["error"])
        r = run([{"obj": "doc", "call": "Save"}], {"doc": fake("IKompasDocument3D")})
        self.assertIn("kompas_save_document", r["steps"][0]["error"])

    def test_api5_tuple_and_truncation(self):
        self.assertEqual(G.to_json((True, (1.0, 2.0))), {"ok": True, "value": [1.0, 2.0]})
        big = G.to_json(list(range(10000)))
        self.assertEqual(big[-1], {"_truncated": 10000 - G.MAX_ITEMS})
        res = {"ok": True, "steps": [{"i": 0, "result": list(range(5000))}], "changed": False}
        self.assertLessEqual(len(G.dumps_clipped(res)), G.MAX_TOTAL_CHARS + 120)

    def test_com_error_stops(self):
        e = fake("IExtrusion")
        def boom():
            raise RuntimeError("COM fail")
        e.Update = boom
        r = run([{"obj": "top", "call": "Update"}, {"obj": "top", "get": "Direction"}], {"top": e})
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["steps"]), 1)
        self.assertTrue(r["steps"][0]["com"])


if __name__ == "__main__":
    unittest.main()


class VarValues(unittest.TestCase):
    def test_parse_var_set(self):
        import server
        self.assertEqual(server.parse_var_values('{"D":250,"H":"D/2"}'), {"D": 250.0, "H": "D/2"})
        self.assertEqual(server.parse_var_values(""), {})
        for bad in ('[1]', '{"D": true}', '{"D": [1]}'):
            with self.assertRaises(ValueError):
                server.parse_var_values(bad)


class FeatureArgs(unittest.TestCase):
    def test_feature_args(self):
        import server
        op, pts, prm = server.parse_feature_args("Chamfer", "[[50,0,12]]", '{"size":1}')
        self.assertEqual((op, pts, prm), ("chamfer", [[50, 0, 12]], {"size": 1}))
        for args in (("drill", "[[0,0,0]]", "{}"), ("fillet", "[[0,0]]", '{"r":1}'),
                     ("fillet", "[[0,0,0]]", "{}"), ("thread", "[[0,0,0],[1,1,1]]", '{"pitch":1}')):
            with self.assertRaises(ValueError):
                server.parse_feature_args(*args)


class Boxes(unittest.TestCase):
    def test_boxes_overlap(self):
        import server
        a = [0, 0, 0, 10, 10, 5]
        self.assertTrue(server.boxes_overlap(a, [5, 5, 1, 20, 20, 3]))
        self.assertFalse(server.boxes_overlap(a, [0, 0, 5, 10, 10, 10]))          # касание
        self.assertTrue(server.boxes_overlap(a, [0, 0, 5, 10, 10, 10], -0.01))
        self.assertFalse(server.boxes_overlap(a, [11, 0, 0, 20, 10, 5], -0.01))


class Bom(unittest.TestCase):
    def test_bom_group(self):
        import server
        self.assertEqual(server.bom_kind(True, False, "Болт М10x16 А2"), "крепёж (по имени)")
        self.assertEqual(server.bom_kind(False, False, "Узел"), "сборка")
        self.assertEqual(server.bom_kind(True, True, "Гайка"), "стандартное")
        rec = lambda kind, name, f: {"kind": kind, "name": name, "marking": "", "material": "", "mass_g": 1, "file": f}
        rows = server.bom_group([rec("деталь", "Диск", "a.m3d"), rec("сборка", "Узел", "u.a3d"),
                                 rec("деталь", "Диск", "A.M3D"), rec("крепёж (по имени)", "Болт", "b.m3d")])
        self.assertEqual([(r["kind"], r["qty"]) for r in rows],
                         [("сборка", 1), ("деталь", 2), ("крепёж (по имени)", 1)])


class ReviewFixes(unittest.TestCase):
    def test_more_blocked(self):
        r = run([{"obj": "doc5", "call": "ksSaveDocument", "args": [""]}], {"doc5": fake("ksDocument2D")})
        self.assertIn("kompas_save_document", r["steps"][0]["error"])
        r = run([{"obj": "doc5", "call": "Open", "args": ["x.m3d"]}], {"doc5": fake("ksDocument3D")})
        self.assertIn("kompas_open_document", r["steps"][0]["error"])
        e = fake("ksEntity")
        e.Create = lambda: True                    # построение операции — разрешено
        self.assertTrue(run([{"obj": "top5", "call": "Create"}], {"top5": e})["ok"])

    def test_read_only_2d(self):
        e, calls = make_ext()
        ops = G.parse_ops(json.dumps([{"obj": "top", "get": "Direction"},
                                      {"obj": "top", "set": "Direction", "value": 1}]))
        r = G.run(ops, {"top": e}, IDX, read_only="чертёж — только чтение")
        self.assertEqual(r["steps"][0]["result"], 0)
        self.assertIn("только чтение", r["steps"][1]["error"])
        self.assertTrue(G.mutates({"call": "ksLineSeg"}))
        self.assertFalse(G.mutates({"call": "ksGetObjParam"}))
        self.assertFalse(G.mutates({"get": "Views"}))

    def test_clipped_is_valid_json(self):
        res = {"ok": True, "changed": True, "document": "x",
               "steps": [{"i": k, "result": "я" * 2400} for k in range(6)] + [{"i": 6, "error": "e" * 5000}]}
        text = G.dumps_clipped(res)
        d = json.loads(text)
        self.assertLessEqual(len(text), G.MAX_TOTAL_CHARS)
        self.assertTrue(d["changed"])
        self.assertIn("_clipped", d["steps"][0]["result"])

    def test_ref_into_api5_pair(self):
        o = fake("IModelObject")
        o.Update = lambda: (True, (5.0, 6.0))
        o2 = fake("IExtrusion")
        got = []
        o2.SetSideParameters = lambda *a: got.append(a) or True
        r = run([{"obj": "top", "call": "Update", "as": "v"},
                 {"obj": "top5", "call": "SetSideParameters", "args": ["$v.1"]}], {"top": o, "top5": o2})
        self.assertTrue(r["ok"], r)
        self.assertEqual(got, [(6.0,)])
