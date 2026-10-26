# tools/drawing_metrics.py
"""Метрики запусков агентов по транскриптам Claude Code (JSONL).

usage одного assistant-сообщения размазан по нескольким строкам, и в ранних
строках он частичный — берём максимум по message.id (иначе output ×3)."""
import argparse, glob, json, os, re
from datetime import datetime

_FAIL_RE = re.compile(r"FAIL:\s*(\d+)")

def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def analyze_transcript(path):
    usage, tools, t0, t1 = {}, {}, None, None
    images = checks = fails = builds = 0
    pending_check = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("timestamp")
            if ts:
                t = _ts(ts); t0 = t0 or t; t1 = t
            msg = rec.get("message") or {}
            content = msg.get("content")
            if rec.get("type") == "assistant":
                mid = msg.get("id")
                u = msg.get("usage") or {}
                if mid:
                    cur = usage.setdefault(mid, {"o": 0, "c": 0})
                    cur["o"] = max(cur["o"], u.get("output_tokens", 0) or 0)
                    cur["c"] = max(cur["c"], u.get("cache_read_input_tokens", 0) or 0)
                for b in content if isinstance(content, list) else []:
                    if b.get("type") == "tool_use":
                        n = b.get("name", "").split("__")[-1]
                        tools[n] = tools.get(n, 0) + 1
                        # сборка чертежа — прямой вызов, в пакете или Bash run_tool.py
                        if n == "kompas_build_drawing" or (
                                n in ("Bash", "PowerShell", "kompas_batch")
                                and "kompas_build_drawing" in json.dumps(b.get("input") or {})):
                            builds += 1
                        # проверка — прямой вызов или внутри kompas_batch
                        if n == "kompas_check_drawing" or (
                                n == "kompas_batch"
                                and "kompas_check_drawing" in json.dumps(b.get("input") or {})):
                            pending_check.add(b.get("id"))
            elif rec.get("type") == "user" and isinstance(content, list):
                for b in content:
                    if b.get("type") != "tool_result":
                        continue
                    c = b.get("content")
                    is_check = b.get("tool_use_id") in pending_check
                    items = c if isinstance(c, list) else [{"type": "text", "text": c or ""}]
                    for it in items:
                        if it.get("type") == "image":
                            images += 1
                        m = _FAIL_RE.search(it.get("text") or "") if is_check else None
                        if m:
                            checks += 1
                            if int(m.group(1)) > 0:
                                fails += 1
                            break  # одна проверка на один tool_result
    return {
        "file": os.path.basename(path),
        "turns": len(usage),
        "output_tokens": sum(v["o"] for v in usage.values()),
        "cache_read": sum(v["c"] for v in usage.values()),
        "tool_calls": tools,
        "scripts": tools.get("kompas_run_script", 0),
        "images_read": images,
        "checks": checks,
        "check_fails": fails,
        "drawing_builds": builds,
        "duration_s": int((t1 - t0).total_seconds()) if t0 and t1 else 0,
    }

def is_drawing_run(row):
    """Чертёжный запуск: была проверка kompas_check_drawing или сборка kompas_build_drawing."""
    return bool(row["tool_calls"].get("kompas_check_drawing") or row.get("drawing_builds"))

def _files(target, since=None):
    paths = [target] if os.path.isfile(target) else glob.glob(
        os.path.join(target, "**", "*.jsonl"), recursive=True)
    if since:
        lim = datetime.fromisoformat(since).timestamp()
        paths = [p for p in paths if os.path.getmtime(p) >= lim]
    return sorted(paths)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target"); ap.add_argument("--since"); ap.add_argument("--md", action="store_true")
    ap.add_argument("--only-drawing", action="store_true",
                    help="только запуски с kompas_check_drawing или kompas_build_drawing")
    a = ap.parse_args()
    rows = [analyze_transcript(p) for p in _files(a.target, a.since)]
    if a.only_drawing:
        rows = [r for r in rows if is_drawing_run(r)]
    if a.md:
        print("| файл | ходы | output | cache_read | скрипты | картинки | проверки | с FAIL | мин |")
        print("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['file'][:24]} | {r['turns']} | {r['output_tokens']} | {r['cache_read']} | "
                  f"{r['scripts']} | {r['images_read']} | {r['checks']} | {r['check_fails']} | "
                  f"{r['duration_s'] // 60} |")
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
