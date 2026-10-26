# tests/test_drawing_metrics.py
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import drawing_metrics as M

class T(unittest.TestCase):
    def _w(self, lines):
        fd, p = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
        self.addCleanup(os.remove, p)
        with open(p, "w", encoding="utf-8") as f:
            for l in lines: f.write(json.dumps(l, ensure_ascii=False) + "\n")
        return p

    def test_usage_max_per_message_id(self):
        # одно сообщение размазано по 2 строкам; usage в первой частичный
        u1 = {"output_tokens": 3, "cache_read_input_tokens": 100}
        u2 = {"output_tokens": 500, "cache_read_input_tokens": 100}
        p = self._w([
            {"type": "assistant", "timestamp": "2026-10-01T10:00:00Z",
             "message": {"id": "m1", "usage": u1, "content": [{"type": "thinking", "thinking": ""}]}},
            {"type": "assistant", "timestamp": "2026-10-01T10:00:05Z",
             "message": {"id": "m1", "usage": u2, "content": [
                 {"type": "tool_use", "id": "t1", "name": "mcp__kompas3d__kompas_run_script", "input": {"code": "x"}}]}},
            {"type": "user", "timestamp": "2026-10-01T10:00:09Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "image", "source": {"data": "AAAA"}}]}]}},
            {"type": "assistant", "timestamp": "2026-10-01T10:01:00Z",
             "message": {"id": "m2", "usage": {"output_tokens": 20, "cache_read_input_tokens": 200},
                         "content": [
                             {"type": "tool_use", "id": "c1", "name": "mcp__kompas3d__kompas_check_drawing", "input": {}},
                             {"type": "tool_use", "id": "r1", "name": "Read", "input": {}}]}},
            {"type": "user", "timestamp": "2026-10-01T10:01:02Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "Проверок: 15, FAIL: 2"},
                # чужой результат с текстом «FAIL: 3» не должен считаться проверкой
                {"type": "tool_result", "tool_use_id": "r1", "content": "в файле строка FAIL: 3"}]}},
        ])
        r = M.analyze_transcript(p)
        self.assertEqual(r["turns"], 2)
        self.assertEqual(r["output_tokens"], 520)
        self.assertEqual(r["cache_read"], 300)
        self.assertEqual(r["scripts"], 1)
        self.assertEqual(r["images_read"], 1)
        self.assertEqual(r["checks"], 1)
        self.assertEqual(r["check_fails"], 1)
        self.assertEqual(r["duration_s"], 62)

    def test_check_inside_batch_is_counted(self):
        p = self._w([
            {"type": "assistant", "timestamp": "2026-10-01T10:00:00Z",
             "message": {"id": "m1", "usage": {}, "content": [
                 {"type": "tool_use", "id": "b1", "name": "mcp__kompas3d__kompas_batch",
                  "input": {"actions": '[{"tool":"kompas_check_drawing","args":{}}]'}},
                 {"type": "tool_use", "id": "b2", "name": "mcp__kompas3d__kompas_batch",
                  "input": {"actions": '[{"tool":"kompas_measure","args":{}}]'}}]}},
            {"type": "user", "timestamp": "2026-10-01T10:00:01Z", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "b1", "content": "FAIL: 0"},
                {"type": "tool_result", "tool_use_id": "b2", "content": "FAIL: 5"}]}},
        ])
        r = M.analyze_transcript(p)
        self.assertEqual(r["checks"], 1)
        self.assertEqual(r["check_fails"], 0)

    def test_only_drawing_includes_build_drawing_runs(self):
        """--only-drawing: запуск с kompas_build_drawing (прямо или через Bash run_tool.py)
        — чертёжный, даже без отдельного kompas_check_drawing."""
        def run(name, inp):
            return self._w([{"type": "assistant", "timestamp": "2026-10-01T10:00:00Z",
                             "message": {"id": "m1", "usage": {}, "content": [
                                 {"type": "tool_use", "id": "x1", "name": name, "input": inp}]}}])
        direct = M.analyze_transcript(run("mcp__kompas3d__kompas_build_drawing", {"spec_path": "a"}))
        bash = M.analyze_transcript(run("Bash", {"command":
            "python experiments/run_tool.py kompas_build_drawing '{\"spec_path\": \"a\"}'"}))
        other = M.analyze_transcript(run("Bash", {"command": "git status"}))
        # правка кода сервера с этим именем — не запуск чертежа
        edit = M.analyze_transcript(run("Edit", {"old_string": "def kompas_build_drawing("}))
        self.assertEqual((direct["drawing_builds"], bash["drawing_builds"], other["drawing_builds"],
                          edit["drawing_builds"]), (1, 1, 0, 0))
        self.assertTrue(M.is_drawing_run(direct))
        self.assertTrue(M.is_drawing_run(bash))
        self.assertFalse(M.is_drawing_run(other))

if __name__ == "__main__":
    unittest.main()
