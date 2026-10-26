# tests/test_sdk_index.py
import io, json, os, sys, tempfile, unittest, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sdk_index as S


def page(title, body, charset="UTF-8", head_extra=""):
    return (f'<!DOCTYPE html><html><head><title>{title}</title>'
            f'<meta http-equiv="Content-Type" content="text/html" charset="{charset}" />'
            f'<script>var x = "ZOOM <b>junk</b>";</script>{head_extra}</head><body>'
            f'<svg><path d="M0 0"/></svg><!--ZOOMRESTART-->{body}'
            f'<div class="topicfooter"><p>(c) footer</p></div><script>var y=1;</script></body></html>')


class HtmlToText(unittest.TestCase):
    def test_strips_tags_scripts_and_footer(self):
        t, x = S.parse_page(page("Интерфейс IView&nbsp;", "<p>Hello <b>world</b></p><p>Next&nbsp;line &amp; more</p>").encode("utf-8"))
        self.assertEqual(t, "Интерфейс IView")
        self.assertEqual(x, "Hello world Next line & more")
        self.assertNotIn("footer", x)
        self.assertNotIn("junk", x)

    def test_table_cells_are_separated(self):
        _, x = S.parse_page(page("T", "<table><tr><td>X, Y</td><td>- coords</td></tr></table>").encode())
        self.assertEqual(x, "X, Y - coords")

    def test_cp1251_declared_charset(self):
        raw = page("Вид", "<p>Проекция точки</p>", charset="windows-1251").encode("cp1251")
        t, x = S.parse_page(raw)
        self.assertEqual((t, x), ("Вид", "Проекция точки"))

    def test_bom_and_missing_charset_fall_back_to_utf8(self):
        raw = b"\xef\xbb\xbf<html><head><title>\xd0\x92\xd0\xb8\xd0\xb4</title></head><body><p>\xd0\xa2\xd0\xb5\xd0\xba\xd1\x81\xd1\x82</p></body></html>"
        self.assertEqual(S.parse_page(raw), ("Вид", "Текст"))

    def test_no_marker_uses_body(self):
        raw = b"<html><head><title>A</title></head><body><script>q=1</script><p>text</p></body></html>"
        self.assertEqual(S.parse_page(raw), ("A", "text"))

    def test_text_is_capped(self):
        _, x = S.parse_page(page("A", "<p>" + "w " * 50000 + "</p>").encode(), max_chars=1000)
        self.assertLessEqual(len(x), 1000)


class Ranking(unittest.TestCase):
    def setUp(self):
        self.idx = [
            {"p": "iview_method.html", "t": "Method - свойство вида", "x": "Упоминает IAssociationViewElements один раз."},
            {"p": "iassociationviewelements.html", "t": "Интерфейс IAssociationViewElements", "x": "Объекты и элементы."},
            {"p": "iassociationviewelements_projectdimensions.html",
             "t": "IAssociationViewElements - ProjectDimensions", "x": "Проецирует размеры на вид."},
            {"p": "other.html", "t": "Прочее", "x": "ничего общего"},
        ]

    def test_interface_page_first(self):
        r = S.search(self.idx, "IAssociationViewElements", 5)
        self.assertEqual(r[0]["p"], "iassociationviewelements.html")
        self.assertNotIn("other.html", [x["p"] for x in r])

    def test_title_beats_text(self):
        r = S.search(self.idx, "associationviewelements", 5)
        self.assertEqual([x["p"] for x in r][-1], "iview_method.html")

    def test_case_insensitive_cyrillic(self):
        r = S.search(self.idx, "ПРОЕЦИРУЕТ", 5)
        self.assertEqual([x["p"] for x in r], ["iassociationviewelements_projectdimensions.html"])

    def test_all_words_required_then_any_fallback(self):
        r = S.search(self.idx, "ProjectDimensions размеры", 5)
        self.assertEqual([x["p"] for x in r], ["iassociationviewelements_projectdimensions.html"])
        r = S.search(self.idx, "ProjectDimensions ничего", 5)   # нет страницы с обоими -> любое слово
        self.assertEqual({x["p"] for x in r},
                         {"iassociationviewelements_projectdimensions.html", "other.html"})

    def test_limit_and_empty_query(self):
        self.assertEqual(len(S.search(self.idx, "IAssociationViewElements", 2)), 2)
        self.assertEqual(S.search(self.idx, "   ", 5), [])
        self.assertEqual(S.search(self.idx, "zzzzqqq", 5), [])


