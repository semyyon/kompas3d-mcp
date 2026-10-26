# tests/test_capabilities.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import api_index as A
import capabilities as CAP


@unittest.skipUnless(os.path.exists(os.path.join(A.KOMPAS_BIN, "kAPI7.tlb")), "нет КОМПАС v23")
class Capabilities(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = A.load()
        cls.rows = CAP.enum_rows(cls.idx)

    @unittest.skipUnless(os.path.exists(os.path.join(A.STUBS_DIR, "KompasAPI7.pyi")), "SDK stubs are not shipped")
    def test_curated_names_exist(self):
        self.assertEqual(CAP.check_names(self.idx, CAP.C), [])

    def test_enum_rows(self):
        self.assertGreaterEqual(sum(r["key"].startswith("o3d_") for r in self.rows), 330)
        self.assertGreaterEqual(sum(not r["key"].startswith("o3d_") for r in self.rows), 80)

    @unittest.skipUnless(os.path.exists(CAP.SDK_ZIP), "нет справки SDK")
    def test_v23_names_from_sdk_help(self):
        r = next(r for r in self.rows if r["key"] == "o3d_FilletChanger")
        self.assertEqual(r["func"], "Изменить размер скругления")
