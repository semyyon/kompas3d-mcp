# -*- coding: utf-8 -*-
"""Проверка хука бэкапа. Запуск: python hooks/test_check_backup.py

Зачем отдельный тест: хук с синтаксической ошибкой завершается с кодом 1,
а Claude Code трактует ненулевой код (кроме 2) как «не блокировать». То есть
сломанный хук НЕ падает заметно — он молча перестаёт защищать. Один прогон
этого файла отвечает, работает защита или нет.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_backup.py")
BLOCK, PASS = "БЛОК", "пропуск"


def call(payload):
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 2:
        return BLOCK
    if r.returncode == 0:
        return PASS
    return f"СЛОМАН (код {r.returncode}): {(r.stderr or '').strip()[:200]}"


def main():
    d = tempfile.mkdtemp(prefix="hooktest_")
    a3d = os.path.join(d, "сборка.a3d")
    bak = os.path.join(d, "сборка_backup.a3d")
    open(a3d, "wb").write(b"x" * 100)
    code = 'doc.Save()  # "%s"' % a3d

    def batch(c):
        return {"actions": json.dumps(
            [{"tool": "kompas_run_script", "args": {"code": c}}], ensure_ascii=False)}

    cases = []

    def case(name, payload, want):
        cases.append((name, payload, want))

    case("run_script с Save, бэкапа нет",
         {"tool_name": "mcp__kompas3d__kompas_run_script", "tool_input": {"code": code}}, BLOCK)
    case("batch с Save внутри, бэкапа нет",
         {"tool_name": "mcp__kompas3d__kompas_batch", "tool_input": batch(code)}, BLOCK)
    case("batch без Save",
         {"tool_name": "mcp__kompas3d__kompas_batch", "tool_input": {"actions": "[]"}}, PASS)
    case("batch с битым JSON — не падать",
         {"tool_name": "mcp__kompas3d__kompas_batch", "tool_input": {"actions": "{не json"}}, PASS)
    case("посторонний инструмент",
         {"tool_name": "mcp__kompas3d__kompas_measure", "tool_input": {}}, PASS)
    case("мусор на входе — не падать", {}, PASS)

    ok = True
    for name, payload, want in cases:
        got = call(payload)
        mark = "OK  " if got == want else "ПРОВАЛ"
        ok &= got == want
        print(f"  {mark} {name:38s} ожидалось {want:8s} получено {got}")

    # сценарии, зависящие от возраста бэкапа
    open(bak, "wb").write(b"x" * 100)
    for name, age_h, want in (("бэкап свежий", 0, PASS), ("бэкап старше 8 ч", 9, BLOCK)):
        os.utime(bak, ((time.time() - age_h * 3600),) * 2)
        for tool, payload in (("run_script", {"code": code}), ("batch", batch(code))):
            got = call({"tool_name": f"mcp__kompas3d__kompas_{tool}", "tool_input": payload})
            mark = "OK  " if got == want else "ПРОВАЛ"
            ok &= got == want
            print(f"  {mark} {name + ', ' + tool:38s} ожидалось {want:8s} получено {got}")

    # --- сценарии чертежей: сохранение .cdw требует свежего чистого вердикта ---
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import check_backup as cb
    cdw = os.path.join(d, "чертёж.cdw")
    open(cdw, "wb").write(b"x" * 50)
    os.makedirs(cb.CHECKS_DIR, exist_ok=True)
    vp = os.path.join(cb.CHECKS_DIR, "чертёж.json")
    if os.path.exists(vp):
        os.remove(vp)

    def save_p(path):
        return {"tool_name": "mcp__kompas3d__kompas_save_document",
                "tool_input": {"path": path}}

    def case2(name, payload, want):
        nonlocal_ok = call(payload) == want
        mark = "OK  " if nonlocal_ok else "ПРОВАЛ"
        print(f"  {mark} {name:38s} ожидалось {want:8s} получено {call(payload)}")
        return nonlocal_ok

    ok &= case2("новый .cdw (файла нет) — пропуск",
                save_p(os.path.join(d, "нового_нет.cdw")), PASS)
    ok &= case2(".cdw без отчёта проверки", save_p(cdw), BLOCK)
    io.open(vp, "w", encoding="utf-8").write(json.dumps({"fails": 2, "failed": ["Ч04", "Ч10"]}))
    ok &= case2(".cdw с отчётом, где есть FAIL", save_p(cdw), BLOCK)
    io.open(vp, "w", encoding="utf-8").write(json.dumps({"fails": 0, "failed": []}))
    ok &= case2(".cdw со свежим чистым отчётом", save_p(cdw), PASS)
    os.utime(vp, ((time.time() - 20 * 60),) * 2)
    ok &= case2(".cdw с устаревшим отчётом (20 мин)", save_p(cdw), BLOCK)
    os.utime(vp, None)
    b_cdw = {"tool_name": "mcp__kompas3d__kompas_batch", "tool_input": {"actions": json.dumps(
        [{"tool": "kompas_save_document", "args": {"path": cdw}}], ensure_ascii=False)}}
    ok &= case2("batch с сохранением .cdw, отчёт чист", b_cdw, PASS)
    os.remove(vp)
    ok &= case2("batch с сохранением .cdw, отчёта нет", b_cdw, BLOCK)

    print("\nИТОГ:", "защита работает" if ok else "ЗАЩИТА НЕ РАБОТАЕТ — чинить немедленно")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
