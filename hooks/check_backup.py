# -*- coding: utf-8 -*-
"""PreToolUse-hook: страхует бэкап сборки .a3d при произвольных скриптах.

Читает JSON вызова инструмента из stdin (tool_name, tool_input).
Правило (CLAUDE.md): перед правкой сборки — бэкап `<имя>_backup.a3d`.

Зона ответственности (разделено 2026-08-25):
  - `kompas_save_document` хук больше НЕ трогает: сервер сам делает файловую
    копию непосредственно перед перезаписью .a3d (`_make_backup` в server.py).
    Раньше хук блокировал вызов ДО того, как сервер успевал сделать бэкап, —
    выходил лишний круг «отказ → бэкап вручную → повтор».
  - `kompas_run_script` проверяется по-прежнему: произвольный скрипт может
    вызвать `.Save()` в обход инструмента, и перехватить это больше некому.

Бэкап считается свежим, если моложе 8 часов. Не смогли определить — пропускаем.
"""
import json
import os
import re
import sys
import time

MAX_BACKUP_AGE_H = 8


def backup_ok(a3d_path: str) -> bool:
    if not a3d_path or not os.path.exists(a3d_path):
        return True  # новый файл — бэкапить нечего
    stem, _ = os.path.splitext(a3d_path)
    if stem.endswith("_backup"):
        return True  # сохраняют сам бэкап
    b = stem + "_backup.a3d"
    return os.path.exists(b) and (time.time() - os.path.getmtime(b)) < MAX_BACKUP_AGE_H * 3600


CHECKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "kompas-mcp", "logs", "drawing_checks")
if not os.path.isdir(CHECKS_DIR):
    CHECKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "logs", "drawing_checks")
MAX_VERDICT_AGE_S = 15 * 60


def drawing_verdict_ok(cdw_path: str):
    """(ok, причина) для сохранения .cdw. Правило проекта: чертёж сохраняется
    только со свежим чистым вердиктом kompas_check_drawing.

    Новый файл (ещё не существует) пропускается — иначе нельзя создать черновик.
    Блокируется только ПЕРЕзапись существующего чертежа."""
    if not cdw_path or not os.path.exists(cdw_path):
        return True, ""
    key = os.path.splitext(os.path.basename(cdw_path))[0]
    vp = os.path.join(CHECKS_DIR, key + ".json")
    if not os.path.exists(vp):
        return False, ("нет отчёта проверки. Прогони kompas_check_drawing "
                       f"(document=\"{key}\") и исправь FAIL")
    try:
        v = json.load(open(vp, encoding="utf-8"))
    except Exception:
        return False, "отчёт проверки повреждён — прогони kompas_check_drawing заново"
    age = time.time() - os.path.getmtime(vp)
    if age > MAX_VERDICT_AGE_S:
        return False, f"отчёт проверки устарел ({age/60:.0f} мин) — прогони kompas_check_drawing заново"
    if v.get("fails", 1) > 0:
        return False, ("проверка не пройдена: " + ", ".join(v.get("failed", [])[:5])
                       + " — исправь и прогони kompas_check_drawing")
    return True, ""


def deny(msg: str):
    sys.stderr.write(msg)
    sys.exit(2)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "")
    inp = data.get("tool_input", {}) or {}

    # --- чертежи: сохранение .cdw только со свежим чистым вердиктом ---
    # Сохранение без path пишет файл документа `document=` — его и проверяем
    # (дыра 2026-10-21: annot.cdw с FAIL сохранён через document= без path).
    # Сохранение «активного» без адреса хук не видит — его ловит сам сервер:
    # kompas_save_document прогоняет kompas_check_drawing перед записью .cdw.
    def save_target(args):
        return ((args.get("path") or "").strip()
                or (args.get("document") or "").strip())

    cdw_targets = []
    if tool.endswith("kompas_save_document"):
        p = save_target(inp)
        if p.lower().endswith(".cdw"):
            cdw_targets.append(p)
    elif tool.endswith("kompas_batch"):
        try:
            for a in json.loads(inp.get("actions") or "[]"):
                if isinstance(a, dict) and str(a.get("tool", "")).split("__")[-1] == "kompas_save_document":
                    p = save_target(a.get("args") or {})
                    if p.lower().endswith(".cdw"):
                        cdw_targets.append(p)
        except Exception:
            pass
    for p in cdw_targets:
        ok, why = drawing_verdict_ok(p)
        if not ok:
            deny("ПРАВИЛО ПРОЕКТА: чертёж сохраняется только после чистой самопроверки.\n"
                 f"  {os.path.basename(p)}: {why}")

    if tool.endswith("kompas_batch"):
        # Пакет содержит вложенные вызовы, и Save/SaveAs может быть внутри
        # скрипта одного из действий — иначе пакет стал бы дырой в правиле
        # бэкапа. Payload разбираем по-настоящему: если просто склеить его
        # в строку, пути окажутся дважды экранированы (C:\\... вместо C:\...)
        # и регулярка их не узнает.
        code = ""
        try:
            acts = json.loads(inp.get("actions") or "[]")
            if isinstance(acts, dict):
                acts = [acts]
            for a in acts:
                if not isinstance(a, dict):
                    continue
                if str(a.get("tool", "")).split("__")[-1] == "kompas_run_script":
                    code += ((a.get("args") or {}).get("code", "") or "") + "\n"
        except Exception:
            code = json.dumps(inp, ensure_ascii=False)   # не разобрали — проверим грубо

    elif tool.endswith("kompas_run_script"):
        code = inp.get("code", "") or ""
    else:
        code = ""

    if code:
        if re.search(r"\.(Save|SaveAs)\s*\(", code):
            # .cdw, названный в скрипте с Save/SaveAs, — то же правило, что для инструмента
            for m in re.finditer(r"[\"']([^\"']+?\.cdw)[\"']", code, re.IGNORECASE):
                ok, why = drawing_verdict_ok(m.group(1).replace("\\\\", "\\"))
                if not ok:
                    deny("ПРАВИЛО ПРОЕКТА: чертёж сохраняется только после чистой самопроверки.\n"
                         f"  {os.path.basename(m.group(1))}: {why}")
            missing = []
            for m in re.finditer(r"[\"']([^\"']+?\.a3d)[\"']", code, re.IGNORECASE):
                p = m.group(1).replace("\\\\", "\\")
                if not p.lower().endswith("_backup.a3d") and not backup_ok(p):
                    missing.append(p)
            if missing:
                deny(
                    "ПРАВИЛО ПРОЕКТА: в скрипте есть Save/SaveAs, а у этих сборок нет свежего бэкапа:\n"
                    + "\n".join(f"  {p}" for p in missing)
                    + "\nСоздай файловые копии <имя>_backup.a3d и повтори."
                )

    sys.exit(0)


if __name__ == "__main__":
    main()
