# tests/test_guard_main_session.py
import json, os, subprocess, sys, unittest
H = os.path.join(os.path.dirname(__file__), "..", "hooks", "guard_main_session.py")
def run(payload):
    p = subprocess.run([sys.executable, H], input=json.dumps(payload), capture_output=True, text=True)
    return p.returncode, p.stderr
class T(unittest.TestCase):
    def test_main_session_cdw_script_blocked(self):
        rc, err = run({"tool_name": "mcp__kompas3d__kompas_run_script",
                       "tool_input": {"code": "D.lin(v, 0,0,10,0,0)  # Чертеж.cdw"}})
        self.assertEqual(rc, 2); self.assertIn("kompas-draftsman", err)
    def test_subagent_allowed(self):
        rc, _ = run({"tool_name": "mcp__kompas3d__kompas_run_script", "agent_id": "a1",
                     "tool_input": {"code": "D.lin(v, 0,0,10,0,0)  # Чертеж.cdw"}})
        self.assertEqual(rc, 0)
    def test_main_session_3d_script_allowed(self):
        rc, _ = run({"tool_name": "mcp__kompas3d__kompas_run_script",
                     "tool_input": {"code": "app7.ActiveDocument.Name"}})
        self.assertEqual(rc, 0)
    def test_gateway_2d_blocked_in_main(self):
        ops = '[{"obj":"doc","cast":"IDrawingDocument","get":"ViewsAndLayersManager"}]'
        rc, _ = run({"tool_name": "mcp__kompas3d__kompas_api", "tool_input": {"ops": ops}})
        self.assertEqual(rc, 2)
        rc, _ = run({"tool_name": "mcp__kompas3d__kompas_api", "agent_id": "a1", "tool_input": {"ops": ops}})
        self.assertEqual(rc, 0)
    def test_gateway_3d_allowed_in_main(self):
        rc, _ = run({"tool_name": "mcp__kompas3d__kompas_api",
                     "tool_input": {"ops": '[{"obj":"top","get":"Mass"}]', "document": "Вал.m3d"}})
        self.assertEqual(rc, 0)
if __name__ == "__main__":
    unittest.main()
