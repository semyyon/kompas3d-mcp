# tests/test_composer.py — компоновщик intent → spec (этап 2в, docs/drawings-rework-2026-10-19.md)
import json, os, sys, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
import composer as C
import server

FIX = os.path.join(HERE, "fixtures", "val.intent.json")
_CACHE = {}


def val_spec():
    """Компоновка вала один раз на модуль: перебор вариантов — секунды."""
    if not _CACHE:
        with open(FIX, encoding="utf-8") as f:
            _CACHE["intent"] = json.load(f)
        _CACHE["spec"], _CACHE["errs"] = C.compose(_CACHE["intent"], r"C:\tmp\val.cdw", model=r"C:\tmp\val.m3d")
    return _CACHE["intent"], _CACHE["spec"], _CACHE["errs"]


class Parts(unittest.TestCase):
    def test_pack_rows_chain_in_one_row_overall_last(self):
        rows = C.pack_rows([("a", 0, 30, False), ("b", 30, 45, False), ("c", 0, 136, False),
                            ("g", 0, 579, True)])
        self.assertEqual(rows["a"], rows["b"])            # цепочка — один ряд
        self.assertNotEqual(rows["a"], rows["c"])         # перекрытие — другой ряд
        self.assertEqual(rows["g"], max(rows.values()))   # габарит — последний
        self.assertGreater(rows["g"], rows["c"])

    def test_columns_keep_order_and_no_overlap(self):
        xs = C.columns([100, 101, 300], [30, 12, 30], 20, 400)
        self.assertLess(xs[0], xs[1])
        self.assertGreaterEqual(xs[1], xs[0] + 30)
        self.assertGreaterEqual(xs[2], 300)

    def test_span_of_finds_length_between_shoulders(self):
        prof = C.Profile([(0, 30, 45), (30, 45, 55), (45, 136, 45), (136, 186, 40)])
        feats = {"F8": {"kind": "шейка", "geom": {"d": 80, "x0": 136, "x1": 186}}}
        self.assertEqual(C.span_of({"value": 50, "features": ["F8"]}, feats, prof.bounds()), (136, 186))


class ShaftSpec(unittest.TestCase):
    def setUp(self):
        self.intent, self.spec, self.errs = val_spec()     # перебор вариантов — ~13 с, один раз

    def test_no_layout_errors_and_valid_spec(self):
        self.assertEqual(self.errs, [])
        errs = [e for e in server.validate_spec(self.spec) if "файла модели нет" not in e]
        self.assertEqual(errs, [])

    def test_diameters_on_steps_not_ladder(self):
        dias = [d for d in self.spec["dims"] if d["view"] == "front" and d["orient"] == "v"]
        self.assertTrue(dias and all(d.get("pos") is not None for d in dias))
        fits = {d["suffix"] for d in dias if d.get("suffix")}
        self.assertTrue({"j6", "h6"} <= fits)
        self.assertTrue(any(d.get("prefix") == "М" for d in dias))   # резьба M76×2-6g

    def test_length_rows_at_most_three(self):
        ys = {d["pos"] for d in self.spec["dims"] if d["view"] == "front" and d["orient"] == "h"}
        self.assertLessEqual(len(ys), 3)

    def test_view_letters_do_not_repeat_bases(self):
        bases = {b["letter"] for b in self.spec.get("bases", [])}
        letters = {v["key"].split("-")[0] for v in self.spec["views"][1:]}
        self.assertFalse(bases & letters)
        self.assertTrue(any(v.get("wave") for v in self.spec["views"]))    # расточка — частичный разрез

    def test_gdt_and_roughs_present(self):
        self.assertGreaterEqual(len(self.spec["gdt"]), 4)
        self.assertGreaterEqual(len(self.spec["roughs"]), 2)
        # Г — база размеров (упорный торец), ни один допуск на неё не ссылается: знака нет
        self.assertEqual({b["letter"] for b in self.spec["bases"]}, {"А", "Б"})

    def test_tt_numbered(self):
        self.assertTrue(all(x[0].isdigit() or x.startswith("*") for x in self.spec["tt"]))


if __name__ == "__main__":
    unittest.main()


