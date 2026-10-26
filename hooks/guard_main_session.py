# hooks/guard_main_session.py
"""Главная сессия не чертит: kompas_run_script с 2D-кодом (.cdw, D., _draw,
LineDimensions, IView) из главной сессии блокируется — это работа kompas-draftsman.

Признак субагента — поле `agent_id` во входе PreToolUse (документация Claude Code,
https://code.claude.com/docs/en/hooks, «Common input fields»: `agent_id` — id субагента,
в котором сработал хук; в главной сессии его нет). `agent_type` не годится: он есть
и в главной сессии, запущенной с `--agent`."""
import json, re, sys
SUBAGENT_KEYS = ("agent_id",)   # уточнено в Task 3 Step 3 (см. докстринг)
DRAW_RE = re.compile(r"\.cdw|\bD\.|_draw|LineDimension|DiametralDimension|ISymbols2DContainer|AddStandartViews")
# шлюз kompas_api: кода Python там нет — признаки чертежа это имена 2D-интерфейсов в операторах
GATEWAY_2D_RE = re.compile(r"IKompasDocument2D|IDrawingDocument|IDrawingContainer|IViewsAndLayersManager|"
                           r"\bIViews?\b|ksDocument2D|IFragmentDocument|IDrawingGroups|\bIStamp\b|\bITechnicalDemand\b")
def main():
    try:      # сообщение по-русски: stderr в UTF-8, а не в кодовой странице консоли (cp1251) —
        sys.stderr.reconfigure(encoding="utf-8")   # иначе читающая сторона (Claude Code, тесты) видит мусор
    except Exception:
        pass
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if any(data.get(k) for k in SUBAGENT_KEYS):
        return 0
    code = json.dumps(data.get("tool_input", {}), ensure_ascii=False)
    gateway = str(data.get("tool_name", "")).endswith("kompas_api")
    if DRAW_RE.search(code) or (gateway and GATEWAY_2D_RE.search(code)):
        sys.stderr.write("Главная сессия не чертит: делегируй чертёж агенту kompas-draftsman "
                         "(конвейер drawing_spec → kompas_build_drawing).\n")
        return 2
    return 0
if __name__ == "__main__":
    sys.exit(main())
