# tests/test_drawing_save_gate.py
"""Дыра 2026-10-21: kompas_batch → kompas_save_document(document=annot.cdw) без path
записал чертёж с FAIL Ч19 — хук смотрел только path, а сервер не проверял вовсе.
Закрыто с двух сторон: хук учитывает document= и .cdw в скриптах с Save,
сервер сам прогоняет kompas_check_drawing перед записью .cdw."""
import json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "hooks"))
import check_backup as cb
import server

HOOK = os.path.join(HERE, "..", "hooks", "check_backup.py")


def hook(tool, inp):
    p = subprocess.run([sys.executable, HOOK], capture_output=True,   # stderr хука — в cp1251
                       input=json.dumps({"tool_name": "mcp__kompas3d__" + tool, "tool_input": inp},
                                        ensure_ascii=False).encode("utf-8"))
    return p.returncode


class Hook(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gate_")
        self.stem = "gate_annot_%d" % os.getpid()
        self.cdw = os.path.join(self.dir, self.stem + ".cdw")
        open(self.cdw, "wb").write(b"x")
        os.makedirs(cb.CHECKS_DIR, exist_ok=True)
        self.vp = os.path.join(cb.CHECKS_DIR, self.stem + ".json")
        self.verdict(1)

    def tearDown(self):
        if os.path.exists(self.vp):
            os.remove(self.vp)

    def verdict(self, fails):
        json.dump({"fails": fails, "failed": ["Ч19 вид за рамкой"] * fails},
                  open(self.vp, "w", encoding="utf-8"), ensure_ascii=False)

    def test_batch_save_by_document_blocked(self):     # ровно вызов из прогона annot.cdw
        acts = [{"tool": "kompas_check_drawing", "args": {"document": self.cdw}},
                {"tool": "kompas_save_document", "args": {"document": self.cdw}}]
        self.assertEqual(hook("kompas_batch", {"actions": json.dumps(acts, ensure_ascii=False)}), 2)

    def test_direct_save_by_document_blocked(self):
        self.assertEqual(hook("kompas_save_document", {"document": self.cdw}), 2)

    def test_clean_verdict_passes(self):
        self.verdict(0)
        self.assertEqual(hook("kompas_save_document", {"document": self.cdw}), 0)

    def test_script_save_of_cdw_blocked(self):
        code = 'doc = app7.Documents.Open(r"%s")\ndoc.Save()' % self.cdw
        self.assertEqual(hook("kompas_run_script", {"code": code}), 2)

    def test_script_export_without_save_passes(self):
        code = 'd5.SaveAsToRasterFormat(r"%s", p)' % self.cdw.replace(".cdw", ".png")
        self.assertEqual(hook("kompas_run_script", {"code": code + '  # "%s"' % self.cdw}), 0)


class FakeDoc:
    def __init__(self, path):
        self.PathName, self.Name, self.saved = path, os.path.basename(path), []

    def Save(self):
        self.saved.append(self.PathName)

    def SaveAs(self, p):
        self.saved.append(p)


class Server(unittest.TestCase):
    """kompas_save_document без COM: подменены приложение и kompas_check_drawing."""

    def run_save(self, fails, **kw):
        doc = FakeDoc(r"C:\tmp\annot.cdw")
        app = type("App", (), {"ActiveDocument": doc})()
        rep = "JSON: " + json.dumps({"fails": fails, "errors": [{"code": "Ч19"}] * fails})
        saved = server.get_app7, server.kompas_check_drawing, server._activate
        server.get_app7 = lambda: app
        server.kompas_check_drawing = lambda document="": rep
        server._activate = lambda a, t: ""
        try:
            out = server._unwrapped("kompas_save_document")(**kw)
        finally:
            server.get_app7, server.kompas_check_drawing, server._activate = saved
        return out, doc.saved

    def test_save_without_path_refused_on_fail(self):
        out, saved = self.run_save(1, document=r"C:\tmp\annot.cdw")
        self.assertEqual(saved, [])
        self.assertIn("Ч19", out)
        self.assertTrue(out.startswith("Failed"))

    def test_saveas_new_cdw_refused_on_fail(self):     # новый файл хук пропускает — сервер нет
        out, saved = self.run_save(2, path=os.path.join(tempfile.gettempdir(), "final_gate.cdw"))
        self.assertEqual(saved, [])

    def test_clean_drawing_saved(self):
        out, saved = self.run_save(0)
        self.assertEqual(saved, [r"C:\tmp\annot.cdw"])

    def test_refusal_when_check_failed_to_run(self):
        self.assertIn("не выполнена", server._drawing_save_refusal("Failed: активный документ — не чертёж"))
        self.assertEqual(server._drawing_save_refusal('JSON: {"fails": 0, "errors": []}'), "")


if __name__ == "__main__":
    unittest.main()