class Snippet(unittest.TestCase):
    def test_window_around_first_hit(self):
        x = "a" * 500 + " NEEDLE " + "b" * 500
        s = S.snippet(x, ["needle"], 300)
        self.assertIn("NEEDLE", s)
        self.assertLessEqual(len(s), 300 + 2)
        self.assertTrue(s.startswith("…") and s.endswith("…"))

    def test_hit_at_start_and_no_hit(self):
        self.assertEqual(S.snippet("needle here", ["needle"], 300), "needle here")
        self.assertEqual(S.snippet("c" * 1000, ["zzz"], 300), "c" * 300 + "…")

    def test_format_results_is_compact(self):
        out = S.format_results([{"p": "a.html", "t": "Title", "x": "body text"}], ["body"], 300)
        self.assertEqual(out, "1. Title  [a.html]\n   body text")
        self.assertEqual(S.format_results([], ["x"], 300), "Ничего не найдено.")


class Cache(unittest.TestCase):
    def make_zip(self, path, pages):
        with zipfile.ZipFile(path, "w") as z:
            for name, raw in pages.items():
                z.writestr(name, raw)

    def test_build_skips_non_html_and_roundtrips_through_cache(self):
        with tempfile.TemporaryDirectory() as d:
            zp, cp = os.path.join(d, "h.zip"), os.path.join(d, "logs", "idx.json")
            self.make_zip(zp, {"a.html": page("Alpha", "<p>one</p>"), "jstopics/a.js": "hmLoadTopic({})",
                               "css/s.css": "x", "_hmXpopup.htm": page("Pop", "<p>p</p>")})
            idx = S.load_or_build(zp, cp)
            self.assertEqual([p["p"] for p in idx], ["a.html"])
            self.assertTrue(os.path.exists(cp))
            # второй вызов берёт кеш: подменим содержимое кеша и убедимся, что оно прочитано
            with open(cp, encoding="utf-8") as f:
                data = json.load(f)
            data["pages"][0]["t"] = "FROM CACHE"
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self.assertEqual(S.load_or_build(zp, cp)[0]["t"], "FROM CACHE")

    def test_rebuild_when_zip_changes(self):
        with tempfile.TemporaryDirectory() as d:
            zp, cp = os.path.join(d, "h.zip"), os.path.join(d, "idx.json")
            self.make_zip(zp, {"a.html": page("Alpha", "<p>one</p>")})
            S.load_or_build(zp, cp)
            self.make_zip(zp, {"a.html": page("Alpha", "<p>one</p>"), "b.html": page("Beta", "<p>two</p>")})
            self.assertEqual(sorted(p["p"] for p in S.load_or_build(zp, cp)), ["a.html", "b.html"])

    def test_corrupt_cache_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as d:
            zp, cp = os.path.join(d, "h.zip"), os.path.join(d, "idx.json")
            self.make_zip(zp, {"a.html": page("Alpha", "<p>one</p>")})
            with open(cp, "w") as f:
                f.write("{not json")
            self.assertEqual(len(S.load_or_build(zp, cp)), 1)

    def test_find_zip(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "x.zip")
            with open(f, "wb"):
                pass
            self.assertEqual(S.find_zip([os.path.join(d, "nope.zip"), f]), f)
            self.assertIsNone(S.find_zip([os.path.join(d, "nope.zip")]))


if __name__ == "__main__":
    unittest.main()
