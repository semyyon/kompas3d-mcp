# -*- coding: utf-8 -*-
"""Структурное сравнение чертежа с эталоном по объектам (задача 16): два .cdw (или два JSON
дампа tools/cdw_structure.py) → таблица критериев с баллами 0/1/2 и сумма.

    python -X utf8 tools/drawing_benchmark.py <эталон.cdw|json> <наш.cdw|json> [--md] [--json out]

Критерии (из разбора docs/plate-vs-reference-2026-10-15.md):
  1 листы и масштабы · 2 состав видов и подписи «k места» · 3 центровые на всех отверстиях ·
  4 полнота размеров (число, пропущенные значения эталона) · 5 группы «N отв.Ø» · 6 рамки
  теоретически точных размеров · 7 допуски расположения (число, значения) · 8 ТТ · 9 штамп ·
  10 оформление: Ø-тексты на полках, выноски «s» · 11 толщина выноской.
Визуальные дефекты (наложения, читаемость) инструмент не судит — их смотрит агент по PNG.
Балл: 2 — как в эталоне или лучше, 1 — частично, 0 — нет. Только чтение.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import drawing_rules  # noqa: E402

_FRAME_RE = re.compile(r"@80~\$\((.*?)\)\$")


def plain(s):
    s = _FRAME_RE.sub(lambda m: m.group(1), s or "")
    return re.sub(r"@\d+~", "", s).strip()


def load(path):
    """JSON дампа или .cdw (тогда — дамп через cdw_structure.py в соседнем процессе)."""
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    out = os.path.splitext(path)[0] + ".structure.json"
    subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, "cdw_structure.py"), path, "--out", out],
                   check=True, capture_output=True)
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def _views(d):
    return [v for v in d.get("views", []) if v.get("kind") != "system"]


def _dims(d, kinds=("lin", "dia", "rad", "ang")):
    out = []
    for v in _views(d):
        for k in kinds:
            for o in v.get(k, []):
                tx = o.get("texts", {})
                out.append({"view": v["i"], "kind": k, "text": plain(tx.get("NominalText", {}).get("str")),
                            "prefix": (tx.get("Prefix", {}) or {}).get("str", "") or "",
                            "frame": bool(o.get("text", {}).get("Rectangle")) or bool(_FRAME_RE.search(
                                tx.get("NominalText", {}).get("str") or "")),
                            "sign": o.get("text", {}).get("Sign")})
    return out


def _scale(v):
    s = v.get("Scale")
    return round(float(s), 4) if s else None


def _stamp(d, cell):
    try:
        raw = (d["sheets"][0]["stamp"].get(str(cell)) or d["sheets"][0]["stamp"].get(cell) or {}).get("str", "")
        return drawing_rules.stamp_text(raw).strip()
    except Exception:
        return ""


def compare(ref, ours):
    rows = []

    def row(n, title, score, ref_txt, our_txt, note=""):
        rows.append({"n": n, "title": title, "score": score, "ref": ref_txt, "ours": our_txt, "note": note})

    # 1 листы и масштабы
    rs, os_ = ref.get("sheets", []), ours.get("sheets", [])
    rv, ov = _views(ref), _views(ours)
    r_main = [_scale(v) for v in rv if v.get("kind") == "proj"][:1]
    o_main = [_scale(v) for v in ov if v.get("kind") == "proj"][:1]
    r_det = sorted({_scale(v) for v in rv if v.get("kind") == "detail"} - {None})
    o_det = sorted({_scale(v) for v in ov if v.get("kind") == "detail"} - {None})
    sc = 2 if (len(rs) == len(os_) and r_main == o_main) else (1 if r_main == o_main or len(rs) == len(os_) else 0)
    row(1, "Листы и масштабы", sc, f"{len(rs)} л., главный {r_main}, выносные {r_det}",
        f"{len(os_)} л., главный {o_main}, выносные {o_det}")

    # 2 состав видов и подписи
    def kinds(vs):
        return {k: sum(1 for v in vs if v.get("kind") == k) for k in ("proj", "section", "detail")}
    rk, ok = kinds(rv), kinds(ov)
    r_pl = sum(1 for v in rv if "мест" in ((v.get("designation") or {}).get("Designation") or ""))
    o_pl = sum(1 for v in ov if "мест" in ((v.get("designation") or {}).get("Designation") or ""))
    sc = 2 if (rk["proj"] == ok["proj"] and ok["detail"] >= rk["detail"] and o_pl >= r_pl) else \
        (1 if ok["detail"] > 0 or ok["proj"] == rk["proj"] else 0)
    row(2, "Состав видов, подписи «k места»", sc, f"{rk}, «мест»: {r_pl}", f"{ok}, «мест»: {o_pl}")

    # 3 центровые: доля видов (проекции и выносные), где маркеров центра не меньше окружностей
    def covered(vs):
        judged = [v for v in vs if v.get("kind") in ("proj", "detail") and v.get("n_circles")]
        ok_ = [v for v in judged if (v.get("centre_markers") or {}).get("n", 0) >= v.get("n_circles", 0)]
        return (len(ok_) / len(judged) if judged else 1.0), [v["i"] for v in judged if v not in ok_]
    rc, rbad = covered(rv)
    oc, obad = covered(ov)
    sc = 2 if oc >= rc - 1e-9 else (1 if oc >= 0.5 * rc else 0)
    row(3, "Центровые на всех отверстиях (доля видов с полным покрытием)", sc,
        f"{rc:.2f} (без покрытия: {rbad})", f"{oc:.2f} (без покрытия: {obad})")

    # 4 полнота размеров
    rd, od = _dims(ref), _dims(ours)
    r_vals = {d["text"] for d in rd if d["text"]}
    o_vals = {d["text"] for d in od if d["text"]}
    missing = sorted(r_vals - o_vals, key=lambda s: (len(s), s))
    frac = len(od) / len(rd) if rd else 1.0
    sc = 2 if frac >= 0.9 else (1 if frac >= 0.5 else 0)
    row(4, "Полнота размеров", sc, f"{len(rd)} (lin/dia/rad/ang)", f"{len(od)}",
        f"значений эталона нет у нас: {', '.join(missing[:12])}" + (" …" if len(missing) > 12 else ""))

    # 5 группы отверстий
    def groups(ds):
        return sorted(f"{d['prefix'].strip()} Ø{d['text']}" for d in ds if d["kind"] == "dia" and "отв" in d["prefix"])
    rg, og = groups(rd), groups(od)
    sc = 2 if len(og) >= len(rg) and len(og) > 0 else (1 if og else 0)
    row(5, "Группы «N отв.Ø» (Ø-размер с префиксом)", sc, f"{len(rg)}: {', '.join(rg)}", f"{len(og)}: {', '.join(og)}")

    # 6 рамки
    rf, of = sum(1 for d in rd if d["frame"]), sum(1 for d in od if d["frame"])
    sc = 2 if (of >= rf and rf > 0) or (rf == 0 and of == 0) else (1 if of > 0 else 0)
    row(6, "Рамки теоретически точных размеров", sc, str(rf), str(of))

    # 7 допуски
    def tols(d):
        return sorted(f"{t.get('Sign')}:{t.get('Value')}" for v in _views(d) for t in v.get("tolerances", []))
    rt, ot = tols(ref), tols(ours)
    sc = 2 if len(ot) >= len(rt) and len(ot) > 0 else (1 if ot else 0)
    row(7, "Допуски расположения (знак:значение)", sc, f"{len(rt)}: {', '.join(rt)}", f"{len(ot)}: {', '.join(ot)}")

    # 8 ТТ
    def tt(d):
        """Пункты ТТ: строки без номера в начале — продолжение предыдущего пункта (ручной блок
        переносит длинные строки на TextLine)."""
        out = []
        for s in (d.get("tt") or {}).get("lines", []):
            s = re.sub(r"\s+", " ", s).strip()
            if not s:
                continue
            if out and not re.match(r"^\d+[\.\)]?\s", s):
                out[-1] = out[-1] + " " + s
            else:
                out.append(s)
        return [s.rstrip(".") for s in out]
    rtt, ott = tt(ref), tt(ours)
    sc = 2 if rtt == ott else (1 if set(rtt) & set(ott) else 0)
    row(8, "ТТ", sc, " | ".join(rtt), " | ".join(ott))

    # 9 штамп
    cells = {1: "наименование", 2: "обозначение", 3: "материал", 6: "масштаб", 7: "лист", 8: "листов",
             9: "организация", 25: "перв. примен."}
    diff = [f"{name}: «{_stamp(ref, c)}» ↔ «{_stamp(ours, c)}»" for c, name in cells.items()
            if (_stamp(ref, c) or "").strip() != (_stamp(ours, c) or "").strip()]
    sc = 2 if not diff else (1 if len(diff) <= 2 else 0)
    row(9, "Штамп (графы 1,2,3,6,7,8,9,25)", sc, "", "", "; ".join(diff) or "совпадает")

    # 10 Ø-тексты на полках (TextType 2 не читается — косвенно: ShelfLength задан) — число dia
    r_dia, o_dia = sum(1 for d in rd if d["kind"] == "dia"), sum(1 for d in od if d["kind"] == "dia")
    sc = 2 if o_dia >= r_dia else (1 if o_dia else 0)
    row(10, "Ø-размеры (число)", sc, str(r_dia), str(o_dia))

    # 11 толщина выноской «s»
    def s_leader(d):
        return [l for v in _views(d) for l in v.get("leaders", [])
                if re.match(r"^s\s*\d", ((l.get("texts") or {}).get("TextOnShelf") or {}).get("str") or "")]
    rl, ol = s_leader(ref), s_leader(ours)
    sc = 2 if (bool(rl) == bool(ol)) else (1 if ol else 0)
    row(11, "Толщина выноской «s…»", sc, str(len(rl)), str(len(ol)))

    total = sum(r["score"] for r in rows)
    return {"rows": rows, "total": total, "max": 2 * len(rows)}


def to_md(res, ref_name, our_name):
    lines = [f"| № | Критерий | Эталон ({ref_name}) | Наш ({our_name}) | Балл |", "|---|---|---|---|---|"]
    for r in res["rows"]:
        note = f" — {r['note']}" if r.get("note") else ""
        lines.append(f"| {r['n']} | {r['title']} | {r['ref']} | {r['ours']}{note} | **{r['score']}** |")
    lines.append(f"| | **Итого** | | | **{res['total']} / {res['max']}** |")
    return "\n".join(lines)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    ref, ours = load(args[0]), load(args[1])
    res = compare(ref, ours)
    if "--json" in sys.argv:
        with open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    print(to_md(res, os.path.basename(args[0]), os.path.basename(args[1])))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
