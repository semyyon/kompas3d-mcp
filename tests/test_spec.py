# tests/test_spec.py — validate_spec и чистые помощники kompas_build_drawing (задача 9)
import copy, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # импорт без COM: только FastMCP и определения функций

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "schemas", "examples", "spacer.json")


class ValidateSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = os.path.join(self.tmp.name, "Проставка.m3d")
        open(self.model, "wb").close()
        self.spec = {
            "model": self.model, "cdw": os.path.join(self.tmp.name, "Проставка.cdw"),
            "format": 3, "vertical": False, "scale": "1:1",
            "views": [{"key": "front", "orient": "#Слева", "x": 40, "y": 215},
                      {"key": "top", "orient": "#Сверху", "x": 40, "y": 165},
                      {"key": "А-А", "type": "section", "from": "top",
                       "line": [[100, -20], [100, 20]], "x": 300, "y": 165},
                      {"key": "Б", "type": "detail", "from": "front", "center": [52.5, 9],
                       "r": 4, "scale": "5:1", "x": 300, "y": 230}],
            "dims": [{"view": "top", "id": "L", "kind": "lin",
                      "ref": {"type": "points", "p1": [0, -5, 0], "p2": [210, -5, 0]},
                      "side": "bottom"},
                     {"view": "front", "kind": "lin",
                      "ref": {"type": "view_points", "p1": [210, 0], "p2": [210, 9]}}],
            "threads": [{"view": "top", "ref": {"type": "circle", "center": [52.5, 0, 9], "d": 3.3},
                         "text": "2 отв. М4-6H"}],
            "leaders": [{"view": "top", "at": [10, 5], "text": "Пометить"}],
            "tt": ["1. Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2."],
            "ra": "Ra 1,6", "stamp": {"marking": "PRJB.001.000.001"}}

    def tearDown(self):
        self.tmp.cleanup()

    def errs(self, spec=None):
        return server.validate_spec(spec if spec is not None else self.spec)

    def test_valid_spec_has_no_errors(self):
        self.assertEqual(self.errs(), [])

    def test_unknown_view_in_dims_is_named(self):
        self.spec["dims"][0]["view"] = "side"
        e = self.errs()
        self.assertEqual(len(e), 1, e)
        self.assertIn("side", e[0])
        self.assertIn("dims[0]", e[0])

    def test_unknown_view_in_threads_and_leaders(self):
        self.spec["threads"][0]["view"] = "plan"
        self.spec["leaders"][0]["view"] = "plan2"
        e = " | ".join(self.errs())
        self.assertIn("plan", e)
        self.assertIn("plan2", e)

    def test_missing_model_file(self):
        self.spec["model"] = os.path.join(self.tmp.name, "нет такой.m3d")
        e = self.errs()
        self.assertTrue(any("нет такой.m3d" in s for s in e), e)

    def test_relative_paths_rejected(self):
        self.spec["cdw"] = "Проставка.cdw"
        self.assertTrue(any("cdw" in s and "абсолютн" in s for s in self.errs()))

    def test_bad_scale(self):
        self.spec["scale"] = "1:3"
        e = self.errs()
        self.assertTrue(any("1:3" in s for s in e), e)

    def test_bad_detail_scale(self):
        self.spec["views"][3]["scale"] = "3:1"
        self.assertTrue(any("3:1" in s for s in self.errs()))

    def test_section_from_unknown_or_later_view(self):
        self.spec["views"][2]["from"] = "side"
        self.assertTrue(any("side" in s for s in self.errs()))
        spec = copy.deepcopy(self.spec)
        spec["views"][2]["from"] = "Б"          # выносной объявлен ПОЗЖЕ разреза
        self.assertTrue(any("Б" in s for s in self.errs(spec)))

    def test_first_view_must_be_orient(self):
        self.spec["views"] = self.spec["views"][2:] + self.spec["views"][:2]
        self.assertTrue(any("views[0]" in s for s in self.errs()))

    def test_duplicate_view_key(self):
        self.spec["views"][1]["key"] = "front"
        self.assertTrue(any("front" in s and "повтор" in s for s in self.errs()))

    def test_unknown_field_rejected(self):
        self.spec["views"][0]["codes"] = [3]
        self.spec["extra"] = 1
        e = " | ".join(self.errs())
        self.assertIn("codes", e)
        self.assertIn("extra", e)

    def test_unknown_view_type_rejected(self):
        self.spec["views"].append({"key": "В", "type": "local", "from": "top",
                                   "center": [0, 0], "r": 5})
        self.assertTrue(self.errs())

    def test_extra_view_must_be_projection_of_main(self):
        self.spec["views"][1]["orient"] = "#Справа"      # противоположен главному «#Слева»
        self.assertTrue(any("views[1].orient" in s for s in self.errs()))
        self.spec["views"][1]["orient"] = "#Сверху"
        self.spec["views"].insert(2, {"key": "top2", "orient": "#Сверху", "x": 40, "y": 100})
        self.assertTrue(any("top2" in s or "views[2]" in s for s in self.errs()))

    def test_orient_must_be_string_with_hash(self):
        self.spec["views"][0]["orient"] = 3
        self.assertTrue(self.errs())
        self.spec["views"][0]["orient"] = "Слева"
        self.assertTrue(self.errs())

    def test_kind_ref_mismatch(self):
        self.spec["dims"][0]["kind"] = "dia"
        self.assertTrue(any("dims[0]" in s and "circle" in s for s in self.errs()))

    def test_section_key_format(self):
        self.spec["views"][2]["key"] = "A"
        self.assertTrue(any("А-А" in s for s in self.errs()))

    def test_thread_text_must_be_thread(self):
        self.spec["threads"][0]["text"] = "Ø3,3"
        self.assertTrue(self.errs())

    def test_example_spacer_spec_is_structurally_valid(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            spec = json.load(f)
        spec["model"] = self.model             # в примере — заглушки путей
        spec["cdw"] = os.path.join(self.tmp.name, "Деталь.cdw")
        self.assertEqual(server.validate_spec(spec), [])

    def test_example_fails_only_on_placeholder_paths(self):
        # пример — шаблон: как есть не собирается (ничего не запишет в Documents)
        with open(EXAMPLE, encoding="utf-8") as f:
            spec = json.load(f)
        e = server.validate_spec(spec)
        self.assertEqual(sorted(s.split(":")[0] for s in e), ["cdw", "model"], e)
        self.assertTrue(all("<" in s and "абсолютн" in s for s in e), e)

    def test_blind_thread_fields(self):
        t = self.spec["threads"][0]
        t.update({"depth": 8, "drill_d": 3.3, "drill_depth": 11})
        self.assertEqual(self.errs(), [])
        t.pop("drill_d")                                     # глубина сверления без Ø
        self.assertTrue(any("threads[0]" in s and "drill_d" in s for s in self.errs()))
        t.update({"drill_d": 3.3, "drill_depth": 6})         # сверление мельче резьбы
        self.assertTrue(any("threads[0]" in s and "drill_depth" in s for s in self.errs()))
        t.pop("drill_d"); t.pop("drill_depth")               # только глубина резьбы — можно
        self.assertEqual(self.errs(), [])

    def test_dim_prefix_lin_and_dia(self):      # задача 13a: «2 отв. Ø4,5»
        self.spec["dims"][0]["prefix"] = "2 отв. "
        self.spec["dims"].append({"view": "top", "kind": "dia", "prefix": "2 отв. ",
                                  "ref": {"type": "circle", "center": [52.5, 0, 9], "d": 4.5}})
        self.assertEqual(self.errs(), [])
        self.spec["dims"][2] = {"view": "top", "kind": "rad", "prefix": "2 отв. ",
                                "ref": {"type": "circle", "center": [52.5, 0, 9], "d": 4.5}}
        self.assertTrue(any("dims[2].prefix" in s for s in self.errs()))
        self.spec["dims"][2]["kind"] = "dia"
        self.spec["dims"][2]["prefix"] = 2
        self.assertTrue(self.errs())

    def test_dim_suffix_lin_dia_rad(self):      # справочный размер «130*» (ГОСТ 2.307)
        self.spec["dims"][0]["suffix"] = "*"
        self.spec["dims"].append({"view": "top", "kind": "dia", "suffix": "*",
                                  "ref": {"type": "circle", "center": [52.5, 0, 9], "d": 4.5}})
        self.spec["dims"].append({"view": "top", "kind": "rad", "suffix": "*",
                                  "ref": {"type": "circle", "center": [52.5, 0, 9], "d": 4.5}})
        self.assertEqual(self.errs(), [])
        self.spec["dims"][2]["suffix"] = 2
        self.assertTrue(self.errs())
        self.spec["dims"][2]["suffix"] = ""
        self.assertTrue(self.errs())

    def test_stamp_override_fields(self):        # задача 13a: шифр ACME.100.01, материал ГОСТ
        self.spec["stamp"] = {"marking": "ACME.100.01.01.016", "name": "Лепесток заземления",
                              "material": "Медь М1 ГОСТ 859-2014"}
        self.assertEqual(self.errs(), [])
        self.spec["stamp"]["mass"] = "0,01"         # массу не перебить — поля нет
        self.assertTrue(any("mass" in s for s in self.errs()))

    def test_latin_m_accepted_for_normalization(self):
        self.spec["threads"][0]["text"] = "2 отв. M4-6H"     # латинская M
        self.assertEqual(self.errs(), [])

    def test_dim_text_override_format(self):     # задача 14c: «22,632» → «22,6»
        for t in ("22,6", "4.8", "70"):
            self.spec["dims"][0]["text"] = t
            self.assertEqual(self.errs(), [], t)
        for t in ("Ø22,6", "22,6 мм", "", 22.6):
            self.spec["dims"][0]["text"] = t
            self.assertTrue(any("dims[0].text" in s for s in self.errs()), t)

    def test_detail_shelf(self):                 # задача 14c: полка буквы выносного
        self.spec["views"][3]["shelf"] = [45.0, -5.0]
        self.assertEqual(self.errs(), [])
        self.spec["views"][3]["shelf"] = [45.0]
        self.assertTrue(any("views[3].shelf" in s for s in self.errs()))

    def test_sheet_field(self):                  # задача 14d: views[].sheet, format2
        self.spec["views"][2]["sheet"] = 2       # разрез на листе 2, родитель top — на листе 1
        self.spec["views"][3]["sheet"] = 2
        self.spec["views"][1]["sheet"] = 1
        self.assertEqual(self.errs(), [])
        self.spec["format2"] = 3
        self.assertEqual(self.errs(), [])
        self.spec["views"][2]["sheet"] = 3
        self.assertTrue(any("views[2].sheet" in s for s in self.errs()))

    def test_sheet2_only_measured_a3_landscape(self):   # 14d правка 1 (I2)
        self.spec["views"][2]["sheet"] = 2
        self.spec["format2"] = 4
        self.assertTrue(any(s.startswith("format2") for s in self.errs()))
        del self.spec["format2"]
        self.spec["vertical"] = True
        self.assertTrue(any("views[2].sheet" in s for s in self.errs()))
        self.spec["vertical"] = False
        self.spec["format"] = 4
        self.assertTrue(any("views[2].sheet" in s for s in self.errs()))
        self.spec["format"] = 3
        self.assertEqual(self.errs(), [])

    def test_main_view_on_sheet_1(self):
        self.spec["views"][0]["sheet"] = 2
        self.assertTrue(any("views[0].sheet" in s for s in self.errs()))

    def test_format2_needs_sheet2_views(self):
        self.spec["format2"] = 3
        self.assertTrue(any(s.startswith("format2") for s in self.errs()))


class Helpers(unittest.TestCase):
    def test_dim_sides(self):                    # задача 14c: куда встанут размеры вида
        spec = {"dims": [{"view": "front", "kind": "lin", "side": "top"},
                         {"view": "front", "kind": "lin", "orient": "v"},
                         {"view": "front", "kind": "dia"},
                         {"view": "top", "kind": "lin"}]}
        self.assertEqual(server._dim_sides(spec, "front"), {"top", "left"})
        self.assertEqual(server._dim_sides(spec, "top"), {"bottom", "left"})
        self.assertEqual(server._dim_sides({}, "top"), set())

    def test_sheet_offset(self):                 # задача 14d: замер — лист 2 А3 с x = 420
        self.assertEqual(server._sheet_offset({"format": 3}, 1), 0.0)
        self.assertEqual(server._sheet_offset({"format": 3}, None), 0.0)
        self.assertEqual(server._sheet_offset({"format": 3, "vertical": False}, 2), 420.0)
        self.assertEqual(server._sheet_offset({"format": 4, "vertical": True}, 2), 210.0)

    def test_off_sheet_rect(self):               # 14d правка 1: вид листа 2 вне рамки
        self.assertIsNone(server._off_sheet_rect((518.5, 200.0, 631.5, 210.0), 420.0))
        self.assertEqual(server._off_sheet_rect((800.0, 200.0, 850.0, 210.0), 420.0),
                         [380.0, 200.0, 430.0, 210.0])          # за правый край рамки (415)
        self.assertIsNotNone(server._off_sheet_rect((430.0, 100.0, 500.0, 120.0), 420.0))  # поле подшивки
        self.assertIsNotNone(server._off_sheet_rect((500.0, 280.0, 560.0, 300.0), 420.0))  # выше 292

    def test_no_room_hint_points_to_sheet2(self):  # задача 14d: Р05
        h = server._dims_error_hint("нет места")
        self.assertIn("views[].sheet=2", h); self.assertIn("Р05", h)
        self.assertEqual(server._dims_error_hint("иное"), "иное")

    def test_preview_paths_per_sheet(self):      # задача 14d: «имя(1).png», «имя(2).png»
        self.assertEqual(server._preview_paths(r"C:.png", 1), [r"C:.png"])
        self.assertEqual(server._preview_paths(r"C:.png", 2),
                         [r"C:(1).png", r"C:(2).png"])

    def test_dim_text_check(self):              # задача 14c: допуск ±0,05 мм
        self.assertTrue(server._dim_text_check("4,8", 4.75)[0])        # прецедент эталона 30.09
        self.assertTrue(server._dim_text_check("22,6", 22.632)[0])     # .012
        self.assertTrue(server._dim_text_check("12.4", 12.418)[0])
        ok, why = server._dim_text_check("22,7", 22.632)
        self.assertFalse(ok); self.assertIn("22,632", why.replace(".", ","))
        self.assertFalse(server._dim_text_check("abc", 1.0)[0])

    def test_scale_value(self):
        self.assertAlmostEqual(server._scale_value("1:2,5"), 0.4)
        self.assertAlmostEqual(server._scale_value("2:1"), 2.0)
        self.assertAlmostEqual(server._scale_value("1:1"), 1.0)

    def test_mass_check_rounding_of_last_digit(self):
        ok, _ = server._mass_check("0,147", 147.4, 1)          # кг, 3 знака
        self.assertTrue(ok)
        ok, _ = server._mass_check("0,148", 147.4, 1)
        self.assertFalse(ok)
        ok, why = server._mass_check("0,14", 147.4, 1)         # усечение КОМПАС (задача 6)
        self.assertFalse(ok)
        self.assertIn("0,14", why)
        ok, _ = server._mass_check("2,35", 2.346, 0)           # граммы
        self.assertTrue(ok)
        ok, _ = server._mass_check("", 147.4, 1)
        self.assertFalse(ok)
        ok, _ = server._mass_check("0,54 г", 0.54, 0)          # граммы с единицей (задача 14b)
        self.assertTrue(ok)
        ok, _ = server._mass_check("0,54 г", 0.54, 1)          # «г», а чертёж в кг
        self.assertFalse(ok)

    def test_glaz_aspects_from_rules_md(self):
        text = ("- Р07 `[закон][авто:Ч08]` Вид в масштабе штампа.\n"
                "- Р08 `[закон]` `[глаз]` Разрез крупно (пилот: А-А 1:1); секущая С ЗАЗОРОМ\n"
                "  от контура.\n"
                "- Р11 `[закон]` `[глаз]` Стрелка взгляда вида по стрелке — остриём НА деталь.\n"
                "## ГОСТ ЕСКД — дополнение (`[глаз]`, автопроверка их не ловит)\n"
                "- Г11 ГОСТ 2.104: основная надпись формы 1.\n"
                "## Проект PRJ.02\n- Узел — что-то\n")
        got = server._glaz_aspects(text)
        self.assertEqual([g.split()[0] for g in got], ["Р08", "Р11", "Г11"])
        self.assertTrue(all(len(g) <= 60 for g in got))
        self.assertIn("Разрез крупно", got[0])

    @unittest.skipUnless(os.path.exists(server.RULES_MD_PATH), "rules.md is not shipped")
    def test_glaz_aspects_real_file_nonempty(self):
        got = server._glaz_aspects()
        self.assertIn("Р08", " ".join(got))
        self.assertGreaterEqual(len(got), 6)

    def test_projection_code_from_measured_axes(self):
        # замер задачи 9: «#Слева»+[3,4,5,6] и «#Спереди»+[3,4,5,6] на копии проставки
        pc = server._projection_code
        self.assertEqual(pc("#Слева", "#Сверху"), 3)
        self.assertEqual(pc("#Слева", "#Снизу"), 4)
        self.assertEqual(pc("#Слева", "#Сзади"), 5)
        self.assertEqual(pc("#Слева", "#Спереди"), 6)
        self.assertEqual(pc("#Спереди", "#Сверху"), 3)
        self.assertEqual(pc("#Спереди", "#Слева"), 5)
        self.assertEqual(pc("#Спереди", "#Справа"), 6)
        self.assertIsNone(pc("#Слева", "#Справа"))     # взгляд с обратной стороны
        self.assertIsNone(pc("#Слева", "#Слева"))

    def test_thread_parts_through_and_blind(self):
        p = server._thread_parts("2 отв. M4-6H")
        self.assertEqual((p["prefix"], p["nominal"], p["suffix"]), ("2 отв. ", "М4-6H", []))
        self.assertEqual(server._thread_label(p), "2 отв. М4-6H")
        p = server._thread_parts("М4-6H", depth=8, drill_d=3.3, drill_depth=11)
        self.assertEqual(p["prefix"], "")
        self.assertEqual(p["suffix"], [("depth",), ("text", "8 / "), ("dia",), ("text", "3,3"),
                                       ("depth",), ("text", "11")])
        self.assertEqual(server._thread_label(p), "М4-6H↧8 / Ø3,3↧11")
        self.assertEqual(server._thread_label(server._thread_parts("М4-6H", depth=8)), "М4-6H↧8")

    def test_thread_parts_normalizes_latin_and_decimal(self):
        p = server._thread_parts("M8x0.75-6H", depth=12.5)
        self.assertEqual(p["nominal"], "М8х0,75-6H")         # кириллические М и х, запятая
        self.assertEqual(server._thread_label(p), "М8х0,75-6H↧12,5")
        self.assertNotIn("M", server._thread_label(p).replace("М", ""))

    # оси видов «#Слева» + [3, 4, 5, 6], замер задачи 9 (GetProjectionPoint, индекс вида -> оси)
    PROBE = {1: {"X": (1, 0), "Y": (0, 0), "Z": (0, 1)},
             2: {"X": (1, 0), "Y": (0, 1), "Z": (0, 0)},
             3: {"X": (1, 0), "Y": (0, -1), "Z": (0, 0)},
             4: {"X": (0, 0), "Y": (-1, 0), "Z": (0, 1)},
             5: {"X": (0, 0), "Y": (1, 0), "Z": (0, 1)}}

    def test_toward_from_axes(self):
        self.assertEqual(server._toward_from_axes(self.PROBE[1]), (0, -1, 0))   # «#Слева»
        self.assertEqual(server._toward_from_axes(self.PROBE[2]), (0, 0, 1))    # сверху
        self.assertEqual(server._toward_from_axes({"X": (0.0, -1.0), "Y": (1.0, 0.0),
                                                   "Z": (0.0, 0.0)}), (0, 0, 1))

    def test_match_views_by_orientation_not_order(self):
        measured = {i: server._toward_from_axes(a) for i, a in self.PROBE.items()}
        # ключи даны НЕ по возрастанию кодов: сзади (5), сверху (3), спереди (6), снизу (4)
        want = {"main": "#Слева", "back": "#Сзади", "top": "#Сверху",
                "front": "#Спереди", "bottom": "#Снизу"}
        idx, errs = server._match_views(want, measured)
        self.assertEqual(errs, [])
        self.assertEqual(idx, {"main": 1, "top": 2, "bottom": 3, "back": 4, "front": 5})

    def test_match_views_reports_missing(self):
        measured = {1: (0, -1, 0), 2: (0, 0, 1)}
        idx, errs = server._match_views({"main": "#Слева", "side": "#Спереди"}, measured)
        self.assertEqual(idx, {"main": 1})
        self.assertTrue(any("side" in e for e in errs))

    def test_failure_result_shape(self):
        r = server._failure_result("check", "RuntimeError: сбой", r"C:\a\X.draft.cdw", True)
        self.assertEqual(r["verification"]["level"], "call_returned")
        self.assertEqual(r["saved"], "draft")
        self.assertEqual(r["cdw"], r"C:\a\X.draft.cdw")
        self.assertTrue(r["document_closed"])
        self.assertEqual(r["lint"]["errors"][0]["code"], "exception:check")
        self.assertIn("сбой", r["lint"]["errors"][0]["fix_hint"])
        r = server._failure_result("build", "COM error: x", None, False)
        self.assertEqual((r["cdw"], r["saved"]), (None, "none"))
        self.assertFalse(json.dumps(r, ensure_ascii=False).startswith("Failed"))

    def test_close_new_docs_closes_only_new(self):
        class Doc:
            def __init__(self, name, path):
                self.Name, self.PathName, self.closed = name, path, None
            def Close(self, opt):
                self.closed = opt

        class Docs:
            def __init__(self, items):
                self.items = items
            @property
            def Count(self):
                return len(self.items)
            def Item(self, i):
                return self.items[i]

        old, new = Doc("a.cdw", r"C:\a.cdw"), Doc("b.draft.cdw", r"C:\b.draft.cdw")
        app = type("App", (), {})()
        app.Documents = Docs([None, old])
        before = server._open_doc_keys(app)
        app.Documents.items.append(new)
        self.assertEqual(server._close_new_docs(app, before), 1)
        self.assertEqual((old.closed, new.closed), (None, 0))   # 0 = без сохранения

    def test_norm_path_slashes_case_short(self):
        n = server._norm_path
        self.assertEqual(n("C:/Users/User Name/a/Деталь.CDW"),
                         n(r"c:\users\User Name\A\деталь.cdw"))
        self.assertEqual(n(r"C:\x\.\y\..\z.cdw"), r"c:\x\z.cdw")
        long = lambda p: p.replace("USERNA~1", "User Name")
        self.assertEqual(n(r"C:\Users\USERNA~1\t\a.cdw", long),
                         n(r"C:\Users\User Name\t\a.cdw"))

    def test_activate_finds_forward_slash_path(self):
        class Doc:
            Name, PathName, Active = "a.cdw", r"C:\Temp\Чертежи\a.cdw", False
        d = Doc()
        app = type("App", (), {})()
        app.Documents = type("Docs", (), {"Count": 1, "Item": lambda self, i: d})()
        self.assertEqual(server._activate(app, "C:/Temp/чертежи/A.cdw"), "")
        self.assertTrue(d.Active)

    def test_title_issue_is_warning_not_error(self):
        issues = [{"code": "build:title", "object": "А-А", "fix_hint": "x"},
                  {"code": "build:dims", "object": "plan.R10", "fix_hint": "y"}]
        err, warn = server._split_warnings(issues)
        self.assertEqual([e["code"] for e in err], ["build:dims"])
        self.assertEqual([w["code"] for w in warn], ["build:title"])

    def test_side_paths(self):
        self.assertEqual(server._spec_paths(r"C:\a\Деталь.cdw"),
                         (r"C:\a\Деталь.draft.cdw", r"C:\a\Деталь.state.json"))


if __name__ == "__main__":
    unittest.main()
