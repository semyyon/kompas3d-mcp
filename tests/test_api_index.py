# tests/test_api_index.py
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import api_index as A

STUB = '''
class IBase(IDispatch):  # ibase.html
    """
    ## Интерфейс IBase - базовый
    """

    @property
    def Name(self) -> str:  # x.html
        """doc"""
        ...
    @Name.setter
    def Name(self, value: str): ...

    @property
    def Valid(self) -> bool:  # x.html
        ...

class IChild(IBase):  # ichild.html
    """
    ## Интерфейс IChild - ребёнок
    """

    def Add(self, Type, Count=1) -> IBase:  # y.html
        """doc"""
        ...
'''


class ParseStub(unittest.TestCase):
    def setUp(self):
        self.idx = {"classes": A.parse_stub(STUB, "api7"), "enums": {}}

    def test_inherited_member_and_rw(self):
        m = A.member(self.idx, "IChild", "Name")
        self.assertEqual((m["kind"], m["rw"], m["owner"]), ("prop", True, "IBase"))
        self.assertFalse(A.member(self.idx, "IChild", "Valid")["rw"])
        self.assertEqual(A.member(self.idx, "IChild", "Add")["args"], ["Type", "Count"])
        self.assertIsNone(A.member(self.idx, "IChild", "Nope"))
        self.assertEqual(self.idx["classes"]["IChild"]["doc"], "Интерфейс IChild - ребёнок")

    def test_similar(self):
        self.assertIn("Depth", A.similar(["Depth", "DepthObject", "Name"], "Dept"))


@unittest.skipUnless(os.path.exists(os.path.join(A.KOMPAS_BIN, "kAPI7.tlb")), "нет КОМПАС v23")
class Typelib(unittest.TestCase):
    def test_v23_extras(self):
        idx = A.load()
        self.assertEqual(A.member(idx, "IModelsFamilyManager", "ApplyLine")["args"], ["LineIndex"])
        self.assertEqual(A.member(idx, "IExtrusion", "Depth")["args"], ["Normal"])
        self.assertIn("o3d_FilletChanger", idx["enums"]["ksObj3dTypeEnum"]["values"])