class ShaftSpec2g(unittest.TestCase):
    """Шаг 2г: узкие ступени — Ø на полке, знак Ra штатный, ножки/надписи сверху без пересечений."""

    def setUp(self):
        self.intent, self.spec, self.errs = val_spec()     # перебор вариантов — ~13 с, один раз

    def test_narrow_steps_on_shelf(self):
        shelves = {d["id"] for d in self.spec["dims"] if d.get("shelf")}
        self.assertEqual(shelves, {"D2", "D8"})          # Ø110 бурт и Ø60 — ступени 15 мм (7,5 мм листа)

    def test_spec_rough_form(self):
        self.assertEqual(self.spec["ra_form"], "spec")
        self.assertEqual(self.spec["ra"], "Ra 6,3")

    def test_hard_no_crossings(self):
        self.assertFalse([e for e in self.errs if "режет" in e or "пересека" in e or "налезают" in e])

    def test_cut_letter_off_extension_lines(self):
        cut = next(v for v in self.spec["views"] if v["key"].startswith("В"))
        x = cut["line"][0][0]
        ox, s = self.spec["views"][0]["x"], 0.5
        ext = [457.2, 462.2, 505.0]                      # выносные 47,8 / 42,8 / 15 у секущей
        self.assertFalse(any(ox + x * s - 15 < ox + e * s < ox + x * s + 1 for e in ext))


class ShaftSpecV6(unittest.TestCase):
    """Замечания ревьюера к Вал_v5 (2026-10-19): буква Д у секущей В, рамки на полках Ra,
    разрез Е-Е, граница Д, ссылка на ГОСТ в ТТ."""

    @classmethod
    def setUpClass(cls):
        cls.intent, cls.spec, cls.errs = val_spec()
        cls.ox, cls.oy, cls.s = cls.spec["views"][0]["x"], cls.spec["views"][0]["y"], 0.5

    def paper(self, x, z):
        return self.ox + x * self.s, self.oy + z * self.s

    def test_tt_general_tolerances_reference_gost(self):
        tt = [x for x in self.spec["tt"] if "отклонения" in x]
        self.assertEqual(len(tt), 1)
        self.assertIn("ГОСТ 30893.1", tt[0])
        self.assertIn("H14, h14, ±IT14/2", tt[0])             # значения таблицы не тронуты
        self.assertEqual(C.tt_gost(["Общие допуски по ГОСТ 30893.1-m."]), ["Общие допуски по ГОСТ 30893.1-m."])

    def test_detail_circle_visible(self):
        d = next(v for v in self.spec["views"] if v.get("type") == "detail")
        self.assertGreaterEqual(d["r"] * self.s, C.DETAIL_R_MIN - 1e-6)    # не мельче Ø7 на листе

    def test_detail_letter_clear_of_cut_letters(self):
        d = next(v for v in self.spec["views"] if v.get("type") == "detail")
        sx, sy = self.paper(*d["shelf"])
        box = (sx, sy, sx + C.SHELF_W, sy + C.SHELF_H)
        for v in self.spec["views"]:
            if v.get("type") == "section" and abs(v["line"][0][0] - v["line"][1][0]) < 1e-6:
                qx = self.paper(v["line"][0][0], 0)[0]
                top = self.paper(0, max(v["line"][0][1], v["line"][1][1]))[1]
                bot = self.paper(0, min(v["line"][0][1], v["line"][1][1]))[1]
                for zone in ((qx - 16, top - 1, qx + 2, top + 22), (qx - 16, bot - 22, qx + 2, bot + 1)):
                    self.assertFalse(box[0] < zone[2] + 3 and zone[0] - 3 < box[2]
                                     and box[1] < zone[3] + 3 and zone[1] - 3 < box[3]), (box, zone)

    def test_bore_section_shows_shoulder_with_wave_break(self):
        e = next(v for v in self.spec["views"] if v.get("wave"))
        xe = e["crop"]["rect"][1][0]                           # обрыв — правый край обрезки
        self.assertGreater(xe, 45.0)                           # за дном (30) и буртом Ø110 (30…45)
        self.assertEqual(e["wave"]["at"][0], xe)
        self.assertGreaterEqual(e["wave"]["half"], 45.0)       # поперёк всей ступени Ø90 у обрыва

    def test_gdt_leaders_do_not_touch_ra_signs(self):
        leaders = []
        for g in self.spec["gdt"]:
            ax, ay = self.paper(g["at"][0], g["at"][2])
            fx, fy = self.paper(*g["frame"])
            leaders.append((ax, ay, fx, fy))
        for r in self.spec["roughs"]:
            if r.get("shelf"):
                continue
            px, py = self.paper(r["at"][0], r["at"][2])
            w = 6.0 + C.text_w("Ra " + r["ra"])
            edges = [(px, py, px + w, py), (px + w, py, px + w, py + C.RA_H),
                     (px + w, py + C.RA_H, px, py + C.RA_H), (px, py + C.RA_H, px, py)]
            self.assertFalse(any(C.seg_cross(q, e, eps=0.0) for q in leaders for e in edges), r)
        self.assertFalse([n for n in self.spec.get("composer_notes") or [] if "наибольшим зазором" in n])



class ShaftSpecV7(unittest.TestCase):
    """Замечания ревьюера к Вал_v6 (2026-10-21): секущая Е в контуре, замкнутая цепь, Д 2,5:1,
    пучок выносок у буртика Ø60, графа 25."""

    @classmethod
    def setUpClass(cls):
        cls.intent, cls.spec, cls.errs = val_spec()
        cls.ox, cls.oy, cls.s = cls.spec["views"][0]["x"], cls.spec["views"][0]["y"], 0.5

    def paper(self, x, z):
        return self.ox + x * self.s, self.oy + z * self.s

    def test_bore_cut_line_ends_outside_part(self):
        e = next(v for v in self.spec["views"] if v.get("wave"))
        (x1, _), (x2, _) = e["line"]
        self.assertLess(min(x1, x2), 0.0)                      # штрихи за обоими торцами
        self.assertGreater(max(x1, x2), 579.0)
        (a, b) = e["crop"]["rect"]
        self.assertLess(a[0], 0.0)
        self.assertLessEqual(abs(a[1]), b[1] + 1e-6)

    def test_reference_chain_link_and_tt(self):
        d27 = next(d for d in self.spec["dims"] if d["id"] == "D27")     # схема «справочный»
        self.assertEqual(d27.get("suffix"), "*")
        self.assertTrue(any(x.startswith("*") for x in self.spec["tt"]))
        self.assertTrue(C.is_ref({"scheme": "справочный"}) and not C.is_ref({"scheme": "цепь"}))

    def test_detail_keeps_table_scale(self):
        d = next(v for v in self.spec["views"] if v.get("type") == "detail")
        self.assertEqual(d["scale"], "4:1")
        self.assertFalse(self.spec.get("composer_notes"))

    def test_no_bunch_of_leaders_on_top(self):
        anch = sorted(self.paper(it["at"][0], it["at"][2])[0] for k in ("leaders", "gdt")
                      for it in self.spec.get(k, []) if it["at"][2] > 0)
        anch += [self.paper(d["pos"], 0)[0] for d in self.spec["dims"] if d.get("shelf")]
        anch.sort()
        for i, a in enumerate(anch):
            self.assertLessEqual(sum(1 for b in anch[i:] if b - a <= C.BUNCH_W), C.BUNCH_N, anch)

    def test_radius_below_between_extension_lines(self):
        low = [it for it in self.spec["leaders"] if it["at"][2] < 0]
        self.assertEqual([it["text"] for it in low], ["R3"])
        sx, sy = self.paper(*low[0]["shelf"])
        self.assertLess(sy, self.oy - 20 * self.s)             # под контуром Ø40

    def test_stamp_extras_from_part(self):
        it = dict(self.intent, part=dict(self.intent["part"], first_use="ACME.107.002.000", litera="О"))
        spec, _ = C.compose(it, r"C:	mpal.cdw", model=r"C:	mpal.m3d")
        self.assertEqual((spec["stamp"]["first_use"], spec["stamp"]["litera"]), ("ACME.107.002.000", "О"))
        self.assertNotIn("first_use", self.spec["stamp"])

    def test_tt_lines_counts_wraps(self):
        self.assertEqual(C.tt_lines(["коротко"]), 1)
        self.assertEqual(C.tt_lines(["x" * 80]), 2)


def _load(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return json.load(f)


class BushSpec(unittest.TestCase):
    """Втулка с осью Z (ACME.100.01.01.014): торец «#Сверху» + разрез по плоскости пазов + два выносных."""
    @classmethod
    def setUpClass(cls):
        cls.intent = _load("bush.intent.json")
        cls.spec, cls.errs = C.compose(cls.intent, r"C:\tmp\bush.cdw", model=r"C:\tmp\bush.m3d")

    def test_kind_and_valid(self):
        self.assertEqual(C.part_kind(self.intent), "bush")
        self.assertEqual(self.errs, [])
        self.assertEqual([e for e in server.validate_spec(self.spec) if "файла модели нет" not in e], [])

    def test_vertical_cut_through_slots_letters_skip_base(self):
        self.assertEqual(self.spec["views"][0]["orient"], "#Сверху")
        sec = next(x for x in self.spec["views"] if x.get("type") == "section")
        self.assertEqual(sec["key"], "Б-Б")                  # база А на листе — буква вида не А
        self.assertTrue(all(p[0] == 0 for p in sec["line"]))  # y = 0 — плоскость пазов +X
        dets = {x["key"]: x for x in self.spec["views"] if x.get("type") == "detail"}
        self.assertEqual({k: (v["from"], v["scale"]) for k, v in dets.items()},
                         {"В": ("Б-Б", "10:1"), "Г": ("top", "10:1")})

    def test_diameters_on_end_view_length_dev(self):
        # решение пользователя 2026-10-21: Ø9 и Ø5,3 — на торце (на разрезе резали штриховку)
        dias = {d["id"]: d for d in self.spec["dims"] if d["kind"] == "dia"}
        self.assertEqual(set(dias), {"D1", "D2"})
        self.assertTrue(all(d["view"] == "top" and d["ref"]["type"] == "circle" for d in dias.values()))
        self.assertEqual((dias["D1"]["angle"], dias["D2"]["angle"]), (135.0, 225.0))   # мимо секущей и паза
        self.assertGreater(dias["D2"]["leg"] / 4, 4.5 - 2.65)  # полка Ø5,3 — за наружным контуром
        L = next(d for d in self.spec["dims"] if d["id"] == "D3")
        self.assertEqual((L["side"], L["dev"]), ("top", "±0,05"))
        self.assertNotIn("suffix", L)

    def test_widths_on_end_detail(self):
        w = {d["id"]: d for d in self.spec["dims"] if d["id"] in ("D4", "D6")}
        self.assertTrue(all(d["view"] == "Г" for d in w.values()))
        self.assertEqual((w["D4"]["prefix"], w["D4"]["row"], w["D6"]["row"]), ("2 паза ", 1, 0))

    def test_base_gdt_and_ra_at_face_middle(self):
        self.assertEqual([b["letter"] for b in self.spec["bases"]], ["А"])
        g = self.spec["gdt"][0]
        self.assertEqual((g["sign"], g["base"], g["at"][2]), ("par", "А", 12.7))
        rs = self.spec["roughs"]
        self.assertEqual([(r["view"], r["at"][2]) for r in rs], [("Б-Б", 0.0), ("Б-Б", 12.7)])
        self.assertTrue(all(2.65 < -r["at"][0] < 4.2 for r in rs))   # не в углу у образующей
        self.assertTrue(self.spec.get("mass_grams"))


class PlateSpec(unittest.TestCase):
    """Пластина прижимная (ACME.100.01.01.008): план 1:2,5 с разрывом, разрез и выносной на листе 2."""
    @classmethod
    def setUpClass(cls):
        cls.intent = _load("plate.intent.json")
        cls.spec, cls.errs = C.compose(cls.intent, r"C:\tmp\plate.cdw", model=r"C:\tmp\plate.m3d")
        cls.d = {x["id"]: x for x in cls.spec["dims"]}

    def test_kind_and_valid(self):
        self.assertEqual(C.part_kind(self.intent), "plate")
        self.assertEqual(self.errs, [])
        self.assertEqual([e for e in server.validate_spec(self.spec) if "файла модели нет" not in e], [])

    def test_plan_table_scale_with_break_between_hole_columns(self):
        self.assertEqual(self.spec["scale"], "1:2,5")
        br = self.spec["views"][0]["break"][0]
        a, b = br["p1"][0], br["p2"][0]
        self.assertTrue(all(abs((x - 5) % 64 - 32) < 1e-6 for x in (a, b)))   # по серединам между колоннами
        L = 970 - (b - a) + br["gap"] / 0.4
        self.assertLessEqual(L * 0.4, C.PLAN_W)

    def test_dims_across_break_carry_nominal(self):
        for k, v in (("D1", "970"), ("D7", "940"), ("D12", "960"), ("D4", "950,4")):
            self.assertEqual(self.d[k]["text"], v)
        self.assertEqual(self.d["D4"]["dev"], "+0,2")
        self.assertEqual(self.d["D12"]["prefix"], "15×64=")

    def test_rows_fixed_and_bases_on_dimension_lines(self):
        xs = [self.d[k]["pos"] for k in ("D7", "D4", "D12", "D1")]
        self.assertEqual(xs, sorted(xs, reverse=True))          # вложенные пролёты — короче ближе
        self.assertTrue(all(self.d[k]["side"] == "right" for k in ("D14", "D13", "D2")))
        b = {x["letter"]: x for x in self.spec["bases"]}
        self.assertEqual(set(b), {"А", "Б", "В"})
        self.assertEqual(b["Б"]["at"][0], self.d["D2"]["pos"])   # на размерной линии ширины
        self.assertEqual(b["В"]["at"][1], self.d["D1"]["pos"])   # на размерной линии длины
        self.assertLess(b["В"]["at"][0], 0)                      # левее левой стрелки (справа внизу — ТТ)
        self.assertEqual(b["А"]["view"], "Г-Г")

    def test_position_tolerance_with_three_bases(self):
        self.assertEqual(self.d["D15"]["tol"], {"value": "0,1", "branch": False, "base": "А|Б|В"})
        self.assertEqual(self.d["D15"]["dev"], "+0,1")

    def test_section_sheet2_cut_outside_break(self):
        sec = next(x for x in self.spec["views"] if x.get("type") == "section")
        det = next(x for x in self.spec["views"] if x.get("type") == "detail")
        self.assertEqual((sec["key"], sec["scale"], sec["sheet"], sec["axes"]), ("Г-Г", "2:1", 2, ["h"]))
        self.assertEqual((det["scale"], det["sheet"]), ("5:1", 2))
        self.assertAlmostEqual((sec["x"] + (-11.0) * 2 + det["x"]) / 2, 215.0, delta=1.0)   # симметрично по полю
        br = self.spec["views"][0]["break"][0]
        cx = self.d["D3"]["ref"]["p1"][0]                     # x секущей в модели (линия — в СК вида с разрывом)
        self.assertTrue(cx < br["p1"][0] or cx > br["p2"][0])
        self.assertEqual(sec["line"][0][0], cx)                # обозначения — в сырых координатах вида

    def test_section_dims_inside_cb_and_on_shelf(self):
        self.assertEqual(self.d["D17"]["pos"], -55.0)          # глубина цековки — внутри неё
        self.assertEqual(self.d["D16"]["prefix"], "36 отв. Ø")
        self.assertIn("shelf", self.d["D16"])
        self.assertEqual((self.d["D6"]["dev"], self.d["D6"]["pos"]), ("-0,1", -47.6))   # в пустоте окна над губкой

    def test_leaders_tiers_and_times_sign(self):
        ls = self.spec["leaders"]
        self.assertEqual([x["text"] for x in ls], ["4 фаски 2×45°"])   # R1/R2 — в ТТ

    def test_lowest_plan_row_above_tt(self):
        v = self.spec["views"][0]
        y_low = v["y"] + min(self.d[k]["pos"] for k in ("D7", "D4", "D12", "D1")) * 0.4
        self.assertGreaterEqual(y_low + 1e-6, C.STAMP_TOP + 5.0 + 7.0 * C.tt_lines(self.spec["tt"]) + 6.0)

    def test_radii_in_tt_after_general_tolerances(self):
        # решение пользователя 2026-10-21: R1 и R2 при 1:2,5 — пунктом ТТ, не на плане
        self.assertEqual(self.spec["tt"][2], "3. Радиусы R2 углов окна, R1 углов окна губки.")
        self.assertIn("ГОСТ 30893.1", self.spec["tt"][1])

    def test_tt_numbered_without_table_marks(self):
        self.assertTrue(self.spec["tt"][0].startswith("1. * Размеры"))
        self.assertFalse(any("по умолчанию" in x for x in self.spec["tt"]))

    def test_detail_reference_breakout(self):
        it = self.d["D18"]
        self.assertEqual((it["view"], it["text"], it["suffix"]), ("Д", "0,2", "*"))
        self.assertEqual([round(p, 2) for p in (it["ref"]["p1"][1], it["ref"]["p2"][1])], [50.0, 50.2])
