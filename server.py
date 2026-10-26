# -*- coding: utf-8 -*-
"""
MCP server for KOMPAS-3D (ASCON) automation via COM API.

Connects Claude Code to a locally installed KOMPAS-3D through its COM
interfaces (KOMPAS.Application.7 / KOMPAS.Application.5). Each tool call
opens its own COM context, so KOMPAS itself keeps all state (open
documents) between calls.

Run:  python server.py   (stdio transport, registered in Claude Code MCP config)
"""
import functools
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager, redirect_stdout

import pythoncom
import win32com.client
from win32com.client import Dispatch, GetActiveObject

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("kompas3d")

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "calls.jsonl")

# Prefixes every tool uses when it returns a failure as a plain string instead
# of raising, so the call log can tell a real success from a soft error.
_ERR_PREFIXES = ("Failed", "COM error", "Script error", "Job error", "No active",
                 "File not found", "Job not found", "Unknown doc_type", "KOMPAS-3D is not running",
                 "KOMPAS window not found", "Bad params JSON", "Screenshot failed")


def _log_call(name: str, ms: float, ok: bool, note: str = "") -> None:
    """Append one line to logs/calls.jsonl. Never raises - logging must not
    break a tool call."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name,
               "ms": round(ms), "ok": ok}
        if note:
            rec["note"] = note[:200]
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception:
        pass


def _timed(fn):
    """Wrap a tool so its wall-clock duration lands in the call log. Keeps the
    original signature (functools.wraps sets __wrapped__, which FastMCP's
    signature inspection follows), so tool schemas are unaffected."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        t0 = time.perf_counter()
        ok, note = True, ""
        try:
            res = fn(*a, **kw)
            if isinstance(res, str) and res.startswith(_ERR_PREFIXES):
                ok, note = False, res.splitlines()[0]
            if _FRESH_INSTANCE[0] and isinstance(res, str):
                _FRESH_INSTANCE[0] = False
                res += (chr(10) + chr(10) +
                        "ВНИМАНИЕ: работающего КОМПАС не было, поэтому запущен НОВЫЙ, "
                        "пустой экземпляр. Документы, которые были открыты у пользователя, "
                        "закрыты вместе с прежним процессом (файлы на диске не пострадали). "
                        "Не продолжай правку сборки вслепую: сообщи пользователю и открой "
                        "нужные файлы заново.")
            return res
        except Exception as e:
            ok, note = False, f"{type(e).__name__}: {e}"
            raise
        finally:
            _log_call(fn.__name__, (time.perf_counter() - t0) * 1000.0, ok, note)
    return wrapper


_fastmcp_tool = mcp.tool


def _timed_tool(*targs, **tkw):
    """Drop-in replacement for mcp.tool() that inserts the timing wrapper."""
    def deco(fn):
        return _fastmcp_tool(*targs, **tkw)(_timed(fn))
    return deco


mcp.tool = _timed_tool

PROGID_API7 = "KOMPAS.Application.7"
PROGID_API5 = "KOMPAS.Application.5"

DOC_TYPES = {
    "drawing": 1,        # ksDocumentDrawing (.cdw)
    "fragment": 2,       # ksDocumentFragment (.frw)
    "specification": 3,  # ksDocumentSpecification (.spw)
    "part": 4,           # ksDocumentPart (.m3d)
    "assembly": 5,       # ksDocumentAssembly (.a3d)
    "text": 6,           # ksDocumentTextual (.kdw)
}
DOC_TYPE_NAMES = {v: k for k, v in DOC_TYPES.items()}

# DocumentCloseOptions (API7)
KD_DO_NOT_SAVE_CHANGES = 0   # kdDoNotSaveChanges (проверено по stubs/constants.py, 2026-09-03)
KD_SAVE_CHANGES = 1          # kdSaveChanges


# Cross-process lock around KOMPAS. A named Windows mutex, not threading.Lock:
# every Claude Code session gets its own server PROCESS, so the contenders are
# processes. The mutex is owned by a thread and is re-entrant for that thread,
# so nesting (kompas_batch -> tool -> com_context) is safe as long as every
# acquire is matched by a release.
COM_MUTEX_NAME = "Local\\KompasMcpComLock"
COM_LOCK_WAIT_S = 300.0        # must outlast a full-budget kompas_batch (240 s)
_COM_MUTEX = [None]
_COM_ABANDONED = [False]


def _com_mutex():
    import ctypes
    if _COM_MUTEX[0] is None:
        h = ctypes.windll.kernel32.CreateMutexW(None, False, COM_MUTEX_NAME)
        _COM_MUTEX[0] = h or 0
    return _COM_MUTEX[0]


def _acquire_com(timeout_s: float) -> None:
    """Take the KOMPAS lock or raise with a message the agent can act on."""
    import ctypes
    h = _com_mutex()
    if not h:
        return                                  # no mutex: degrade to old behaviour
    r = ctypes.windll.kernel32.WaitForSingleObject(h, int(timeout_s * 1000))
    if r == 0:                                  # WAIT_OBJECT_0
        return
    if r == 0x80:                               # WAIT_ABANDONED: owner died holding it
        _COM_ABANDONED[0] = True
        return
    raise RuntimeError(
        f"КОМПАС занят другим агентом дольше {timeout_s:.0f} с. Повтори вызов; "
        "если повторяется — значит где-то идёт длинная операция, дождись её.")


def _release_com() -> None:
    import ctypes
    h = _COM_MUTEX[0]
    if h:
        ctypes.windll.kernel32.ReleaseMutex(h)


@contextmanager
def com_lock(timeout_s: float = COM_LOCK_WAIT_S):
    """Hold the KOMPAS lock across several calls — used by kompas_batch so the
    whole chain is atomic and no other agent can switch the active document
    between its actions."""
    _acquire_com(timeout_s)
    try:
        yield
    finally:
        _release_com()


@contextmanager
def com_context():
    """Initialize the COM apartment for the current thread.

    Deliberately does NOT call CoUninitialize(): the COM proxy objects
    created inside the `with` block (app, doc, docs, ...) are Python
    locals in the *caller's* function and are only released after that
    function returns, i.e. after this context manager's __exit__ would
    already have run. Releasing a COM proxy on an apartment that was
    already uninitialized is undefined behavior and was crashing/
    corrupting the host process. CoInitialize is idempotent per thread
    (returns S_FALSE on repeat calls), so it's safe to call every time
    and simply never tear down; the OS reclaims the apartment when the
    thread exits.

    Also takes the cross-process COM lock, so two agents cannot interleave
    inside one KOMPAS. This is not only about future parallel work: Claude Code
    already runs a SEPARATE server process per session (two were observed on
    2026-08-26), so an in-process threading.Lock would not have helped.
    """
    pythoncom.CoInitialize()
    _acquire_com(COM_LOCK_WAIT_S)
    try:
        yield
    finally:
        _release_com()


def _get_running(progid):
    """Return the already-running COM instance for progid, or None."""
    try:
        return GetActiveObject(progid)
    except pythoncom.com_error:
        return None


# Set when a tool had to START KOMPAS because none was running. A silent
# restart is dangerous: the user's documents are gone and the agent would
# happily keep working in an empty fresh instance believing it is the same
# session. The flag is surfaced on the next tool result and logged.
_FRESH_INSTANCE = [False]


def get_app7():
    """Connect to running KOMPAS or start it. Returns IApplication (API7).
    Prefers an already-running instance (via ROT) over Dispatch's
    fall-through creation, to avoid spawning a second KOMPAS process."""
    app = _get_running(PROGID_API7)
    if app is None:
        app = Dispatch(PROGID_API7)
        _FRESH_INSTANCE[0] = True
        _log_call("KOMPAS_STARTED", 0, True,
                  "no running instance found - a NEW KOMPAS was launched; "
                  "any documents the user had open are gone")
    app.Visible = True
    return app


def get_app5():
    """Connect to the running KOMPAS API5 object, or start it. Prefers an
    already-running instance over Dispatch's fall-through creation, for the
    same reason as get_app7 — avoids launching a duplicate KOMPAS process
    when API5 and API7 are requested close together."""
    return _get_running(PROGID_API5) or Dispatch(PROGID_API5)


def format_error(exc: Exception) -> str:
    if isinstance(exc, pythoncom.com_error):
        details = exc.excepinfo[2] if exc.excepinfo and len(exc.excepinfo) > 2 else ""
        return f"COM error: {exc.strerror or exc.hresult}. {details}".strip()
    return f"{type(exc).__name__}: {exc}"


def iter_documents(docs):
    """Yield documents from an API7 Documents collection (0-based index)."""
    count = docs.Count
    for i in range(count):
        try:
            yield docs.Item(i)
        except Exception:
            # some collections are 1-based
            yield docs.Item(i + 1)


def describe_document(doc, active_path: str = "") -> dict:
    info = {}
    try:
        info["name"] = doc.Name
        info["path"] = doc.PathName
        info["type"] = DOC_TYPE_NAMES.get(doc.DocumentType, str(doc.DocumentType))
        info["active"] = bool(active_path) and doc.PathName == active_path
    except Exception as e:
        info["error"] = format_error(e)
    return info


@mcp.tool()
def kompas_status() -> str:
    """Check whether KOMPAS-3D is running and list open documents.
    Does NOT start KOMPAS if it is not running (use kompas_launch for that)."""
    with com_context():
        try:
            app = GetActiveObject(PROGID_API7)
        except pythoncom.com_error:
            return "KOMPAS-3D is not running. Use kompas_launch to start it."
        try:
            name = app.ApplicationName(FullName=True)
            docs = app.Documents
            lines = [f"Running: {name}", f"Open documents: {docs.Count}"]
            active_path = ""
            try:
                active = app.ActiveDocument
                if active is not None:
                    active_path = active.PathName
                    lines.append(
                        f"Active document: {active.Name} "
                        f"({DOC_TYPE_NAMES.get(active.DocumentType, '?')}) {active_path or '[not saved]'}"
                    )
            except Exception:
                pass
            phantoms = 0
            for d in iter_documents(docs):
                i = describe_document(d, active_path)
                if not i.get("name") and not i.get("path"):
                    phantoms += 1  # traces of invisible documents, harmless
                    continue
                lines.append(f"- {i.get('name')} ({i.get('type')}) {i.get('path') or '[not saved]'}")
            if phantoms:
                lines.append(f"(+{phantoms} phantom entries from invisible documents — harmless)")
            return "\n".join(lines)
        except Exception as e:
            return f"KOMPAS is running but query failed. {format_error(e)}"


@mcp.tool()
def kompas_launch() -> str:
    """Start KOMPAS-3D (or connect to a running instance) and make it visible."""
    with com_context():
        try:
            app = get_app7()
            return f"Connected: {app.ApplicationName(FullName=True)}"
        except Exception as e:
            return f"Failed to launch KOMPAS. {format_error(e)}"


@mcp.tool()
def kompas_create_document(doc_type: str) -> str:
    """Create a new KOMPAS document and make it active.
    doc_type: one of 'part' (.m3d), 'assembly' (.a3d), 'drawing' (.cdw),
    'fragment' (.frw), 'specification' (.spw), 'text' (.kdw)."""
    t = DOC_TYPES.get(doc_type.strip().lower())
    if t is None:
        return f"Unknown doc_type '{doc_type}'. Use one of: {', '.join(DOC_TYPES)}"
    with com_context():
        try:
            app = get_app7()
            doc = app.Documents.Add(t, True)
            if doc is None:
                return "KOMPAS returned no document (Add failed)."
            return f"Created new {doc_type} document: {doc.Name}"
        except Exception as e:
            return f"Failed to create document. {format_error(e)}"


@mcp.tool()
def kompas_open_document(path: str) -> str:
    """Open an existing KOMPAS document (.m3d, .a3d, .cdw, .frw, .spw, .kdw)
    by absolute path and make it visible."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return f"File not found: {path}"
    with com_context():
        try:
            app = get_app7()
            doc = app.Documents.Open(path, True, False)
            if doc is None:
                return f"KOMPAS could not open: {path}"
            return f"Opened: {doc.Name} ({DOC_TYPE_NAMES.get(doc.DocumentType, '?')})"
        except Exception as e:
            return f"Failed to open document. {format_error(e)}"


def _drawing_save_refusal(check_text: str) -> str:
    """По ответу kompas_check_drawing: "" — сохранять можно, иначе причина отказа."""
    v = _check_json(check_text)
    if v is None:
        return "проверка чертежа не выполнена: " + (check_text or "").strip()[:300]
    if v.get("fails", 1):
        codes = ", ".join(e["code"] for e in v.get("errors", [])[:8])
        return (f"проверка: FAIL {v.get('fails')} ({codes}). Чертёж сохраняется только "
                "при 0 FAIL — исправь по kompas_check_drawing")
    return ""


@mcp.tool()
def kompas_save_document(path: str = "", document: str = "") -> str:
    """Save the active document. With no path performs Save (document must
    already have a file). With a path performs SaveAs to that path; a
    non-native extension (e.g. .step, .stl, .igs, .dxf, .pdf) triggers
    format conversion by KOMPAS. A .cdw target is saved only if
    kompas_check_drawing on this document gives 0 FAIL."""
    with com_context():
        try:
            app = get_app7()
            # Сохранить НЕ ТОТ документ — самая разрушительная ошибка из возможных,
            # поэтому адрес проверяется до всего остального.
            err = _activate(app, document)
            if err:
                return err
            doc = app.ActiveDocument
            if doc is None:
                return "No active document."
            if path:
                path = os.path.abspath(path)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                target = path
            else:
                if not doc.PathName:
                    return "Document was never saved — pass an explicit path."
                target = doc.PathName
            # Правило проекта: .cdw пишется только при 0 FAIL kompas_check_drawing.
            # Проверка здесь, а не только в хуке: хук видит аргументы до вызова
            # и не знает, какой документ сохраняется без path (дыра 2026-10-21).
            if target.lower().endswith(".cdw"):
                why = _drawing_save_refusal(
                    _unwrapped("kompas_check_drawing")(document=doc.PathName or ""))
                if why:
                    return f"Failed: {os.path.basename(target)} не сохранён — {why}"
            # Project rule: an assembly is never overwritten without a copy of
            # the previous state. The on-disk file is still the last saved
            # version at this point, so backing it up here captures exactly
            # that. Done before the save so a failed backup blocks the save.
            note = ""
            if target.lower().endswith(".a3d") and os.path.exists(target):
                note = " | " + _make_backup(target)
            if path:
                doc.SaveAs(path)
            else:
                doc.Save()
            if os.path.exists(target):
                return f"Saved: {target} ({os.path.getsize(target)} bytes){note}"
            return f"Save command completed, but file not found at {target} (check KOMPAS dialogs).{note}"
        except Exception as e:
            return f"Failed to save. {format_error(e)}"


@mcp.tool()
def kompas_close_document(save: bool = False, document: str = "") -> str:
    """Close a document: `document` (path or part of name) or, if empty, the
    active one. save=True saves changes first (document must have a path),
    save=False discards changes."""
    with com_context():
        try:
            app = get_app7()
            err = _activate(app, document)
            if err:
                return err
            doc = app.ActiveDocument
            if doc is None:
                return "No active document."
            name = doc.Name
            if save:
                path = doc.PathName or ""
                if not path:
                    return "Failed: документ ни разу не сохранён — kompas_save_document(path=...), затем закрыть."
                if path.lower().endswith(".cdw"):
                    return ("Failed: чертёж сохраняется только kompas_save_document (проверка чертежа); "
                            "сохрани им, затем закрой с save=False.")
                if path.lower().endswith(".a3d") and os.path.exists(path):
                    _make_backup(path)                    # правило проекта: перед перезаписью сборки
                doc.Save()
            doc.Close(KD_DO_NOT_SAVE_CHANGES)
            return f"Closed: {name}"
        except Exception as e:
            return f"Failed to close. {format_error(e)}"


@mcp.tool()
def kompas_screenshot(path: str = "", region: str = "window", max_px: int = 1400) -> str:
    """Capture KOMPAS-3D to a PNG file and return its path and actual size.
    region="window" — whole main window; region="sheet" — only the document
    view (no ribbon/panels). Для чертежа — region='sheet'; одна на чертёж.
    The image is downscaled to max_px on the long side; картинка ≈
    ⌈w/28⌉×⌈h/28⌉ токенов. Default path is in the temp directory."""
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image

    if not path:
        path = os.path.join(tempfile.gettempdir(), "kompas_screenshot.png")
    path = os.path.abspath(path)

    with com_context():
        try:
            # NB: app5.ksGetHWindow is an int property pointing at a helper
            # window that is 160x28 while KOMPAS is minimized — using it is
            # what produced the useless 160x28 screenshots. Use the real
            # shell window and un-minimize it first.
            if region not in ("window", "sheet"):
                return "region must be 'window' or 'sheet'."
            hwnd, _view = _kompas_windows()
            if not hwnd:
                return "KOMPAS window not found. Is KOMPAS running?"
            _restore(hwnd)
            if region == "sheet":
                if _view == hwnd:   # 2D document: no D3View, the sheet is class "View"
                    kids = []
                    win32gui.EnumChildWindows(hwnd, lambda c, _: kids.append(c), None)
                    _view = next((c for c in kids if win32gui.GetClassName(c) == "View"
                                  and win32gui.IsWindowVisible(c)), hwnd)
                hwnd = _view

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            w, h = right - left, bottom - top
            if w <= 0 or h <= 0:
                return "KOMPAS window has zero size (minimized?)."

            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bmp)
            ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
            bgra = bmp.GetBitmapBits(True)
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

            img = Image.frombuffer("RGBA", (w, h), bgra, "raw", "BGRA", 0, 1).convert("RGB")
            if region == "sheet":
                img = _crop_panels(img)   # drawing tree / property panels painted inside
            img.thumbnail((max_px, max_px))
            img.save(path, "PNG")
            return f"Screenshot saved: {path} ({img.width}x{img.height})"
        except Exception as e:
            return f"Screenshot failed. {format_error(e)}"


_DRAW_CACHE = {"mtime": None, "mod": None}
def _load_draw():
    import importlib.util as _ilu
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs", "_draw.py")
    mt = os.path.getmtime(p)
    if _DRAW_CACHE["mtime"] != mt:
        spec = _ilu.spec_from_file_location("_draw", p)
        mod = _ilu.module_from_spec(spec); spec.loader.exec_module(mod)
        _DRAW_CACHE.update(mtime=mt, mod=mod)
    return _DRAW_CACHE["mod"]


@mcp.tool()
def kompas_run_script(code: str) -> str:
    """Execute Python code against the live KOMPAS-3D COM API. This is the
    power tool for anything the other tools don't cover (sketches, 3D
    operations, dimensions, parameters, drawings, etc.).

    Available in scope:
      app7      - IApplication (API7, KOMPAS.Application.7)
      app5      - KompasObject (API5, KOMPAS.Application.5)
      Dispatch  - win32com.client.Dispatch
      CastTo    - win32com.client.CastTo
      win32com, pythoncom
      DOC_TYPES - {'part': 4, 'assembly': 5, 'drawing': 1, ...}
      D         - module jobs/_draw.py (2D drawing helpers; reloaded when
                  the file changes)
      find_doc  - D.find_doc
    No sys.path / `import _draw` bootstrap is needed — use D directly.

    print() output is captured and returned. Assign to a variable named
    `result` to return its repr as well.
    Example (create a part and extrude a sketch) uses the standard
    kompas_api7 object model: app7.ActiveDocument, TopPart, Sketches etc."""
    with com_context():
        buf = io.StringIO()
        try:
            app7 = get_app7()
            try:
                app5 = get_app5()
            except Exception:
                app5 = None
            D = _load_draw()
            ns = {
                "app7": app7,
                "app5": app5,
                "Dispatch": Dispatch,
                "CastTo": win32com.client.CastTo,
                "win32com": win32com,
                "pythoncom": pythoncom,
                "DOC_TYPES": DOC_TYPES,
                "D": D,
                "find_doc": D.find_doc,
            }
            with redirect_stdout(buf):
                exec(code, ns)
            out = buf.getvalue()
            parts = []
            if out.strip():
                parts.append(out.rstrip())
            if "result" in ns:
                parts.append(f"result = {ns['result']!r}")
            return "\n".join(parts) if parts else "OK (no output)"
        except Exception:
            out = buf.getvalue()
            tb = traceback.format_exc()
            return (out + "\n" if out.strip() else "") + f"Script error:\n{tb}"


def get_typed_api():
    """API5 KompasObject + API7 IApplication via gencache (the reliable pattern:
    the plain dynamic wrappers miss members like ActiveDocument3D)."""
    from win32com.client import gencache
    m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
    kompas = m5.KompasObject(Dispatch("Kompas.Application.5")._oleobj_.QueryInterface(
        m5.KompasObject.CLSID, pythoncom.IID_IDispatch))
    app7 = gencache.EnsureDispatch("Kompas.Application.7")
    return kompas, app7


def _active_3d(kompas, app7):
    doc7 = app7.ActiveDocument
    if doc7 is None or doc7.DocumentType not in (4, 5):
        raise RuntimeError("Active document is not a 3D model (part/assembly).")
    d3 = win32com.client.CastTo(doc7, "IKompasDocument3D")
    return doc7, d3


def _m16(v):
    """Normalize a placement matrix to a flat list of 16 floats.

    API7 `IPlacement.GetMatrix3D()` returns the 16 numbers directly, but the
    API5 `ksPlacement.GetMatrix3D()` wrapper returns `(ok, (16 numbers))`
    because the matrix is an out-parameter. Indexing the API5 result as if it
    were flat silently yields an empty slice - that is what made
    kompas_get_bbox report every component as unreadable."""
    if v is None:
        raise RuntimeError("GetMatrix3D returned nothing")
    v = tuple(v)
    if len(v) == 16:
        return [float(x) for x in v]
    if len(v) == 2 and hasattr(v[1], "__len__") and len(v[1]) == 16:
        if not v[0]:
            raise RuntimeError("GetMatrix3D reported failure")
        return [float(x) for x in v[1]]
    raise RuntimeError(f"unexpected matrix shape: len={len(v)}")


def _mat_mul(a, b):
    return [sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4))
            for i in range(4) for j in range(4)]


def _make_backup(path: str, min_age_s: int = 900) -> str:
    """Copy `<name>.<ext>` to `<name>_backup.<ext>`. Skips if a backup younger
    than min_age_s already exists, so repeated saves in one session cannot
    overwrite the good pre-edit copy with an intermediate state."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return f"File not found: {path}"
    stem, ext = os.path.splitext(path)
    if stem.endswith("_backup"):
        return "This IS a backup file; nothing to do."
    bpath = f"{stem}_backup{ext}"
    if os.path.exists(bpath) and (time.time() - os.path.getmtime(bpath)) < min_age_s:
        age_min = (time.time() - os.path.getmtime(bpath)) / 60.0
        return f"Backup already fresh ({age_min:.0f} min old): {bpath}"
    shutil.copy2(path, bpath)
    return f"Backup created: {bpath} ({os.path.getsize(bpath)} bytes)"


@mcp.tool()
def kompas_backup(path: str) -> str:
    """Create the mandatory file-copy backup `<name>_backup.<ext>` next to the
    given KOMPAS file (project rule before editing any assembly). Refuses to
    overwrite a backup younger than 15 minutes.
    NOTE: kompas_save_document now does this automatically for .a3d files, so
    an explicit call is only needed to snapshot a file you are not saving yet."""
    return _make_backup(path)


@mcp.tool()
def kompas_get_tree(max_depth: int = 3, max_items: int = 300) -> str:
    """Component tree of the active assembly (API7 recursion) with world-space
    origins. Groups repeated components: shows `name xN` when N same-named
    siblings share a file. Use to inspect assembly structure without a script."""
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            doc7, d3 = _active_3d(kompas, app7)
            top = d3.TopPart
            I4 = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
            lines = [f"Document: {doc7.PathName or doc7.Name}"]
            count = [0]

            def walk(p7, parent_m, depth):
                if depth > max_depth or count[0] >= max_items:
                    return
                try:
                    parts = p7.Parts
                    n = parts.Count
                except Exception:
                    return
                groups = {}
                for i in range(n):
                    c = parts.Part(i)
                    key = (c.Name, getattr(c, "FileName", ""))
                    groups.setdefault(key, []).append(c)
                for (name, fname), comps in groups.items():
                    if count[0] >= max_items:
                        lines.append("  " * depth + "… (max_items reached)")
                        return
                    count[0] += 1
                    first = comps[0]
                    try:
                        m = _m16(first.Placement.GetMatrix3D())
                        world = _mat_mul(m, parent_m)
                        org = f"({world[12]:.1f}, {world[13]:.1f}, {world[14]:.1f})"
                    except Exception:
                        world, org = parent_m, "(?)"
                    suffix = f" x{len(comps)}" if len(comps) > 1 else ""
                    hidden = " [hidden]" if getattr(first, "Hidden", False) else ""
                    lines.append("  " * depth + f"- {name}{suffix}{hidden} origin={org}")
                    walk(first, world, depth + 1)

            walk(top, I4, 0)
            if count[0] == 0 and doc7.DocumentType == 4:
                lines.append("(part document — no components)")
            lines.append(f"Total listed component groups: {count[0]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to read tree. {format_error(e)}"


@mcp.tool()
def kompas_get_bbox(component: str = "") -> str:
    """Bounding box via GetGabarit. Without arguments: the whole active 3D
    document (assembly coordinate system). With `component`: the first
    top-level component whose name contains the given substring (matching is
    case-insensitive and normalizes Cyrillic/Latin lookalikes х/x, М/M).
    NOTE: an instance bbox is its rotated local AABB (conservative)."""
    def norm(s):
        return (s or "").lower().replace("x", "х").replace("m", "м")
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            doc7, d3 = _active_3d(kompas, app7)
            doc5 = kompas.ActiveDocument3D()
            if not component:
                p = doc5.GetPart(-1)  # pTop_Part
                g = p.GetGabarit(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                ok, x1, y1, z1, x2, y2, z2 = g
                return (f"{doc7.Name}: X {x1:.3f}..{x2:.3f} ({x2-x1:.3f}), "
                        f"Y {y1:.3f}..{y2:.3f} ({y2-y1:.3f}), "
                        f"Z {z1:.3f}..{z2:.3f} ({z2-z1:.3f})")
            plc = doc5.PartCollection(True)
            if plc is None:
                return (f"{doc7.Name} is a part document — no components. "
                        "Call without `component` for the whole-part bbox.")
            hits, skipped = [], 0
            for i in range(plc.GetCount()):
                try:
                    p = plc.GetByIndex(i)
                    if norm(component) not in norm(p.name):
                        continue
                    g = p.GetGabarit(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    m = _m16(p.GetPlacement().GetMatrix3D())
                    hits.append((p.name, g, m[12:15]))
                    if len(hits) >= 10:
                        break
                except Exception:
                    skipped += 1
            if not hits:
                return (f"No top-level component matching '{component}'."
                        + (f" ({skipped} components unreadable)" if skipped else ""))
            out = []
            if skipped:
                out.append(f"({skipped} components skipped as unreadable)")
            for name, g, org in hits:
                ok, x1, y1, z1, x2, y2, z2 = g
                out.append(f"{name}: origin=({org[0]:.2f},{org[1]:.2f},{org[2]:.2f}) "
                           f"bbox X {x1:.2f}..{x2:.2f}, Y {y1:.2f}..{y2:.2f}, Z {z1:.2f}..{z2:.2f}")
            return "\n".join(out)
        except Exception as e:
            return f"Failed. {format_error(e)}"


@mcp.tool()
def kompas_measure() -> str:
    """Numeric self-check of the active 3D document: gabarit, mass (needs
    material density set), face/body counts, top-level component count.
    Read-only. Use after every modelling step (project rule)."""
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            doc7, d3 = _active_3d(kompas, app7)
            doc5 = kompas.ActiveDocument3D()
            p5 = doc5.GetPart(-1)
            g = p5.GetGabarit(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            ok, x1, y1, z1, x2, y2, z2 = g
            lines = [f"Document: {doc7.PathName or doc7.Name}",
                     f"Gabarit: X {x1:.3f}..{x2:.3f} ({x2-x1:.3f}), "
                     f"Y {y1:.3f}..{y2:.3f} ({y2-y1:.3f}), "
                     f"Z {z1:.3f}..{z2:.3f} ({z2-z1:.3f})"]
            try:
                lines.append(f"Mass (API7 TopPart.Mass): {d3.TopPart.Mass:.6g} "
                             "(units per document MCX settings, usually grams)")
            except Exception as e:
                lines.append(f"Mass: unavailable ({format_error(e)})")
            try:
                # NB: EntityCollection(115) is NOT a body count - it returns a
                # constant 7 (the default coordinate-system entities) even for an
                # empty part. The real count is BodyCollection().
                lines.append(f"Faces: {p5.EntityCollection(6).GetCount()}, "
                             f"edges: {p5.EntityCollection(7).GetCount()}, "
                             f"bodies: {p5.BodyCollection().GetCount()}")
            except Exception:
                pass
            if doc7.DocumentType == 5:
                try:
                    lines.append(f"Top-level components: {doc5.PartCollection(True).GetCount()}")
                except Exception:
                    pass
            try:
                lines.append(f"Name/marking/material: {p5.name!r} / {p5.marking!r} / {p5.GetMaterial()!r}")
            except Exception:
                pass
            return "\n".join(lines)
        except Exception as e:
            return f"Failed. {format_error(e)}"


@mcp.tool()
def kompas_run_job(job: str, params: str = "{}") -> str:
    """Run a named job script from jobs/<job>.py with the same COM
    scope as kompas_run_script plus a PARAMS dict parsed from the `params`
    JSON string. List available jobs by passing job='list'."""
    jobs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs")
    if job == "list":
        names = sorted(f[:-3] for f in os.listdir(jobs_dir) if f.endswith(".py"))
        return "Available jobs: " + (", ".join(names) if names else "(none)")
    jpath = os.path.join(jobs_dir, f"{job}.py")
    if not os.path.exists(jpath):
        return f"Job not found: {jpath}"
    import json as _json
    try:
        pdict = _json.loads(params or "{}")
    except Exception as e:
        return f"Bad params JSON: {e}"
    with open(jpath, encoding="utf-8") as f:
        code = f.read()
    with com_context():
        buf = io.StringIO()
        try:
            app7 = get_app7()
            try:
                app5 = get_app5()
            except Exception:
                app5 = None
            ns = {"app7": app7, "app5": app5, "Dispatch": Dispatch,
                  "win32com": win32com, "pythoncom": pythoncom,
                  "DOC_TYPES": DOC_TYPES, "PARAMS": pdict,
                  # exec() gives the job no __file__, so a job cannot find its
                  # siblings. Both are injected so jobs can share code:
                  #   sys.path.insert(0, JOBS_DIR); import _shapes
                  "__file__": jpath, "JOBS_DIR": jobs_dir}
            with redirect_stdout(buf):
                exec(compile(code, jpath, "exec"), ns)
            out = buf.getvalue()
            return out.rstrip() if out.strip() else "OK (no output)"
        except Exception:
            out = buf.getvalue()
            tb = traceback.format_exc()
            return (out + "\n" if out.strip() else "") + f"Job error:\n{tb}"



# ---------------------------------------------------------------------------
# Views and screenshots
# ---------------------------------------------------------------------------

# Projection names as they actually appear in KOMPAS v23's
# GetViewProjectionCollection() (verified at runtime, do not guess).
VIEW_ALIASES = {
    "iso": "#Изометрия", "изометрия": "#Изометрия",
    "dim": "#Диметрия", "диметрия": "#Диметрия",
    "front": "#Спереди", "спереди": "#Спереди",
    "back": "#Сзади", "сзади": "#Сзади",
    "top": "#Сверху", "сверху": "#Сверху",
    "bottom": "#Снизу", "снизу": "#Снизу",
    "left": "#Слева", "слева": "#Слева",
    "right": "#Справа", "справа": "#Справа",
    "current": "",  # leave the projection alone
}

# Upper bound on the pixel area of one kompas_view sheet (~900 tokens to read).
MAX_VIEW_PX = 700_000


def _kompas_windows():
    """(shell, view) HWNDs of the running KOMPAS.

    v23 hosts everything in a WPF shell window; the legacy `Afx:` MDI frame
    stays hidden and `app5.ksGetHWindow` is an int PROPERTY (not a method)
    pointing at a helper window that is 160x28 while KOMPAS is minimized —
    capturing that is the long-standing "screenshot 160x28" bug. The real 3D
    viewport is a child window of class `D3View`.
    """
    import win32gui
    cands = []

    def cb(h, _):
        try:
            if "КОМПАС" in win32gui.GetWindowText(h):
                cands.append((h, win32gui.GetClassName(h)))
        except Exception:
            pass
    win32gui.EnumWindows(cb, None)
    shell = next((h for h, c in cands if c.startswith("HwndWrapper")), 0)
    if not shell:
        shell = next((h for h, c in cands if win32gui.IsWindowVisible(h)), 0)
    if not shell:
        return 0, 0
    view = [0]

    def find(h):
        def cb2(c, _):
            if view[0]:
                return
            if win32gui.GetClassName(c) == "D3View" and win32gui.IsWindowVisible(c):
                view[0] = c
                return
            find(c)
        try:
            win32gui.EnumChildWindows(h, cb2, None)
        except Exception:
            pass
    find(shell)
    return shell, (view[0] or shell)


def _restore(shell) -> bool:
    """Un-minimize KOMPAS if needed. PrintWindow works on a background window,
    so focus is deliberately NOT stolen from whatever the user is doing."""
    import ctypes
    import win32gui
    if shell and ctypes.windll.user32.IsIconic(shell):
        win32gui.ShowWindow(shell, 9)  # SW_RESTORE
        time.sleep(2.0)          # a half-drawn first frame breaks the auto-crop
        return True
    return False


def _grab(hwnd):
    """Capture a window (including OpenGL content) into a PIL RGB image."""
    import ctypes
    import win32gui
    import win32ui
    from PIL import Image
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise RuntimeError(f"window {hwnd} has zero size")
    dc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(dc)
    sdc = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    sdc.SelectObject(bmp)
    try:
        ctypes.windll.user32.PrintWindow(hwnd, sdc.GetSafeHdc(), 2)  # PW_RENDERFULLCONTENT
        bits = bmp.GetBitmapBits(True)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        sdc.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)
    return Image.frombuffer("RGB", (w, h), bits, "raw", "BGRX", 0, 1)


def _crop_panels(img):
    """Drop the model tree / property panels that KOMPAS paints inside the
    D3View window. The 3D background is a smooth vertical gradient, so the
    topmost row of the true viewport is a run of one colour; panels break it.
    Scans right-to-left along that row and cuts where the run ends."""
    import numpy as np
    a = np.asarray(img, dtype=np.int16)
    h, w = a.shape[:2]
    if h < 40 or w < 40:
        return img
    # Several probe rows, not one: a tooltip, a progress strip or a half-drawn
    # panel right after the window is restored can spoil any single row, and a
    # spoiled row silently returns the uncropped frame.
    votes = []
    for y in (1, 3, 8, 15, 25):
        if y >= h:
            break
        row = a[y]
        ref = row[w - 3]
        same = (np.abs(row - ref).max(axis=1) <= 12)
        x0 = w - 3
        while x0 > 0 and same[x0 - 1]:
            x0 -= 1
        x1 = w - 1
        while x1 > x0 and not same[x1]:   # a panel docked on the right
            x1 -= 1
        if x1 - x0 >= w // 4:
            votes.append((x0, x1))
    if not votes:                          # nothing plausible: keep the frame
        return img
    x0 = sorted(v[0] for v in votes)[len(votes) // 2]
    x1 = sorted(v[1] for v in votes)[len(votes) // 2]
    return img.crop((min(x0 + 2, w - 1), 0, x1 + 1, h))


def _crop_to_model(img, margin=0.04):
    """Trim empty background around the model. Background is a vertical
    gradient, so each row's own leftmost pixel is that row's reference colour.
    The axis gizmo in the bottom-left corner is excluded, otherwise it would
    anchor the crop to that corner."""
    import numpy as np
    a = np.asarray(img, dtype=np.int16)
    h, w = a.shape[:2]
    if h < 40 or w < 40:
        return img
    ref = a[:, :1, :]
    mask = np.abs(a - ref).max(axis=2) > 16
    mask[int(h * 0.86):, :int(w * 0.10)] = False   # axis gizmo
    ys, xs = np.nonzero(mask)
    if ys.size < 50:
        return img
    my, mx = int(h * margin), int(w * margin)
    return img.crop((max(0, int(xs.min()) - mx), max(0, int(ys.min()) - my),
                     min(w, int(xs.max()) + 1 + mx), min(h, int(ys.max()) + 1 + my)))


@mcp.tool()
def kompas_view(views: str = "iso", fit: bool = True, width: int = 900,
                unselect: bool = True, crop: bool = True, path: str = "") -> str:
    """Set one or more standard projections of the active 3D document and
    capture them into a SINGLE compact PNG. Replaces the old pair
    "run a script to change the projection, then kompas_screenshot".

    views  - comma-separated: iso, dim, front, back, top, bottom, left, right,
             current (keeps whatever is on screen). Russian names also work.
             1 view => one column, 2+ => two columns.
    fit    - zoom-to-fit before each capture.
    width  - width of the FINAL image in pixels (default 900). The cost of
             reading the image scales with its area, so keep this small.
    crop   - trim the docked tree/property panels and the empty background
             around the model. Turn off to inspect the KOMPAS UI itself.
    """
    from PIL import Image
    if not path:
        path = os.path.join(tempfile.gettempdir(), "kompas_view.png")
    path = os.path.abspath(path)
    names = [v.strip().lower() for v in views.split(",") if v.strip()]
    if not names:
        names = ["iso"]
    unknown = [v for v in names if v not in VIEW_ALIASES]
    if unknown:
        return (f"Failed: unknown view(s) {unknown}. "
                f"Use: {', '.join(sorted(k for k in VIEW_ALIASES if k.isascii()))}")

    with com_context():
        try:
            kompas, app7 = get_typed_api()
            doc7, d3 = _active_3d(kompas, app7)
            doc5 = kompas.ActiveDocument3D()
            if unselect:
                try:
                    d3.SelectionManager.UnselectAll()
                except Exception:
                    pass
            shell, viewwnd = _kompas_windows()
            if not shell:
                return "Failed: KOMPAS window not found."
            shots, notes = [], []

            # Un-minimizing makes KOMPAS activate one of its MDI children,
            # which can silently switch the active document — you would then
            # set projections on one assembly and photograph another.
            want = doc7.PathName
            restored = _restore(shell)
            if restored and want:
                cur = app7.ActiveDocument
                if cur is None or cur.PathName != want:
                    for i in range(app7.Documents.Count):
                        try:
                            d = app7.Documents.Item(i)
                            if d.PathName == want:
                                d.Active = True
                                time.sleep(0.6)
                                break
                        except Exception:
                            pass
                    doc7, d3 = _active_3d(kompas, app7)
                    doc5 = kompas.ActiveDocument3D()
                    notes.append("window restore had switched the active document; "
                                 "switched back before capturing")
                    shell, viewwnd = _kompas_windows()

            col = doc5.GetViewProjectionCollection()
            for v in names:
                proj = VIEW_ALIASES[v]
                if proj:
                    p = col.GetByName(proj, True, True)
                    if p is None:
                        notes.append(f"projection {proj} not found, kept current")
                    else:
                        p.SetCurrent()
                if fit:
                    doc5.ZoomPrevNextOrAll(2)   # 2 = zoom to fit
                time.sleep(0.45)                # let the viewport redraw
                img = _grab(viewwnd)
                if crop:
                    img = _crop_to_model(_crop_panels(img))
                shots.append((v, img))

            # Layout follows the model's own proportions: wide models stack
            # vertically, tall ones sit side by side, so little of the sheet
            # is wasted on letterboxing.
            aspects = sorted(im.width / im.height for _, im in shots)
            med = min(2.2, max(0.6, aspects[len(aspects) // 2]))
            n = len(shots)
            if n == 1:
                cols = 1
            elif n == 2:
                cols = 1 if med >= 1.15 else 2
            else:
                cols = 2
            rows = (n + cols - 1) // cols
            cw = max(160, width // cols)
            ch = max(120, int(round(cw / med)))
            sheet = Image.new("RGB", (cw * cols, ch * rows), (255, 255, 255))
            for i, (v, im) in enumerate(shots):
                sc = min(cw / im.width, ch / im.height)
                nw, nh = max(1, int(im.width * sc)), max(1, int(im.height * sc))
                sheet.paste(im.resize((nw, nh), Image.LANCZOS),
                            ((i % cols) * cw + (cw - nw) // 2,
                             (i // cols) * ch + (ch - nh) // 2))
            # Hard ceiling on area: reading an image costs roughly area/750
            # tokens, so an unlucky aspect ratio must not blow the budget.
            area = sheet.width * sheet.height
            if area > MAX_VIEW_PX:
                k = (MAX_VIEW_PX / area) ** 0.5
                sheet = sheet.resize((max(1, int(sheet.width * k)),
                                      max(1, int(sheet.height * k))), Image.LANCZOS)
            sheet.save(path, optimize=True)

            out = [f"Document: {doc7.PathName or doc7.Name}",
                   f"Saved: {path} ({sheet.width}x{sheet.height}, "
                   f"{os.path.getsize(path)} bytes)",
                   f"Views: {', '.join(v for v, _ in shots)} "
                   f"| source {shots[0][1].width}x{shots[0][1].height} px after crop"]
            if restored:
                out.append("KOMPAS window was minimized and has been restored.")
            out += notes
            return "\n".join(out)
        except Exception as e:
            return f"Failed to capture views. {format_error(e)}"


# KOMPAS shows a modal "НАСТРОЙКА ЗАПИСИ В ФОРМАТ PNG" dialog on every
# raster SaveAs of a 3D document and IApplication.HideMessage does not
# suppress it (verified 2026-09-22: the call blocked for 188 s until the
# user clicked). The watcher below confirms that dialog on the user's behalf.
RASTER_DIALOG_MARKERS = ("ФОРМАТ", "PNG", "JPG", "BMP", "TIF")
RASTER_DIALOG_BUTTONS = ("Экспортировать", "Сохранить", "OK", "ОК")


def _kompas_pid():
    import win32process
    shell, _ = _kompas_windows()
    if not shell:
        return 0
    return win32process.GetWindowThreadProcessId(shell)[1]


def _find_dialogs(pid):
    """Visible top-level windows of the KOMPAS process that look like dialogs
    (have a caption, are not the main shell and not a document view)."""
    import win32gui
    import win32process
    out = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        if win32process.GetWindowThreadProcessId(h)[1] != pid:
            return
        title = win32gui.GetWindowText(h)
        if not title or "КОМПАС-3D" in title:
            return
        style = win32gui.GetWindowLong(h, -16)  # GWL_STYLE
        if style & 0x00C00000:                  # WS_CAPTION
            out.append(h)
    win32gui.EnumWindows(cb, None)
    return out


def _press_dialog(dlg, buttons):
    """Press the first child button whose caption is in `buttons`; for WPF
    dialogs without Win32 buttons send Enter (default button). Returns a note."""
    import win32con
    import win32gui
    btns = []

    def cb2(c, _):
        if win32gui.GetWindowText(c) in buttons:
            btns.append(c)
    try:
        win32gui.EnumChildWindows(dlg, cb2, None)
    except Exception:
        pass
    title = win32gui.GetWindowText(dlg)
    if btns:
        win32gui.PostMessage(btns[0], win32con.BM_CLICK, 0, 0)
        return f"'{title}': нажата кнопка «{win32gui.GetWindowText(btns[0])}»"
    win32gui.PostMessage(dlg, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    win32gui.PostMessage(dlg, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    return f"'{title}': подтверждено клавишей Enter (кнопка по умолчанию)"


# Background auto-answer of KOMPAS pop-ups (enabled by kompas_auto_dialogs).
AUTO_DIALOG = {"on": False, "buttons": ("Экспортировать", "ОК", "OK", "Да", "Сохранить"),
               "log": [], "thread": None}


def _auto_dialog_loop():
    import win32gui
    seen = {}
    while AUTO_DIALOG["on"]:
        try:
            pid = _kompas_pid()
            if pid:
                for dlg in _find_dialogs(pid):
                    now = time.time()
                    if now - seen.get(dlg, 0) < 2.0:
                        continue           # give the click time to land
                    seen[dlg] = now
                    note = _press_dialog(dlg, AUTO_DIALOG["buttons"])
                    AUTO_DIALOG["log"].append(time.strftime("%H:%M:%S ") + note)
                    del AUTO_DIALOG["log"][:-50]
        except Exception as e:
            AUTO_DIALOG["log"].append(f"watcher error: {e}")
        time.sleep(0.25)


@mcp.tool()
def kompas_auto_dialogs(enable: bool = True, buttons: str = "") -> str:
    """Enable/disable automatic answering of KOMPAS pop-up dialogs (export
    settings, rebuild prompts, plain OK messages) on the user's behalf.

    While enabled, a background watcher presses the first button from the
    allowed list found in any visible KOMPAS dialog; WPF dialogs without
    Win32 buttons get Enter (their default button). Cancel/No buttons are
    never pressed. buttons - comma-separated captions to allow; default
    "Экспортировать,ОК,OK,Да,Сохранить". Returns the recent action log.
    Use kompas_auto_dialogs(enable=False) to stop.
    """
    import threading
    if buttons.strip():
        AUTO_DIALOG["buttons"] = tuple(b.strip() for b in buttons.split(",") if b.strip())
    if enable and not AUTO_DIALOG["on"]:
        AUTO_DIALOG["on"] = True
        th = threading.Thread(target=_auto_dialog_loop, daemon=True)
        th.start()
        AUTO_DIALOG["thread"] = th
    elif not enable:
        AUTO_DIALOG["on"] = False
    state = "включён" if AUTO_DIALOG["on"] else "выключен"
    log = AUTO_DIALOG["log"][-10:]
    return (f"Автоответ на диалоги КОМПАС: {state}; кнопки: {', '.join(AUTO_DIALOG['buttons'])}\n"
            + ("\n".join(log) if log else "пока ничего не нажималось"))


def _confirm_raster_dialog(stop, timeout=120.0):
    """Poll for the raster-export dialog and press its confirm button.

    Runs in a helper thread while SaveAs blocks the COM thread. Only the
    export dialog is touched: the window title must contain 'ФОРМАТ' and a
    raster format name, and only a button with a known caption is pressed.
    Returns what was done for the tool report.
    """
    import win32con
    import win32gui
    t0 = time.time()
    while not stop.is_set() and time.time() - t0 < timeout:
        hits = []

        def cb(h, _):
            if not win32gui.IsWindowVisible(h):
                return
            title = win32gui.GetWindowText(h).upper()
            if "ФОРМАТ" in title and any(m in title for m in RASTER_DIALOG_MARKERS[1:]):
                hits.append(h)
        win32gui.EnumWindows(cb, None)
        if hits:
            dlg = hits[0]
            btns = []

            def cb2(c, _):
                if win32gui.GetWindowText(c) in RASTER_DIALOG_BUTTONS:
                    btns.append(c)
            try:
                win32gui.EnumChildWindows(dlg, cb2, None)
            except Exception:
                pass
            if btns:
                win32gui.PostMessage(btns[0], win32con.BM_CLICK, 0, 0)
                return f"dialog '{win32gui.GetWindowText(dlg)}' confirmed via button"
            # WPF dialogs expose no Win32 buttons: accept the default button.
            win32gui.PostMessage(dlg, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
            win32gui.PostMessage(dlg, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
            return f"dialog '{win32gui.GetWindowText(dlg)}' confirmed via Enter"
        time.sleep(0.15)
    return "no export dialog appeared"


@mcp.tool()
def kompas_export_raster(path: str, view: str = "current", fit: bool = True,
                         scale: float = 3.0, resolution: int = 300) -> str:
    """Export the active 3D document to a clean raster (PNG/JPG/BMP) via
    KOMPAS SaveAs: white background, no panels, ~3000 px on the long side.
    Confirms the KOMPAS export-settings dialog automatically, so the call
    does not wait for a click. Use this instead of kompas_view for images
    that go into reports and presentations.

    path       - output file with .png/.jpg/.bmp extension (absolute).
    view       - iso, dim, front, back, top, bottom, left, right, current.
    fit        - zoom-to-fit before export.
    scale      - KOMPAS raster scale (3.0 => ~3 px per screen px).
    resolution - dpi passed to KOMPAS (300).
    """
    import threading
    path = os.path.abspath(path)
    v = view.strip().lower()
    if v not in VIEW_ALIASES:
        return (f"Failed: unknown view '{view}'. "
                f"Use: {', '.join(sorted(k for k in VIEW_ALIASES if k.isascii()))}")
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            doc7, d3 = _active_3d(kompas, app7)
            doc5 = kompas.ActiveDocument3D()
            proj = VIEW_ALIASES[v]
            if proj:
                p = doc5.GetViewProjectionCollection().GetByName(proj, True, True)
                if p is not None:
                    p.SetCurrent()
            if fit:
                doc5.ZoomPrevNextOrAll(2)
            rp = doc5.RasterFormatParam()
            rp.Init()
            rp.extScale = float(scale)
            rp.extResolution = int(resolution)
            stop = threading.Event()
            result = {}

            def watch():
                result["dialog"] = _confirm_raster_dialog(stop)
            th = threading.Thread(target=watch, daemon=True)
            th.start()
            t0 = time.time()
            try:
                ok = doc5.SaveAs(path)
            finally:
                stop.set()
                th.join(2)
            dt = time.time() - t0
            if not ok or not os.path.exists(path):
                return f"Failed: SaveAs returned {ok}; {result.get('dialog', '')}"
            size = os.path.getsize(path)
            dims = ""
            try:
                from PIL import Image
                with Image.open(path) as im:
                    dims = f"{im.width}x{im.height}, "
            except Exception:
                pass
            return (f"Document: {doc7.PathName or doc7.Name}\n"
                    f"Saved: {path} ({dims}{size} bytes) in {dt:.1f} s\n"
                    f"View: {v} | {result.get('dialog', 'watcher did not report')}")
        except Exception as e:
            return f"Failed to export raster. {format_error(e)}"


# ---------------------------------------------------------------------------
# Assembly editing
# ---------------------------------------------------------------------------

INSERT_BATCH = 50          # >50 inserts per rebuild once killed the MCP call
PLACE_TOL = 0.01           # mm; deviation above this is reported as a failure

# Named orientations: which world axis the component's own axis is turned to.
# Row-vector convention, entries 0..11 are the X, Y and Z axis rows.
ORIENTATIONS = {
    "": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0],
    "x->z": [0, 0, 1, 0, 0, 1, 0, 0, -1, 0, 0, 0],
    "z->x": [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0],
    "y->z": [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0],
    "z->y": [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0],
    "x->y": [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0],
    "y->x": [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0],
    "flip": [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0],   # 180 deg about X
}


def _norm_path(p: str, long=None) -> str:
    """Путь для сравнения: «/» → обратный слэш, normpath, без учёта регистра; long — функция
    8.3 → длинное имя (_long_path), КОМПАС отдаёт PathName длинным."""
    p = os.path.normpath((p or "").strip().replace("/", "\\"))
    return (long(p) if long else p).lower()


def _activate(app7, target: str) -> str:
    """Сделать документ `target` активным. Возвращает "" при успехе или текст
    ошибки. Совпадение: тот же путь (_norm_path: слэши, регистр, 8.3), иначе
    подстрока имени (без учёта регистра).

    Зачем: КОМПАС один на всех, а активный документ переключается сам —
    при открытии/закрытии документов и при разворачивании окна. Инструмент,
    который правит «активный документ», в параллельной работе может уехать
    не в тот файл. Явный адрес это исключает."""
    if not target:
        return ""
    t = target.strip().lower()
    tn = _norm_path(target, _long_path) if re.search(r"[\\/]", target) else None
    hits = []
    try:
        for i in range(app7.Documents.Count):
            try:
                d = app7.Documents.Item(i)
                if d is None:
                    continue
                path = (d.PathName or "")
                if not path and not d.Name:
                    continue          # фантом невидимого документа
                if path.lower() == t or (tn and path and _norm_path(path) == tn):
                    hits = [(d, path)]
                    break
                if t in path.lower() or t in (d.Name or "").lower():
                    hits.append((d, path or d.Name))
            except Exception:
                pass
    except Exception as e:
        return f"Failed: не удалось перебрать документы. {format_error(e)}"
    if not hits:
        return (f"Failed: документ {target!r} не открыт в КОМПАС. Открой его "
                "(kompas_open_document) или убери параметр document.")
    if len(hits) > 1:
        names = ", ".join(h[1] for h in hits[:5])
        return f"Failed: под {target!r} подходит несколько документов: {names}. Уточни."
    try:
        hits[0][0].Active = True
    except Exception as e:
        return f"Failed: не удалось активировать {hits[0][1]}. {format_error(e)}"
    return ""


@contextmanager
def _active_guard(app7):
    """KOMPAS re-activates a neighbouring document on its own when documents
    are created/closed or the main window is restored. Snapshot the active
    document and put it back, so a tool never leaves the user looking at a
    different assembly than the one they were working on."""
    try:
        cur = app7.ActiveDocument
        want = cur.PathName if cur is not None else ""
    except Exception:
        want = ""
    try:
        yield
    finally:
        if want:
            try:
                a = app7.ActiveDocument
                if a is None or a.PathName != want:
                    for i in range(app7.Documents.Count):
                        try:
                            d = app7.Documents.Item(i)
                            if d is not None and d.PathName == want:
                                d.Active = True
                                break
                        except Exception:
                            pass
            except Exception:
                pass


def _norm_name(s):
    """Cyrillic/Latin lookalikes make name matching fail silently."""
    return (s or "").lower().replace("x", "х").replace("m", "м").replace("a", "а")


def _build_matrix(item):
    """Placement matrix for one insert/move spec."""
    if item.get("matrix"):
        m = [float(x) for x in item["matrix"]]
        if len(m) != 16:
            raise ValueError(f"matrix must have 16 numbers, got {len(m)}")
        return m
    rot = str(item.get("axis", "") or "").strip().lower()
    if rot not in ORIENTATIONS:
        raise ValueError(f"unknown axis {rot!r}; use one of {sorted(k for k in ORIENTATIONS if k)}")
    org = item.get("origin", [0.0, 0.0, 0.0])
    org = [float(x) for x in org]
    if len(org) != 3:
        raise ValueError(f"origin must have 3 numbers, got {len(org)}")
    return list(ORIENTATIONS[rot]) + org + [1.0]


def _place(p5, mat):
    """Move a component. Only API5 SetPlacement actually moves geometry;
    the API7 Placement property changes the value and leaves the model put."""
    pl = p5.GetPlacement()
    pl.InitByMatrix3D(mat)
    p5.SetPlacement(pl)
    p5.UpdatePlacement()


def _origin_of(p5):
    return _m16(p5.GetPlacement().GetMatrix3D())[12:15]


@mcp.tool()
def kompas_insert(components: str, rebuild: bool = True, document: str = "") -> str:
    """Insert components into the active ASSEMBLY as external references and
    place them, in one call. Replaces the hand-written insert script.

    components: JSON list. Each item needs `file` plus either
      {"file": "...m3d", "origin": [x, y, z], "axis": "x->z"}   (axis optional)
      {"file": "...m3d", "matrix": [16 numbers]}                (full control)
    `axis` names which world axis the component's own axis turns to:
    x->z, z->x, y->z, z->y, x->y, y->x, flip.

    Always inserts with ExternalFile=True (embedding bloats the .a3d), splits
    into batches of 50 (larger batches time the call out), and verifies every
    placement by reading the matrix back. Deviations are reported as errors.
    """
    try:
        items = json.loads(components)
    except Exception as e:
        return f"Failed: components is not valid JSON. {e}"
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        return "Failed: components must be a non-empty JSON list."

    specs = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not it.get("file"):
            return f"Failed: item {i} has no `file`."
        f = os.path.abspath(it["file"])
        if not os.path.exists(f):
            return f"Failed: item {i}, file not found: {f}"
        try:
            specs.append((f, _build_matrix(it)))
        except Exception as e:
            return f"Failed: item {i}: {e}"

    with com_context():
        try:
            kompas, app7 = get_typed_api()
            err = _activate(app7, document)
            if err:
                return err
            with _active_guard(app7):
                doc7, d3 = _active_3d(kompas, app7)
                if doc7.DocumentType != 5:
                    return ("Failed: the active document is a part, not an assembly. "
                            "Open the assembly first.")
                doc5 = kompas.ActiveDocument3D()
                top = d3.TopPart

                placed, problems = [], []
                for start in range(0, len(specs), INSERT_BATCH):
                    batch = specs[start:start + INSERT_BATCH]
                    made = []
                    for f, mat in batch:
                        top.Parts.AddFromFile(f, True, False)   # ExternalFile=True
                        plc = doc5.PartCollection(True)
                        new5 = plc.GetByIndex(plc.GetCount() - 1)
                        got = os.path.normcase(os.path.abspath(new5.fileName or ""))
                        if got != os.path.normcase(f):
                            problems.append(f"{os.path.basename(f)}: inserted component could not "
                                            f"be identified (last in collection is {new5.name!r}) "
                                            "- left at origin, place it manually")
                            continue
                        _place(new5, mat)
                        made.append((new5, f, mat))
                    if rebuild:
                        doc5.RebuildDocument()
                    for new5, f, mat in made:
                        try:
                            act = _origin_of(new5)
                        except Exception as e:
                            problems.append(f"{os.path.basename(f)}: placement unreadable ({e})")
                            continue
                        want = mat[12:15]
                        dev = max(abs(a - b) for a, b in zip(act, want))
                        placed.append((os.path.basename(f), want, act, dev))
                        if dev > PLACE_TOL:
                            problems.append(
                                f"{os.path.basename(f)}: asked for "
                                f"({want[0]:.2f}, {want[1]:.2f}, {want[2]:.2f}) but sits at "
                                f"({act[0]:.2f}, {act[1]:.2f}, {act[2]:.2f}), off by {dev:.3f} mm")

                lines = [f"Document: {doc7.PathName or doc7.Name}",
                         f"Inserted {len(placed)} of {len(specs)} components "
                         f"(external references, {(len(specs) - 1) // INSERT_BATCH + 1} batch(es))"]
                for name, want, act, dev in placed[:60]:
                    lines.append(f"  {name}: origin ({act[0]:.2f}, {act[1]:.2f}, {act[2]:.2f}) "
                                 f"[dev {dev:.3f}]")
                if len(placed) > 60:
                    lines.append(f"  ... and {len(placed) - 60} more")
                if problems:
                    lines.append("PROBLEMS:")
                    lines += [f"  {p}" for p in problems]
                else:
                    lines.append("All placements verified against the requested matrices.")
                lines.append("Assembly is NOT saved - check it, then save.")
                return "\n".join(lines)
        except Exception as e:
            return f"Failed to insert. {format_error(e)}"


@mcp.tool()
def kompas_move(match: str, origin: str = "", delta: str = "", matrix: str = "",
                axis: str = "", which: str = "one", dry_run: bool = False,
                document: str = "") -> str:
    """Move top-level components of the active assembly and verify the result.

    match  - substring of the component name (case-insensitive, tolerates
             Cyrillic/Latin lookalikes x/х, m/м, a/а).
    origin - "x,y,z" absolute position;  delta - "dx,dy,dz" relative shift;
    matrix - 16 comma-separated numbers (full placement);
    axis   - reorientation name (see kompas_insert), applied with origin.
    which  - "one" (fail if the name is ambiguous), "all", or a 0-based index.
    dry_run- only list what matches and where it currently sits.

    Moves through API5 SetPlacement, the only route that actually moves
    geometry, then reads the matrix back to confirm.
    """
    def nums(s, n, what):
        v = [float(x) for x in str(s).replace(";", ",").split(",") if x.strip()]
        if len(v) != n:
            raise ValueError(f"{what} needs {n} numbers, got {len(v)}")
        return v

    given = sum(1 for v in (origin, delta, matrix) if str(v).strip())
    if not dry_run and given != 1:
        return ("Failed: give exactly one of origin=, delta= or matrix= "
                "(or dry_run=True to just look).")

    with com_context():
        try:
            kompas, app7 = get_typed_api()
            err = _activate(app7, document)
            if err:
                return err
            with _active_guard(app7):
                doc7, d3 = _active_3d(kompas, app7)
                if doc7.DocumentType != 5:
                    return "Failed: the active document is not an assembly."
                doc5 = kompas.ActiveDocument3D()
                plc = doc5.PartCollection(True)
                needle = _norm_name(match)
                hits = []
                for i in range(plc.GetCount()):
                    try:
                        p = plc.GetByIndex(i)
                        if needle in _norm_name(p.name):
                            hits.append((i, p, _origin_of(p)))
                    except Exception:
                        pass
                if not hits:
                    return f"Failed: no top-level component matching {match!r}."

                listing = [f"{k}: {p.name} at ({o[0]:.2f}, {o[1]:.2f}, {o[2]:.2f})"
                           for k, (i, p, o) in enumerate(hits)]
                if dry_run:
                    return (f"Document: {doc7.PathName or doc7.Name}\n"
                            f"{len(hits)} match(es):\n  " + "\n  ".join(listing))
                if which == "all":
                    targets = hits
                elif which == "one":
                    if len(hits) > 1:
                        return ("Failed: the name is ambiguous, pass which=<index> or "
                                "which=all.\n  " + "\n  ".join(listing))
                    targets = hits
                else:
                    try:
                        targets = [hits[int(which)]]
                    except Exception:
                        return (f"Failed: which={which!r} is not a valid index.\n  "
                                + "\n  ".join(listing))

                moved = []
                for _, p, before in targets:
                    if matrix:
                        mat = nums(matrix, 16, "matrix")
                    else:
                        cur = _m16(p.GetPlacement().GetMatrix3D())
                        if delta:
                            d = nums(delta, 3, "delta")
                            org = [cur[12] + d[0], cur[13] + d[1], cur[14] + d[2]]
                        else:
                            org = nums(origin, 3, "origin")
                        rot = (list(ORIENTATIONS[axis.strip().lower()])
                               if axis.strip() else cur[:12])
                        mat = rot + org + [1.0]
                    _place(p, mat)
                    moved.append((p, before, mat[12:15]))
                doc5.RebuildDocument()

                lines = [f"Document: {doc7.PathName or doc7.Name}"]
                bad = 0
                for p, before, want in moved:
                    act = _origin_of(p)
                    dev = max(abs(a - b) for a, b in zip(act, want))
                    flag = "" if dev <= PLACE_TOL else f"  <-- OFF BY {dev:.3f} mm"
                    bad += dev > PLACE_TOL
                    lines.append(f"{p.name}: ({before[0]:.2f}, {before[1]:.2f}, {before[2]:.2f}) "
                                 f"-> ({act[0]:.2f}, {act[1]:.2f}, {act[2]:.2f}){flag}")
                lines.append("All moves verified." if not bad
                             else f"{bad} component(s) did NOT reach the requested position.")
                lines.append("Assembly is NOT saved - check it, then save.")
                return "\n".join(lines)
        except Exception as e:
            return f"Failed to move. {format_error(e)}"


@mcp.tool()
def kompas_part_props(name: str = "", marking: str = "", material: str = "",
                      density: float = 0.0, color: str = "", document: str = "") -> str:
    """Set name / marking (обозначение) / material / colour of the active PART
    document in one call, then report the resulting mass and gabarit.

    density - g/cm3, required together with material.
    color   - "r,g,b" 0..255.

    Refuses to run on an assembly: SetMaterial in an assembly context has hung
    KOMPAS before, and a colour set in a part file is not inherited by its
    instances anyway - colour those with kompas_run_script per instance.
    """
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            err = _activate(app7, document)
            if err:
                return err
            doc7, d3 = _active_3d(kompas, app7)
            if doc7.DocumentType != 4:
                return ("Failed: the active document is an assembly. Open the part file "
                        "itself - SetMaterial can hang when called on an assembly.")
            doc5 = kompas.ActiveDocument3D()
            p5 = doc5.GetPart(-1)
            done = []
            if name:
                p5.name = name
                done.append(f"name={name!r}")
            if marking:
                p5.marking = marking
                done.append(f"marking={marking!r}")
            if material:
                if density <= 0:
                    return "Failed: material needs density (g/cm3), e.g. 7.92 for 12Х18Н10Т."
                p5.SetMaterial(material, float(density))
                done.append(f"material={material!r} ({density} g/cm3)")
            if color:
                try:
                    r, g, b = [int(x) for x in color.replace(";", ",").split(",")]
                except Exception:
                    return "Failed: color must be \"r,g,b\" with values 0..255."
                p5.SetAdvancedColor((b << 16) | (g << 8) | r, 0.5, 0.7, 0.8, 0.8, 1.0, 0.5)
                done.append(f"color=({r},{g},{b})")
            if not done:
                return "Nothing to do - pass at least one of name/marking/material/color."
            p5.Update()
            doc5.RebuildDocument()

            ok, x1, y1, z1, x2, y2, z2 = p5.GetGabarit(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            lines = [f"Document: {doc7.PathName or doc7.Name}",
                     "Set: " + ", ".join(done),
                     f"Gabarit: {x2 - x1:.3f} x {y2 - y1:.3f} x {z2 - z1:.3f}"]
            try:
                lines.append(f"Mass: {d3.TopPart.Mass:.6g}")
            except Exception:
                pass
            try:
                lines.append(f"Now reads: {p5.name!r} / {p5.marking!r} / {p5.GetMaterial()!r}")
            except Exception:
                pass
            lines.append("Part is NOT saved - check it, then save.")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to set properties. {format_error(e)}"


# ---------------------------------------------------------------------------
# Geometry: sketches and operations without hand-written scripts
# ---------------------------------------------------------------------------

O3D = {"sketch": 5, "plane_xoy": 1, "plane_xoz": 2, "plane_yoz": 3, "plane_offset": 14,
       "base_extrude": 24, "boss_extrude": 25, "cut_extrude": 26, "base_rotated": 27,
       "face": 6, "edge": 7, "body": 115}
PLANES = {"xoy": 1, "xoz": 2, "yoz": 3}
ET_BLIND, ET_THROUGH_ALL = 0, 1


def _draw_profile(w, profile):
    """Draw a sketch from a JSON profile. Returns how many entities were drawn."""
    n = 0
    for i, prim in enumerate(profile):
        if not isinstance(prim, dict) or len(prim) != 1:
            raise ValueError(f"element {i}: expected one key of line/poly/circle/hex/axis")
        (kind, val), = prim.items()
        v = [float(x) for x in val] if kind != "poly" else val
        if kind == "line":
            w.ksLineSeg(v[0], v[1], v[2], v[3], 1)
        elif kind == "axis":
            w.ksLineSeg(v[0], v[1], v[2], v[3], 3)     # style 3 = axis line
        elif kind == "circle":
            w.ksCircle(v[0], v[1], v[2], 1)
        elif kind == "hex":
            r = v[2] / math.sqrt(3.0)                  # v[2] is across-flats
            pts = [(v[0] + r * math.cos(math.radians(a)),
                    v[1] + r * math.sin(math.radians(a))) for a in range(0, 360, 60)]
            for j in range(6):
                w.ksLineSeg(pts[j][0], pts[j][1], pts[(j + 1) % 6][0], pts[(j + 1) % 6][1], 1)
        elif kind == "poly":
            pts = [[float(a) for a in p] for p in val]
            for j in range(len(pts) - 1):
                w.ksLineSeg(pts[j][0], pts[j][1], pts[j + 1][0], pts[j + 1][1], 1)
            if pts[0] != pts[-1]:
                w.ksLineSeg(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], 1)
        else:
            raise ValueError(f"element {i}: unknown kind {kind!r}")
        n += 1
    return n


@mcp.tool()
def kompas_shape(op: str, profile: str, depth: float = 0.0, plane: str = "xoy",
                 offset: float = 0.0, through: bool = False, angle: float = 360.0,
                 reverse: bool = False, document: str = "") -> str:
    """Build one solid operation on the active PART from a sketch given as JSON.
    Replaces the usual hand-written sketch+operation script.

    op      - base | boss | cut | revolve
    profile - JSON list of primitives, sketch coordinates in mm:
              {"line":[x1,y1,x2,y2]} {"poly":[[x,y],...]} {"circle":[xc,yc,r]}
              {"hex":[xc,yc,s]} (s = across flats) {"axis":[x1,y1,x2,y2]} (revolve axis)
    plane   - xoy | xoz | yoz;  offset - signed offset from that plane (mm)
    depth   - extrusion depth;  through=True extrudes through everything
    angle   - revolve angle;    reverse=True extrudes to the other side

    Two traps are handled here so callers cannot hit them:
    a cut always uses directionType=2 with BOTH SetSideParam (a one-sided cut
    silently slices into empty space and reports success), and every operation
    is checked against the face count, so an operation that did nothing is
    reported as an error instead of passing.

    NOTE: in an XOZ sketch the local +Y axis points along GLOBAL -Z. Mirror the
    profile accordingly, or check the gabarit afterwards.
    """
    op = op.strip().lower()
    if op not in ("base", "boss", "cut", "revolve"):
        return "Failed: op must be base, boss, cut or revolve."
    if plane.strip().lower() not in PLANES:
        return f"Failed: plane must be one of {sorted(PLANES)}."
    try:
        prof = json.loads(profile)
        if not isinstance(prof, list) or not prof:
            raise ValueError("profile must be a non-empty JSON list")
    except Exception as e:
        return f"Failed: bad profile. {e}"
    if op in ("base", "boss", "cut") and not through and depth <= 0:
        return "Failed: depth must be > 0 (or pass through=True)."

    with com_context():
        try:
            kompas, app7 = get_typed_api()
            err = _activate(app7, document)
            if err:
                return err
            doc7, d3 = _active_3d(kompas, app7)
            if doc7.DocumentType != 4:
                return ("Failed: the active document is an assembly. Build geometry in "
                        "the part file itself.")
            doc5 = kompas.ActiveDocument3D()
            part = doc5.GetPart(-1)

            base_plane = part.GetDefaultEntity(PLANES[plane.strip().lower()])
            if abs(offset) > 1e-9:
                pl = part.NewEntity(O3D["plane_offset"])
                pd = pl.GetDefinition()
                pd.SetPlane(base_plane)
                pd.direction = bool(offset >= 0)
                pd.offset = abs(float(offset))
                if not pl.Create():
                    return f"Failed: could not build the offset plane at {offset}."
                base_plane = pl

            sk = part.NewEntity(O3D["sketch"])
            sd = sk.GetDefinition()
            sd.SetPlane(base_plane)
            if not sk.Create():
                return "Failed: could not create the sketch."
            w = sd.BeginEdit()
            try:
                drawn = _draw_profile(w, prof)
            finally:
                sd.EndEdit()

            faces0 = part.EntityCollection(O3D["face"]).GetCount()
            bodies0 = part.BodyCollection().GetCount()
            try:
                mass0 = d3.TopPart.Mass
            except Exception:
                mass0 = None

            if op == "revolve":
                e = part.NewEntity(O3D["base_rotated"])
                ed = e.GetDefinition()
                ed.SetSketch(sk)
                ed.SetSideParam(True, float(angle))
            else:
                e = part.NewEntity(O3D[{"base": "base_extrude", "boss": "boss_extrude",
                                        "cut": "cut_extrude"}[op]])
                ed = e.GetDefinition()
                ed.SetSketch(sk)
                et = ET_THROUGH_ALL if through else ET_BLIND
                if op == "cut":
                    # both sides on purpose: the side facing empty space is harmless,
                    # a one-sided cut is the classic "cut into thin air"
                    ed.directionType = 2
                    ed.SetSideParam(True, et, float(depth), 0.0, False)
                    ed.SetSideParam(False, et, float(depth), 0.0, False)
                else:
                    ed.directionType = 1 if reverse else 0
                    ed.SetSideParam(not reverse, et, float(depth), 0.0, False)
            if not e.Create():
                return (f"Failed: the {op} operation did not build. Check that the profile "
                        "is a closed contour without self-intersections, and that a revolve "
                        "has an axis line.")
            doc5.RebuildDocument()

            faces1 = part.EntityCollection(O3D["face"]).GetCount()
            bodies1 = part.BodyCollection().GetCount()
            try:
                mass1 = d3.TopPart.Mass
            except Exception:
                mass1 = None
            changed = (faces1 != faces0) or (mass0 is not None and mass1 is not None
                                             and abs(mass1 - mass0) > 1e-9)
            ok, x1, y1, z1, x2, y2, z2 = part.GetGabarit(0., 0., 0., 0., 0., 0., 0.)
            lines = [f"Document: {doc7.PathName or doc7.Name}",
                     f"{op}: {drawn} sketch element(s) on {plane}"
                     + (f" offset {offset}" if offset else ""),
                     f"Faces {faces0} -> {faces1}, bodies {bodies0} -> {bodies1}"
                     + (f", mass {mass0:.4g} -> {mass1:.4g}" if mass0 is not None and mass1 is not None else ""),
                     f"Gabarit: X {x1:.3f}..{x2:.3f} ({x2-x1:.3f}), "
                     f"Y {y1:.3f}..{y2:.3f} ({y2-y1:.3f}), Z {z1:.3f}..{z2:.3f} ({z2-z1:.3f})"]
            if not changed and op != "base":
                lines.append("ERROR: the operation reported success but changed nothing - "
                             "it cut or added material in empty space. Undo it and check "
                             "the plane, the offset and the direction.")
            lines.append("Part is NOT saved - check it, then save.")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to build the shape. {format_error(e)}"


# ---------------------------------------------------------------------------
# Fastener audit
# ---------------------------------------------------------------------------

FASTENER_WORDS = ("болт", "винт", "гайка", "шайба", "шпилька", "саморез",
                  "bolt", "screw", "nut", "washer", "stud")
_SIZE_RE = re.compile(r"[мm]\s*(\d+(?:[.,]\d+)?)\s*[xх*]\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_DIA_RE = re.compile(r"[мm]\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_BARE_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w.,])")


@mcp.tool()
def kompas_audit_fasteners(max_items: int = 400) -> str:
    """Audit the fasteners of the active assembly: group them, count them, read
    the nominal size out of each name, work out the axis direction and the tip
    of every screw, and flag the traps that bite in practice.

    Read-only. Reports, in particular:
      - two instances sitting at the SAME point (a duplicate that looks fine on
        screen and doubles the count in the BOM),
      - a group whose members do not share one axis direction,
      - names with no readable size.

    What it cannot decide on its own: whether the thread engagement is enough -
    that needs the thickness of the mating part. The tip coordinate is printed
    so the engagement can be checked against the plate.
    """
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            doc7, d3 = _active_3d(kompas, app7)
            if doc7.DocumentType != 5:
                return "Failed: the active document is not an assembly."
            doc5 = kompas.ActiveDocument3D()
            plc = doc5.PartCollection(True)
            n = min(plc.GetCount(), max_items)

            groups, unreadable = {}, 0
            for i in range(n):
                try:
                    p = plc.GetByIndex(i)
                    name = p.name or ""
                    low = name.lower()
                    if not any(word in low for word in FASTENER_WORDS):
                        continue
                    m = _m16(p.GetPlacement().GetMatrix3D())
                    groups.setdefault(name, []).append(m)
                except Exception:
                    unreadable += 1

            if not groups:
                return (f"Document: {doc7.PathName or doc7.Name}\n"
                        f"No fasteners found among {n} top-level components "
                        "(names are matched against болт/винт/гайка/шайба/bolt/screw/nut/washer). "
                        "Fasteners nested inside sub-assemblies are not scanned - open the sub-assembly.")

            lines = [f"Document: {doc7.PathName or doc7.Name}",
                     f"Fastener groups: {len(groups)}, instances: {sum(len(v) for v in groups.values())}",
                     "", "| Наименование | Кол-во | Ø | Длина | Ось | Замечания |",
                     "|---|---|---|---|---|---|"]
            problems = []
            for name in sorted(groups):
                mats = groups[name]
                sm = _SIZE_RE.search(name)
                dm = _DIA_RE.search(name)
                if sm:
                    dia = sm.group(1)
                elif dm:
                    dia = dm.group(1)
                else:
                    # washers are named "Шайба 10 А2" - the size carries no М
                    bare = _BARE_RE.search(name) if any(
                        wd in name.lower() for wd in ("шайба", "washer")) else None
                    dia = bare.group(1) if bare else "?"
                dia = dia.replace(",", ".")
                length = sm.group(2).replace(",", ".") if sm else "-"
                axes = {(round(m[8], 3), round(m[9], 3), round(m[10], 3)) for m in mats}
                if len(axes) > 1:
                    axis_txt = f"{len(axes)} разных"
                else:
                    ax = next(iter(axes))
                    axis_txt = ("Z" if ax == (0.0, 0.0, 1.0) else
                                "-Z" if ax == (0.0, 0.0, -1.0) else
                                "X" if ax == (1.0, 0.0, 0.0) else
                                "Y" if ax == (0.0, 1.0, 0.0) else
                                f"({ax[0]:.2f},{ax[1]:.2f},{ax[2]:.2f})")
                notes = []
                seen = {}
                for m in mats:
                    key = (round(m[12], 2), round(m[13], 2), round(m[14], 2))
                    seen[key] = seen.get(key, 0) + 1
                dups = [k for k, c in seen.items() if c > 1]
                if dups:
                    notes.append(f"ДУБЛИ в {len(dups)} точке(ах)")
                    problems.append(f"{name}: два и более экземпляра в одной точке "
                                    f"{dups[:3]} - в спецификации количество удвоится")
                if len(axes) > 1:
                    notes.append("оси разные")
                    problems.append(f"{name}: экземпляры смотрят в {len(axes)} разных направлений "
                                    "- проверить, что так и задумано")
                if dia == "?":
                    notes.append("размер не читается из имени")
                    problems.append(f"{name}: из имени не вытащить Ø - переименовать в стиле "
                                    "«Болт М10x35 А2» / «Шайба 10 А2», иначе аудит не сверит размеры")
                lines.append(f"| {name} | {len(mats)} | {'М' + dia if dia != '?' else '?'} "
                             f"| {length} | {axis_txt} | {', '.join(notes) or 'ок'} |")

            # tips of the screws, so engagement can be checked against the plate
            lines.append("")
            lines.append("Концы стержней (для проверки зацепления по оси):")
            shown = 0
            for name in sorted(groups):
                sm = _SIZE_RE.search(name)
                if not sm:
                    continue
                L = float(sm.group(2).replace(",", "."))
                for m in groups[name][:3]:
                    ax = (m[8], m[9], m[10])
                    tip = (m[12] - ax[0] * L, m[13] - ax[1] * L, m[14] - ax[2] * L)
                    lines.append(f"  {name}: опора ({m[12]:.1f}, {m[13]:.1f}, {m[14]:.1f}) "
                                 f"-> конец ({tip[0]:.1f}, {tip[1]:.1f}, {tip[2]:.1f})")
                    shown += 1
                if len(groups[name]) > 3:
                    lines.append(f"  {name}: ... ещё {len(groups[name]) - 3} экз. в тех же осях")
            if not shown:
                lines.append("  (ни у одной группы длина не читается из имени)")

            if problems:
                lines += ["", "ТРЕБУЕТ ВНИМАНИЯ:"] + [f"  - {p}" for p in problems]
            else:
                lines += ["", "Дублей и расхождений по осям не найдено."]
            lines.append("")
            lines.append("НЕ проверено автоматически: фактическое зацепление в резьбе (нужна толщина "
                         "ответной детали), цековки под головки, пересечения с другими телами.")
            if unreadable:
                lines.append(f"({unreadable} компонент(ов) не прочитано)")
            if plc.GetCount() > max_items:
                lines.append(f"(просмотрено {n} из {plc.GetCount()} компонентов верхнего уровня - "
                             "поднимите max_items)")
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to audit. {format_error(e)}"


# ---------------------------------------------------------------------------
# Batching: a chain of calls in one round trip
# ---------------------------------------------------------------------------

MAX_BATCH_ACTIONS = 30
MAX_RESULT_CHARS = 1500      # per action, so one chatty step cannot flood the reply

# MCP_TOOL_TIMEOUT is 300 s (client settings). A batch that runs into it
# is killed outright and the caller never learns how far it got — that actually
# happened on 2026-08-26 (a batch measured at 300.8 s). So the batch keeps its
# own, lower budget and stops itself while it can still report.
BATCH_BUDGET_S = 240.0
SLOW_ACTION_S = 60.0         # a single action cannot be interrupted; flag it instead
PROGRESS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "logs", "last_batch.json")


def _write_progress(total: int, done: list, note: str = "") -> None:
    """Snapshot batch progress after every action.

    Survives a hard kill (timeout, KOMPAS crash, process death): the next turn
    reads logs/last_batch.json and resumes instead of replaying actions that
    were already applied. Never raises — bookkeeping must not break the batch."""
    try:
        os.makedirs(os.path.dirname(PROGRESS_PATH), exist_ok=True)
        with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
            json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "total": total,
                       "done": done, "note": note}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# Справка SDK КОМПАС: офлайн-поиск (без COM). Первый путь, что существует; свой — в KOMPAS_SDK_HELP_ZIP.
SDK_HELP_ZIP_CANDIDATES = (
    os.environ.get("KOMPAS_SDK_HELP_ZIP", ""),
    r"C:\Program Files\ASCON\SDK\Help\KOMPAS_SDK_ru-RU.zip",
    r"C:\Program Files\ASCON\KOMPAS-3D v23\SDK\Help\KOMPAS_SDK_ru-RU.zip",
    r"C:\Program Files (x86)\ASCON\SDK\Help\KOMPAS_SDK_ru-RU.zip",
)
SDK_INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "sdk_index.json")
def _face_records(top):
    """Грани детали (API7 IFace/IMathSurface3D) → записи для model_summary.summarize.
    Нормаль грани = нормаль поверхности × (NormalOrientation ? 1 : −1) — наружу из материала.
    Ось цилиндра — по параметру v (Placement поверхности бывает единичной, не ось)."""
    from win32com.client import CastTo
    recs = []
    for o in CastTo(top, "IFeature7").ModelObjects(6) or ():
        f = CastTo(o, "IFace")
        ms = f.MathSurface
        gab = list(ms.GetGabarit()[1:])
        u0, u1, v0, v1 = ms.ParamUMin, ms.ParamUMax, ms.ParamVMin, ms.ParamVMax
        um, vm = (u0 + u1) / 2, (v0 + v1) / 2
        sign = 1.0 if f.NormalOrientation else -1.0
        if f.IsCylinder:
            us = [u0 + (u1 - u0) * t / 3 for t in range(3)]
            recs.append({"t": "cyl", "r": f.Radius, "span": u1 - u0, "gab": gab,
                         "ring0": [list(ms.GetPoint(u, v0)[1:]) for u in us],
                         "ring1": [list(ms.GetPoint(u, v1)[1:]) for u in us],
                         "pt": list(ms.GetPoint(um, vm)[1:]),
                         "nrm": [sign * x for x in ms.GetNormal(um, vm)[1:]]})
        elif f.IsPlanar:
            recs.append({"t": "plane", "gab": gab, "area": f.GetArea(1),   # 1 = ST_MIX_MM
                         "nrm": [sign * x for x in ms.GetNormal(um, vm)[1:]]})
        else:
            recs.append({"t": "cone" if f.IsCone else "other", "gab": gab})
    return recs


def _thread_records(top):
    """Условные резьбы детали (ISymbols3DContainer.Threads) → сырьё для model_summary.threads.
    Без них сводка не видит резьбу вовсе (вал ACME.107.002.004: M76×2 на «Операции без истории»
    — геометрически гладкий цилиндр Ø76; drawing-intent принял его за шейку под ступицу)."""
    from win32com.client import CastTo
    out = []
    th = CastTo(top, "ISymbols3DContainer").Threads
    for i in range(th.Count):
        t = th.Thread(i)
        tp = CastTo(t, "IThreadsParameters")
        rec = {"d": tp.Diameter, "pitch": tp.Pitch, "internal": bool(tp.Internal),
               "left": bool(t.LeftThread), "length": t.Lenght, "std": tp.ThreadStandardFileName or ""}
        try:
            rec["gab"] = list(CastTo(t.BaseObject, "IFace").MathSurface.GetGabarit()[1:])
        except Exception:
            pass
        out.append(rec)
    return out


@mcp.tool()
def kompas_model_summary(path: str, max_items: int = 200) -> str:
    """Сводка геометрии ДЕТАЛИ (.m3d) для планирования чертежа — только чтение, никогда не
    сохраняет. Не открыта — открывает невидимой и закрывает; открыта — берёт её и оставляет.
    JSON: file, props {name, marking, material, mass_g, density}, bbox [x1,y1,z1,x2,y2,z2],
    bodies, faces; holes — группы {d, n, axis (от дна к входу), depth, through, entry "z=9.0",
    centers_top} (центр дна = centers_top − axis·depth; глухое — есть дно/конус сверла Ø≤d);
    bosses {d, n, axis, length, centers_top}; arcs — неполные цилиндры (скругления) {r, n, inner};
    planes {x|y|z: [[уровень, граней, площадь мм²]…], other, capped}; threads — условные резьбы
    модели {designation «M76×2», d, pitch, internal, length, tol_default, axis, span}; notes — Ø сверления →
    резьба по project_rules.json:drill_to_thread (PRJ.02). Координаты — СК модели, как в refs
    drawing_spec ({"type":"points"|"circle"}). max_items — предел числа центров в ответе."""
    import model_summary
    from win32com.client import CastTo
    if not os.path.isfile(path):
        return f"File not found: {path}"
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            import win32api

            def key(p):                   # 8.3-имена (USERNA~1) и регистр — к одному виду
                p = os.path.abspath(p)
                try:
                    p = win32api.GetLongPathName(p)
                except Exception:
                    pass
                return os.path.normcase(p)
            want = key(path)
            doc = None
            for d in iter_documents(app7.Documents):
                try:
                    if d is not None and d.PathName and key(d.PathName) == want:
                        doc = d
                        break
                except Exception:
                    pass
            opened = doc is None
            if opened:
                doc = app7.Documents.Open(path, False, True)
            try:
                if doc.DocumentType != 4:
                    return "Failed: not a part document (.m3d) — kompas_model_summary reads parts only."
                top = CastTo(doc, "IKompasDocument3D").TopPart
                faces = _face_records(top)
                try:
                    rb = CastTo(top, "IFeature7").ResultBodies
                    bodies = len(rb) if isinstance(rb, tuple) else (0 if rb is None else 1)
                except Exception:
                    bodies = None
                s = model_summary.summarize(faces, max_items=max_items,
                                            drill_to_thread=model_summary.load_drill_to_thread())
                out = {"file": doc.PathName,
                       "props": {"name": top.Name, "marking": top.Marking,
                                 "material": top.Material, "mass_g": round(top.Mass, 1),
                                 "density": round(top.Density, 3)},
                       "bbox": s.pop("bbox"), "bodies": bodies, "faces": len(faces)}
                out.update(s)
                try:
                    out["threads"] = model_summary.threads(_thread_records(top))
                except Exception as e:
                    out["threads_err"] = str(e)[:80]
                return json.dumps(out, ensure_ascii=False, separators=(",", ":"))
            finally:
                if opened:
                    try:
                        doc.Close(KD_DO_NOT_SAVE_CHANGES)
                    except Exception:
                        pass
        except Exception as e:
            return f"Failed. {format_error(e)}"


_sdk_pages = {"key": None, "pages": None}


@mcp.tool()
def kompas_sdk_search(query: str, limit: int = 5) -> str:
    """Поиск по справке SDK КОМПАС (офлайн, без COM): интерфейсы, методы, свойства.
    Вместо Grep по огромным stub-файлам. query — слова через пробел (все обязательны;
    нет таких страниц — любое), регистр не важен. Заголовок страницы весомее текста.
    Ответ: top-limit строк «заголовок [путь в zip]» + фрагмент ~300 символов.
    Первый вызов строит индекс (~4 с, 14330 страниц), далее — из logs/sdk_index.json (~1 с в новом процессе, ~0,1 с в живом)."""
    import sdk_index
    zp = sdk_index.find_zip(SDK_HELP_ZIP_CANDIDATES)
    if not zp:
        return ("Failed: справка SDK не найдена (KOMPAS_SDK_ru-RU.zip). Искал: "
                + "; ".join(c for c in SDK_HELP_ZIP_CANDIDATES if c)
                + ". Положи zip по одному из путей или задай env KOMPAS_SDK_HELP_ZIP.")
    try:
        key = sdk_index.zip_key(zp)
        if _sdk_pages["key"] != (zp, key):
            _sdk_pages["pages"] = sdk_index.load_or_build(zp, SDK_INDEX_PATH)
            _sdk_pages["key"] = (zp, key)
        words = sdk_index.words_of(query)
        if not words:
            return "Failed: пустой query."
        found = sdk_index.search(_sdk_pages["pages"], query, max(1, min(int(limit), 20)))
        return sdk_index.format_results(found, words)
    except Exception as e:
        return f"Failed: поиск по справке SDK. {format_error(e)}"


_DOC_IFACE = {1: "IKompasDocument2D", 2: "IKompasDocument2D", 3: "ISpecificationDocument",
              4: "IKompasDocument3D", 5: "IKompasDocument3D", 6: "ITextDocument"}


def _gw_iface(obj):
    """Имя интерфейса объекта: класс gencache или, для динамического, его typeinfo."""
    name = type(obj).__name__
    if name in ("CDispatch", "PyIDispatch"):
        try:
            return obj._oleobj_.GetTypeInfo().GetDocumentation(-1)[0]
        except Exception:
            return name
    return name


def _gw_wrap(obj):
    """Динамический COM-объект → типизированная обёртка gencache (иначе нет GetX/SetX
    индексированных свойств и методов с out-параметрами)."""
    if type(obj).__name__ == "CDispatch":
        from win32com.client import gencache
        try:
            return gencache.EnsureDispatch(obj._oleobj_)
        except Exception:
            return obj
    return obj


def _gw_roots(kompas, app7):
    """Ленивые корни шлюза для АКТИВНОГО документа (адресный уже активирован)."""
    from win32com.client import CastTo
    cache = {}

    def get(name):
        if name in cache:
            return cache[name]
        doc = app7.ActiveDocument
        dt = doc.DocumentType if doc is not None else 0
        if name == "app7":
            v = app7
        elif name == "app5":
            v = kompas
        elif doc is None:
            raise RuntimeError("нет активного документа — открой его или укажи document=")
        elif name == "doc":
            v = CastTo(doc, _DOC_IFACE.get(dt, "IKompasDocument"))
        elif name == "top":
            if dt not in (4, 5):
                raise RuntimeError("top — только для детали/сборки")
            v = CastTo(doc, "IKompasDocument3D").TopPart
        elif name in ("doc5", "top5"):
            if dt in (4, 5):
                d5 = kompas.ActiveDocument3D()
                v = d5 if name == "doc5" else d5.GetPart(-1)
            elif name == "doc5" and dt in (1, 2):
                v = kompas.ActiveDocument2D()
            else:
                raise RuntimeError(f"{name} недоступен для документа типа {dt}")
        else:
            raise RuntimeError(f"нет корня {name}")
        cache[name] = v
        return v
    return get


@mcp.tool()
def kompas_api(ops: str, document: str = "") -> str:
    """Шлюз ко ВСЕМУ API КОМПАС (API7 и API5) без скрипта: JSON-список операторов,
    каждый проверяется по индексу интерфейсов v23 ДО вызова (опечатка → похожие имена).
    Оператор: {"obj": корень|"$имя", "cast": "IИнтерфейс"?, действие, "args": [...]?, "as": "имя"?}
      корни: app7 (IApplication), app5 (KompasObject), doc (документ: IKompasDocument3D/2D,
      ISpecificationDocument, ITextDocument), top (IPart7 верхней детали/сборки),
      doc5 / top5 (API5 ksDocument3D|ksDocument2D / ksPart) — по умолчанию "doc"
      действие: "get": "Имя" (+args у индексированного свойства) | "set": "Имя", "value": x
      | "call": "Метод" | "items": true (элементы коллекции) | "help": "IИнтерфейс[.Член]"
      | "find": "подстрока" (help/find — без КОМПАС, это справка по API)
      аргументы: "$имя" / "$имя.N" — сохранённый результат; {"const": "o3d_cutExtrusion"}.
    Пример: [{"obj":"top","cast":"IFeature7","get":"Variables","args":[false,true],"as":"v"},
             {"obj":"$v.0","get":"Expression"}]
    document — путь или часть имени (активным КОМПАС переключает сам, после — возвращает).
    Сохранить/закрыть/открыть через шлюз нельзя — kompas_save_document/close/open (бэкап,
    защиты). Ответ JSON {ok, steps[{i, result|error}], changed}; при changed — проверь
    kompas_measure. Частое — отдельными инструментами; шлюз — для всего остального."""
    import api_gateway as G
    import api_index
    try:
        parsed = G.parse_ops(ops)
        idx = api_index.load()
    except G.GatewayError as e:
        return f"Failed: {e}"
    if not G.needs_com(parsed):
        return G.dumps_clipped(G.run(parsed, {}, idx))
    from win32com.client import CastTo
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            with _active_guard(app7):
                err = _activate(app7, document)
                if err:
                    return err
                by_type = api_index.o3d_interfaces(idx)

                def wrap(o):
                    o = _gw_wrap(o)
                    if _gw_iface(o) == "IModelObject":     # элемент коллекции/дерева → свой интерфейс
                        try:
                            i = by_type.get(o.ModelObjectType)
                            if i:
                                return CastTo(o, i)
                        except Exception:
                            pass
                    return o
                a = app7.ActiveDocument
                ro = ""
                if a is not None and a.DocumentType in (1, 2):
                    ro = ("в чертеже/фрагменте шлюз только читает — чертит конвейер kompas_build_drawing "
                          "(агент kompas-draftsman); эскиз детали — в 3D-документе, там можно")
                res = G.run(parsed, _gw_roots(kompas, app7), idx, iface_of=_gw_iface,
                            cast=lambda o, i: CastTo(o, i), wrap=wrap, read_only=ro)
                try:
                    a = app7.ActiveDocument
                    res["document"] = (a.PathName or a.Name) if a is not None else ""
                except Exception:
                    pass
        except Exception as e:
            return f"Failed: {format_error(e)}"
    for s in res["steps"]:
        if s.pop("com", False):
            s["error"] += " — проверь аргументы ({\"help\": \"Интерфейс.Член\"}); текст справки: kompas_sdk_search"
    text = G.dumps_clipped(res)
    return text if res["ok"] else "Failed: " + text


def _as_list(v):
    """COM отдаёт коллекцию из одного элемента самим элементом, а не кортежем."""
    if v is None:
        return []
    return list(v) if isinstance(v, (tuple, list)) else [v]


def parse_var_values(text: str) -> dict:
    """'{"D":250,"H":"D/2"}' → {"D": 250.0, "H": "D/2"}: число — значение, строка — выражение."""
    if not text:
        return {}
    d = json.loads(text)
    if not isinstance(d, dict):
        raise ValueError("ожидается JSON-объект {имя: число|выражение}")
    out = {}
    for k, v in d.items():
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError(f"{k}: значение — число или строка-выражение")
        out[str(k)] = float(v) if isinstance(v, (int, float)) else v
    return out


def _var_rec(v, feature=""):
    r = {"name": v.Name, "value": round(v.Value, 6), "expr": v.Expression}
    for k, attr in (("note", "Note"), ("param", "ParameterNote")):
        try:
            if getattr(v, attr):
                r[k] = getattr(v, attr)
        except Exception:
            pass
    if v.External:
        r["external"] = True
    if feature:
        r["feature"] = feature
    return r


@mcp.tool()
def kompas_variables(document: str = "", set: str = "", add: str = "", features: bool = False) -> str:
    """Переменные детали/сборки: прочитать, добавить, задать — с перестроением и сверкой.
    set — '{"D":250,"H":"D/2"}': число → значение, строка → выражение. Имя ищется среди
    переменных модели, затем среди параметров операций (v17… — глубина, угол и т.п.;
    так операцию привязывают к переменной: {"v17": "H"}).
    add — '{"H":25}' новые переменные модели (затем можно ссылаться в set).
    features=True — показать и параметры операций (feature, param — что это за параметр).
    Ответ JSON: variables, [features], changed, before/after {gabarit, mass}."""
    from win32com.client import CastTo
    try:
        to_set, to_add = parse_var_values(set), parse_var_values(add)
    except Exception as e:
        return f"Failed: {e}"
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            with _active_guard(app7):
                err = _activate(app7, document)
                if err:
                    return err
                doc7, d3 = _active_3d(kompas, app7)
                top = d3.TopPart

                def snap():
                    g = top.GetGabarit(True, True)
                    return {"gabarit": [round(x, 3) for x in g[1:]] if g and g[0] else None,
                            "mass": round(top.Mass, 3)}
                out = {"document": doc7.PathName or doc7.Name}
                before = snap()
                fvars = []                       # (имя операции, IVariable7)
                if to_set or features:
                    for f in _as_list(CastTo(top, "IFeature7").SubFeatures(1, False, False)):
                        try:
                            fe = CastTo(f, "IFeature7")
                            for v in _as_list(fe.Variables(False, False)):
                                fvars.append((fe.Name, v))
                        except Exception:
                            pass
                model = {v.Name: v for v in _as_list(CastTo(top, "IFeature7").Variables(False, False))}
                feat = {v.Name: v for _, v in fvars}
                # всё проверить ДО первой правки: половинчатая правка без перестроения хуже отказа
                dup = [n for n in to_add if n in model or n in feat]
                if dup:
                    return f"Failed: переменная уже есть: {', '.join(dup)} — задай её через set."
                miss = [n for n in to_set if n not in model and n not in feat and n not in to_add]
                if miss:
                    return (f"Failed: нет переменной {', '.join(miss)}. Модели: {', '.join(model) or 'нет'}; "
                            "параметры операций — features=True; новую — add=")
                ro = [n for n in to_set if (model.get(n) or feat.get(n)) is not None
                      and (model.get(n) or feat.get(n)).ReadOnly]
                if ro:
                    return f"Failed: только для чтения: {', '.join(ro)}"
                changed, failed = [], []
                for name, val in to_add.items():
                    v = top.AddVariable(name, val if isinstance(val, float) else 0.0, "")
                    if v is None:
                        failed.append(f"{name}: AddVariable не создал переменную")
                        continue
                    model[name] = v
                    changed.append(name)
                    if isinstance(val, str):
                        to_set[name] = val
                for name, val in to_set.items():
                    v = model.get(name) or feat.get(name)
                    if v is None:
                        continue
                    try:
                        if isinstance(val, str):
                            v.Expression = val
                        else:
                            v.Expression = ""
                            v.Value = val
                        changed.append(name)
                    except Exception as e:
                        failed.append(f"{name}: {format_error(e)}")
                if changed:
                    d3.RebuildDocument()
                out["variables"] = [_var_rec(v) for v in model.values()]
                if features:
                    out["features"] = [_var_rec(v, fname) for fname, v in fvars]
                if failed:
                    out["failed"] = failed
                if changed:
                    out["changed"] = sorted(dict.fromkeys(changed))
                    out["before"], out["after"] = before, snap()
                    if out["after"]["mass"] == before["mass"] and out["after"]["gabarit"] == before["gabarit"]:
                        out["warning"] = "масса и габарит не изменились — переменная ни на что не влияет?"
                return json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return f"Failed: {format_error(e)}"


FEATURE_OPS = {"chamfer": (7, 33), "fillet": (7, 34), "thread": (6, 58)}   # (что выбирать, o3d)


def parse_feature_args(op: str, at: str, params: str):
    """Проверка аргументов kompas_feature без КОМПАС → (op, точки, параметры)."""
    op = (op or "").strip().lower()
    if op not in FEATURE_OPS:
        raise ValueError(f"op: {', '.join(FEATURE_OPS)}")
    pts = json.loads(at) if at else []
    if (not isinstance(pts, list) or not pts or
            not all(isinstance(p, list) and len(p) == 3 and all(isinstance(c, (int, float)) for c in p) for p in pts)):
        raise ValueError("at — JSON-список точек [[x,y,z], …] в мм, СК детали")
    if op == "thread" and len(pts) != 1:
        raise ValueError("thread — одна точка на цилиндрической грани")
    prm = json.loads(params) if params else {}
    if not isinstance(prm, dict):
        raise ValueError("params — JSON-объект")
    need = {"chamfer": ("size",), "fillet": ("r",), "thread": ("pitch",)}[op]
    miss = [k for k in need if k not in prm]
    if miss:
        raise ValueError(f"params для {op}: нет {', '.join(miss)}")
    return op, pts, prm


@mcp.tool()
def kompas_feature(op: str, at: str, params: str = "{}", document: str = "") -> str:
    """Фаска, скругление или условная резьба в ДЕТАЛИ с выбором рёбер/грани по точкам.
    op: chamfer | fillet | thread.
    at: JSON-список точек [[x,y,z],…] (мм, СК детали) — точка ЛЕЖИТ на ребре (chamfer/fillet;
        удобно — середина ребра) или на цилиндрической грани (thread, одна точка).
    params: chamfer {"size": 1, "size2"?: 1}; fillet {"r": 2};
            thread {"pitch": 1, "length"?: мм (по умолчанию на всю грань), "d"?: мм (по умолчанию Ø грани)}.
    Проверка: фаска/скругление — выросло число граней и изменилась масса; резьба — число
    резьб модели +1. Иначе — ошибка «операция пустая» (а не тихий успех)."""
    try:
        op, pts, prm = parse_feature_args(op, at, params)
    except Exception as e:
        return f"Failed: {e}"
    pick, o3d = FEATURE_OPS[op]
    from win32com.client import CastTo
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            with _active_guard(app7):
                err = _activate(app7, document)
                if err:
                    return err
                doc7, d3 = _active_3d(kompas, app7)
                if doc7.DocumentType != 4:
                    return "Failed: нужна деталь (.m3d) — в сборке операции делают в файле детали."
                part = kompas.ActiveDocument3D().GetPart(-1)
                top = d3.TopPart
                faces0, mass0 = part.EntityCollection(6).GetCount(), top.Mass
                threads0 = CastTo(top, "ISymbols3DContainer").Threads.Count
                objs = []
                for x, y, z in pts:
                    coll = part.EntityCollection(pick)
                    coll.SelectByPoint(float(x), float(y), float(z))
                    n = coll.GetCount()
                    what = "ребра" if pick == 7 else "грани"
                    if n > 1 and pick == 7:
                        # одно ребро приходит дважды (от каждой из двух смежных граней):
                        # одинаковые тип и длина — значит, это оно же
                        sig = set()
                        for k in range(n):
                            ed = coll.GetByIndex(k).GetDefinition()
                            ends = []
                            for first in (True, False):
                                vx = ed.GetVertex(first)
                                if vx is not None:
                                    p = vx.GetPoint(0.0, 0.0, 0.0)
                                    ends.append(tuple(round(c, 3) for c in p[1:4]))
                            sig.add((ed.IsCircle(), ed.IsLineSeg(), round(ed.GetLength(1), 4),
                                     tuple(sorted(ends))))
                        if len(sig) == 1:
                            n = 1
                    if n == 0:
                        return (f"Failed: через точку ({x}, {y}, {z}) не проходит ни одного {what}. "
                                "Точка должна лежать на нём (СК детали); проверь kompas_model_summary.")
                    if n > 1:
                        return (f"Failed: точка ({x}, {y}, {z}) попала на {n} {what} (вершина/стык?). "
                                "Возьми точку внутри ребра, например середину.")
                    objs.append(coll.First())
                e = part.NewEntity(o3d)
                d = e.GetDefinition()
                if op == "thread":
                    d.SetBaseObject(objs[0])
                    d.autoDefinDr = "d" not in prm
                    if "d" in prm:
                        d.dr = float(prm["d"])
                    d.p = float(prm["pitch"])
                    d.allLength = "length" not in prm
                    if "length" in prm:
                        d.length = float(prm["length"])
                else:
                    if op == "chamfer":
                        d.SetChamferParam(True, float(prm["size"]), float(prm.get("size2", prm["size"])))
                    else:
                        d.radius = float(prm["r"])
                    arr = d.array()
                    for o in objs:
                        arr.Add(o)
                ok = e.Create()
                faces1, mass1 = part.EntityCollection(6).GetCount(), top.Mass
                threads1 = CastTo(top, "ISymbols3DContainer").Threads.Count
                res = {"op": op, "created": bool(ok), "faces": [faces0, faces1],
                       "mass": [round(mass0, 3), round(mass1, 3)], "threads": [threads0, threads1]}
                if op == "thread":
                    good = ok and threads1 == threads0 + 1
                    if good:
                        t = CastTo(CastTo(top, "ISymbols3DContainer").Threads.Thread(threads1 - 1), "IThreadsParameters")
                        res["thread"] = {"d": t.Diameter, "pitch": t.Pitch, "internal": bool(t.Internal)}
                else:
                    good = ok and faces1 > faces0 and abs(mass1 - mass0) > 1e-6
                text = json.dumps(res, ensure_ascii=False)
                if not good:
                    try:                                  # не оставлять пустую операцию в дереве
                        kompas.ActiveDocument3D().DeleteObject(e)
                        text += " | созданная операция удалена"
                    except Exception:
                        text += " | созданную операцию удалить не удалось — проверь дерево"
                    return "Failed: операция пустая или не построилась — " + text
                return text + " | деталь НЕ сохранена"
        except Exception as e:
            return f"Failed: {format_error(e)}"


INTERSECTION_TYPES = {1: "точка", 2: "линия", 3: "касание поверхностью", 4: "ОБЪЁМ"}


def boxes_overlap(a, b, tol=0.01):
    """Боксы [x1,y1,z1,x2,y2,z2] перекрываются больше чем на tol по каждой оси."""
    return all(min(a[i + 3], b[i + 3]) - max(a[i], b[i]) > tol for i in range(3))


@mcp.tool()
def kompas_interference(document: str = "", match: str = "", touch: bool = False,
                        max_pairs: int = 5000) -> str:
    """Пересечения ТЕЛ деталей в сборке (не габаритов): все листья дерева на любой
    глубине, предотбор по габаритам, затем IBody7/ksBody.CheckIntersectionWithBody.
    match — проверять только пары, где имя хотя бы одной детали содержит подстроку.
    Ответ JSON: collisions [{a, b}] — объёмные пересечения (реальная коллизия; объём в мм³
    API не отдаёт), touching — число касаний (touch=True — список; плоское касание торцами
    КОМПАС не сообщает — проверено), checked — пар проверено.
    Ожидаемые коллизии: винт в резьбовом отверстии (резьба не моделируется), поджатое уплотнение."""
    import pythoncom
    from win32com.client import CastTo, gencache
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            m5 = gencache.EnsureModule("{0422828C-F174-495E-AC5D-D31014DBBE87}", 0, 1, 0)
            with _active_guard(app7):
                err = _activate(app7, document)
                if err:
                    return err
                doc7, d3 = _active_3d(kompas, app7)
                if doc7.DocumentType != 5:
                    return "Failed: нужна сборка (.a3d)."
                leaves = []

                def walk(parts, prefix):
                    for k in range(parts.Count):
                        p = CastTo(parts.Part(k), "IPart7")
                        name = f"{prefix}{p.Name}"
                        if p.Detail:
                            leaves.append((name, p))
                        else:
                            walk(p.Parts, name + "/")
                walk(d3.TopPart.Parts, "")
                items = []
                for name, p in leaves:
                    kp = kompas.TransferInterface(p, 1, 0)           # API7 IPart7 → API5 ksPart
                    if not hasattr(kp, "GetMainBody"):
                        kp = m5.ksPart(kp._oleobj_.QueryInterface(m5.ksPart.CLSID, pythoncom.IID_IDispatch))
                    g = kp.GetGabarit(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    body = kp.GetMainBody()
                    if g and g[0] and body is not None:
                        items.append((name, list(g[1:7]), body))
                seen = {}
                named = []
                for name, g, b in items:          # одинаковые имена экземпляров — с номером
                    seen[name] = seen.get(name, 0) + 1
                    named.append((f"{name} #{seen[name]}" if seen[name] > 1 else name, g, b))
                m = match.lower()
                collisions, touching, checked = [], [], 0
                for i in range(len(named)):
                    for j in range(i + 1, len(named)):
                        a, b = named[i], named[j]
                        if m and m not in a[0].lower() and m not in b[0].lower():
                            continue
                        if not boxes_overlap(a[1], b[1], -0.01 if touch else 0.01):
                            continue
                        if checked >= max_pairs:
                            break
                        checked += 1
                        r = a[2].CheckIntersectionWithBody(b[2], False)
                        n = r.GetCount() if r is not None else 0
                        types = {r.GetIntersectionType(k) for k in range(n)}
                        if 4 in types:
                            collisions.append({"a": a[0], "b": b[0]})
                        elif types:
                            touching.append({"a": a[0], "b": b[0],
                                             "types": sorted(INTERSECTION_TYPES.get(t, str(t)) for t in types)})
                out = {"document": doc7.PathName or doc7.Name, "parts": len(named),
                       "checked": checked, "collisions": collisions,
                       "touching": touching if touch else len(touching)}
                if checked >= max_pairs:
                    out["warning"] = f"достигнут предел max_pairs={max_pairs} — сузь match"
                return json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return f"Failed: {format_error(e)}"


BOM_ORDER = {"сборка": 0, "деталь": 1, "стандартное": 2, "крепёж (по имени)": 3}


def bom_kind(detail: bool, standard: bool, name: str) -> str:
    if standard:
        return "стандартное"
    if not detail:
        return "сборка"
    low = (name or "").lower()
    return "крепёж (по имени)" if any(w in low for w in FASTENER_WORDS) else "деталь"


def bom_group(recs):
    """Записи {kind,name,marking,material,mass_g,file} → строки с qty, порядок ЕСКД-разделов."""
    rows = {}
    for r in recs:
        key = (r["kind"], r["file"].lower(), r["marking"], r["name"])
        if key in rows:
            rows[key]["qty"] += 1
        else:
            rows[key] = dict(r, qty=1)
    return sorted(rows.values(), key=lambda r: (BOM_ORDER.get(r["kind"], 9), r["marking"], r["name"]))


@mcp.tool()
def kompas_bom(document: str = "", flat: bool = False) -> str:
    """Состав изделия сборки (BOM) — только чтение. По умолчанию первый уровень, как в
    спецификации ЕСКД (подсборки — строками); flat=True — все детали на любой глубине.
    Строки сгруппированы по файлу+обозначению+имени: {kind, name, marking, qty, material,
    mass_g, file}; kind — сборка | деталь | стандартное (IPart7.Standard) | крепёж (по имени:
    болт/винт/гайка/шайба… — свои модели крепежа проекта не помечены стандартными).
    Пустые обозначение/материал у деталей — в warnings (оформление по правилам проекта)."""
    from win32com.client import CastTo
    with com_context():
        try:
            kompas, app7 = get_typed_api()
            with _active_guard(app7):
                err = _activate(app7, document)
                if err:
                    return err
                doc7, d3 = _active_3d(kompas, app7)
                if doc7.DocumentType != 5:
                    return "Failed: нужна сборка (.a3d)."
                recs = []

                def walk(parts):
                    for k in range(parts.Count):
                        p = CastTo(parts.Part(k), "IPart7")
                        if flat and not p.Detail and not p.Standard:
                            walk(p.Parts)
                            continue
                        recs.append({"kind": bom_kind(p.Detail, p.Standard, p.Name), "name": p.Name,
                                     "marking": p.Marking or "", "material": p.Material or "",
                                     "mass_g": round(p.Mass, 2), "file": p.FileName or ""})
                walk(d3.TopPart.Parts)
                rows = bom_group(recs)
                warn = [f"{r['name']}: нет {'обозначения' if not r['marking'] else 'материала'}"
                        for r in rows if r["kind"] == "деталь" and (not r["marking"] or not r["material"])]
                out = {"document": doc7.PathName or doc7.Name, "flat": flat,
                       "instances": len(recs), "rows": rows}
                if warn:
                    out["warnings"] = warn[:30]
                return json.dumps(out, ensure_ascii=False)
        except Exception as e:
            return f"Failed: {format_error(e)}"


# Tools callable from inside a batch. kompas_batch itself is absent on purpose:
# nesting would make failure reporting ambiguous.
BATCHABLE = (
    "kompas_status", "kompas_launch", "kompas_create_document", "kompas_open_document",
    "kompas_save_document", "kompas_close_document", "kompas_backup",
    "kompas_view", "kompas_screenshot", "kompas_measure", "kompas_get_bbox",
    "kompas_get_tree", "kompas_insert", "kompas_move", "kompas_part_props",
    "kompas_shape", "kompas_audit_fasteners", "kompas_run_job", "kompas_run_script",
    "kompas_check_drawing", "kompas_snapshot", "kompas_place_dimensions",
    "kompas_sdk_search", "kompas_model_summary", "kompas_api", "kompas_variables", "kompas_feature", "kompas_interference", "kompas_bom",
)


def _unwrapped(name):
    """The tool function without the timing wrapper.

    Sub-actions must not each write their own line to calls.jsonl: the batch
    already logs one line, and double counting would inflate the machine-time
    figures that a timing report would show."""
    fn = globals().get(name)
    return getattr(fn, "__wrapped__", fn)


def _clip_result(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    """Обрезать результат действия пакета до limit символов, но сохранить
    последнюю строку `JSON: …` (машинный итог kompas_check_drawing)."""
    if len(text) <= limit:
        return text
    head = text[:limit] + f"\n… (обрезано, всего {len(text)} символов)"
    last = text.rstrip("\n").rsplit("\n", 1)[-1]
    if last.startswith("JSON: "):
        head += "\n" + last
    return head


def _script_needs_backup(code: str):
    """Assemblies mentioned in a Save/SaveAs script that have no fresh backup.

    The PreToolUse hook guards kompas_run_script, but it cannot see inside a
    batch payload, so the same rule is enforced here. Without this a batch
    would be a hole straight through the project's backup rule."""
    if not re.search(r"\.(Save|SaveAs)\s*\(", code or ""):
        return []
    missing = []
    for mt in re.finditer(r"[\"']([^\"']+?\.a3d)[\"']", code, re.IGNORECASE):
        p = mt.group(1).replace("\\\\", "\\")
        if p.lower().endswith("_backup.a3d") or not os.path.exists(p):
            continue
        stem = os.path.splitext(p)[0]
        b = stem + "_backup.a3d"
        if not (os.path.exists(b) and (time.time() - os.path.getmtime(b)) < 8 * 3600):
            missing.append(p)
    return missing


@mcp.tool()
def kompas_batch(actions: str, stop_on_error: bool = True, budget_s: float = 0.0) -> str:
    """Run a CHAIN of kompas_* calls in ONE round trip. Use it whenever the next
    call does not depend on reading the previous result — which is most of the
    time: a part is a sequence of operations, and editing a part inside an
    assembly is always close -> open -> edit -> save -> open.

    actions: JSON list, each item {"tool": "<name>", "args": {...}}, e.g.
      [{"tool":"kompas_create_document","args":{"doc_type":"part"}},
       {"tool":"kompas_shape","args":{"op":"base","profile":"[{\\"circle\\":[0,0,65]}]","depth":12}},
       {"tool":"kompas_part_props","args":{"name":"Заглушка 130","material":"...","density":7.92}},
       {"tool":"kompas_measure","args":{}}]

    Stops at the first failed action (stop_on_error=False runs the rest anyway)
    and always reports what already ran, so the chain can be resumed rather than
    replayed. Nesting kompas_batch inside itself is rejected.

    budget_s: own time budget, 240 s by default — deliberately below the
    300 s MCP tool timeout. On reaching it the batch stops ITSELF and says which
    action to resume from; without this the batch is killed mid-flight and the
    caller cannot tell what was applied. Progress is also mirrored to
    logs/last_batch.json after every action, so even a hard kill
    (timeout, KOMPAS crash) leaves a trail — read that file instead of guessing.
    A single action cannot be interrupted; anything over 60 s is flagged in the
    reply so it can be moved out of the batch.

    Measured on a real hour-long task: 105 KOMPAS calls formed only 26
    uninterrupted chains, so batching removes most of the round trips - and the
    round trip, not KOMPAS, is what the time is spent on.
    """
    try:
        items = json.loads(actions)
    except Exception as e:
        return f"Failed: actions is not valid JSON. {e}"
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        return "Failed: actions must be a non-empty JSON list."
    if len(items) > MAX_BATCH_ACTIONS:
        return (f"Failed: {len(items)} actions, limit is {MAX_BATCH_ACTIONS}. "
                "Split it — a long chain is also harder to resume after an error.")

    # validate the whole batch before running anything: a half-executed batch
    # rejected on its last action is worse than one rejected up front
    plan = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or "tool" not in it:
            return f"Failed: action {i} has no `tool` field."
        name = str(it["tool"]).strip()
        name = name.split("__")[-1]              # tolerate the mcp__kompas3d__ prefix
        if name == "kompas_batch":
            return f"Failed: action {i} nests kompas_batch inside itself."
        if name not in BATCHABLE:
            return (f"Failed: action {i}: unknown or non-batchable tool {name!r}. "
                    f"Allowed: {', '.join(BATCHABLE)}")
        args = it.get("args") or {}
        if not isinstance(args, dict):
            return f"Failed: action {i}: `args` must be an object."
        if name == "kompas_run_script":
            missing = _script_needs_backup(args.get("code", ""))
            if missing:
                return ("Failed: ПРАВИЛО ПРОЕКТА — в пакете есть скрипт с Save/SaveAs, "
                        "а у этих сборок нет свежего бэкапа:\n"
                        + "\n".join(f"  {p}" for p in missing)
                        + "\nСохраняй через kompas_save_document (он делает копию сам) "
                          "или создай бэкап отдельным действием в начале пакета.")
        plan.append((name, args))

    budget = float(budget_s) if budget_s and budget_s > 0 else BATCH_BUDGET_S
    started = time.perf_counter()
    out, done, slow = [], [], []
    failed_at, budget_at = None, None
    # Весь пакет под ОДНОЙ блокировкой: иначе другой агент может вклиниться
    # МЕЖДУ действиями и переключить активный документ — и остаток пакета
    # уйдёт не в тот файл. Блокировка реентерабельна, вложенные захваты
    # внутри самих инструментов бесплатны.
    _acquire_com(COM_LOCK_WAIT_S)
    try:
        _write_progress(len(plan), done, "начат")

        for i, (name, args) in enumerate(plan):
            # Always run the first action: a batch that returns having done nothing
            # is worse than one that overshoots slightly.
            if i and (time.perf_counter() - started) >= budget:
                budget_at = i
                break
            t0 = time.perf_counter()
            try:
                res = _unwrapped(name)(**args)
                bad = isinstance(res, str) and res.startswith(_ERR_PREFIXES)
            except Exception as e:
                res, bad = f"Failed: {format_error(e)}", True
            ms = (time.perf_counter() - t0) * 1000.0
            if ms > SLOW_ACTION_S * 1000.0:
                slow.append(f"[{i}] {name} — {ms/1000:.0f} с")
            text = _clip_result(res if isinstance(res, str) else repr(res))
            out.append(f"[{i}] {name} ({ms:.0f} мс){' — ОШИБКА' if bad else ''}\n{text}")
            done.append({"i": i, "tool": name, "ok": not bad, "ms": round(ms)})
            _write_progress(len(plan), done, "ошибка" if bad else "выполняется")
            if bad:
                failed_at = i
                if stop_on_error:
                    break

        spent = time.perf_counter() - started
        _write_progress(len(plan), done, "завершён")
        head = [f"Пакет: выполнено {len(out)} из {len(plan)} действий за {spent:.0f} с"]
        if budget_at is not None:
            head.append(f"ОСТАНОВЛЕНО ПО БЮДЖЕТУ ВРЕМЕНИ ({budget:.0f} с), а не по ошибке. "
                        f"Действия [0..{budget_at - 1}] применены — продолжай с [{budget_at}] "
                        "новым пакетом. Если упирается регулярно — дели пакет мельче.")
        if failed_at is not None:
            applied = (f"Действия [0..{failed_at - 1}] уже применены — не повторяй их, "
                       if failed_at else "Ничего применить не успело, ")
            head.append(f"ОСТАНОВЛЕНО на действии [{failed_at}] ({plan[failed_at][0]}). "
                        + applied + f"продолжай с [{failed_at}] после исправления."
                        if stop_on_error else
                        f"Действие [{failed_at}] не удалось, остальные выполнены.")
        if slow:
            head.append("Долгие действия (прервать такое нельзя, выноси из пакета отдельным вызовом): "
                        + ", ".join(slow))
    finally:
        _release_com()

    return "\n".join(head) + "\n\n" + "\n\n".join(out)


# ---------------------------------------------------------------------------
# Drawing rule checker
# ---------------------------------------------------------------------------

DRAWING_CHECKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "logs", "drawing_checks")
# Литералы правил (материалы, Ra, ряд масштабов, ТТ, допуски) — в
# project_rules.json; оценка Ч01–Ч16 — drawing_rules.evaluate (без COM).


def _paper_rect(view, bb, D):
    """bbox из координат вида -> бумажный (с учётом поворота вида)."""
    pts = [D.view_to_paper(view, x, y) for x in (bb[0], bb[2]) for y in (bb[1], bb[3])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _view_kind(i, name):
    # Тип вида по имени. Координаты у всех видов одни: D.view_to_paper (Э0).
    if "Разрез" in name:
        return "section"
    if "Выносной" in name:
        return "detail"
    if i == 0 or "Системный" in name:
        return "system"
    return "proj"


def _collect_drawing_facts(doc) -> dict:
    """COM-часть проверки: факты чертежа в формате drawing_rules (см. его docstring).
    Правил здесь нет — только чтение."""
    from win32com.client import CastTo
    import drawing_rules
    D = _load_draw()

    # --- листы и штамп -----------------------------------------------------
    sheets = doc.LayoutSheets
    formats = [int(sheets.Item(si).Format.Format) for si in range(sheets.Count)]
    sheet_x = drawing_rules.sheet_x_ranges(
        formats, [bool(sheets.Item(si).Format.VerticalOrientation) for si in range(sheets.Count)])
    st = sheets.Item(0).Stamp

    def cell(n):
        try:
            return drawing_rules.stamp_text(st.Text(n).Str or "").strip()
        except Exception:
            return ""
    stamp = {"name": cell(1), "marking": cell(2), "material": cell(3),
             "mass": cell(5), "scale": cell(6)}
    sheet_markings = []
    for si in range(sheets.Count):        # графа 2 каждого листа (форма 2а листа 2) — Ч03
        try:
            sheet_markings.append(drawing_rules.stamp_text(
                sheets.Item(si).Stamp.Text(2).Str or "").strip())
        except Exception:
            sheet_markings.append("")

    # --- виды, размеры, выноски ----------------------------------------------
    views = D.views_of(doc)
    # бумажные габариты (ksGetObjGabaritRect) — по ним проверяется позиция текста
    try:
        gab = dict(D.gabarits(doc))
    except Exception:
        gab = {}
    vfacts, dims, leaders, tolerances = [], [], [], []
    for i in range(views.Count):
        v = views.View(i)
        name = v.Name or ""
        kind = _view_kind(i, name)
        try:
            vc = D.view_content(v)
        except Exception:
            vc = {"bbox": None}
        try:
            scale = v.Scale
        except Exception:
            scale = None
        shown = False
        try:
            shown = bool(CastTo(v, "IViewDesignation").ShowScale)
        except Exception:
            pass
        title = None
        if kind in ("section", "detail"):      # заголовок «А-А», «Б (10:1)» — Ч12 (задача 14c)
            try:
                title = D.view_title_rect(doc, i)
            except Exception:
                pass
        bb_s = vc.get("bbox_shown") or vc["bbox"]          # вид с разрывом — видимый габарит
        bbp = list(_paper_rect(v, bb_s, D)) if bb_s else None
        vfacts.append({"idx": i, "name": name, "kind": kind, "scale": scale,
                       "scale_shown": shown, "has_geometry": bool(vc["bbox"]),
                       "bbox_paper": bbp, "title_rect_paper": title,
                       "sheet": 1 if kind == "system" else drawing_rules.sheet_of_x(
                           (bbp[0] + bbp[2]) / 2 if bbp else v.X, sheet_x)})
        if kind == "proj" and vc["bbox"]:
            try:      # Ч16: окружности/дуги против маркеров центра и осевых (СК вида)
                vfacts[-1].update(
                    circles=[list(c) for c in vc["circles"]],
                    arcs=[[a[0], a[1], a[2], round(drawing_rules.arc_sweep(a[0], a[1], *a[3:])[1], 1)]
                          for a in vc["arc_list"]],
                    centre_markers=[[round(x, 2), round(y, 2), t] for x, y, t in D.view_markers(v)],
                    axes=[[round(q, 2) for q in a] for a in D.view_axes(v)])
            except Exception:
                pass
        try:
            sc = CastTo(v, "ISymbols2DContainer")   # свежий прокси на каждый вызов
            packs = [(sc.LineDimensions, "LineDimension", "lin"),
                     (sc.DiametralDimensions, "DiametralDimension", "dia"),
                     (sc.RadialDimensions, "RadialDimension", "rad"),
                     (sc.AngleDimensions, "AngleDimension", "ang")]
        except Exception:
            continue
        for coll, getter, tag in packs:
            for j in range(coll.Count):
                try:
                    o = getattr(coll, getter)(j)
                    text, prefix, frame, sign = "", "", False, 0
                    try:
                        dt = CastTo(o, "IDimensionText")
                        text = (dt.NominalText.Str or "").strip()
                        prefix = dt.Prefix.Str or ""
                        frame = bool(dt.Rectangle)
                        sign = int(dt.Sign or 0)
                    except Exception:
                        pass
                    xy, est = None, False
                    # X3/Y3, сверенные с бумажным габаритом размера (все виды)
                    if tag == "lin":
                        try:
                            xy = D.dim_text_paper(v, o.X3, o.Y3, gab.get(f"v{i}.lin{j}"))
                        except Exception:
                            pass
                    else:        # Ø/R/угол: ОЦЕНКА по габариту и центру (задача 16, Ч12 — предупреждение)
                        try:
                            xy = D.dim_text_estimate(v, o, gab.get(f"v{i}.{tag}{j}"))
                            est = xy is not None
                        except Exception:
                            pass
                    dims.append({"view": i, "id": f"{tag}{j}", "kind": tag, "text": text,
                                 "text_xy_paper": xy, "prefix": prefix, "frame": frame, "est": est,
                                 "sign": sign})
                except Exception:
                    pass
        try:         # рамки допусков формы/расположения (Ч18, задача 16)
            tc = sc.Tolerances
            for j in range(tc.Count):
                t = tc.Tolerance(j)
                tp = CastTo(t, "IToleranceParam")
                br = CastTo(t, "IBranchs")
                x0, y0 = D.view_to_paper(v, br.X0, br.Y0)
                tolerances.append({"view": i, "id": f"tol{j}", "sign": int(tp.Sign), "value": tp.Value,
                                   "anchor_paper": [round(x0, 1), round(y0, 1)],
                                   "base_pos": int(tp.BasePointPos)})
        except Exception:
            pass
        try:
            lds = sc.Leaders
            for j in range(lds.Count):
                t, rect, segs = "", None, []
                try:
                    lo = lds.Leader(j)
                    t = (CastTo(lo, "ILeader").TextOnShelf.Str or "")
                    # Ч12 (задача 14c): текст на полке и ветви к стрелкам — на листе
                    br = CastTo(lo, "IBranchs")
                    p0 = D.view_to_paper(v, br.X0, br.Y0)
                    for k in range(br.BranchCount):
                        pk = D.view_to_paper(v, br.BranchX(k), br.BranchY(k))
                        segs.append([round(p0[0], 1), round(p0[1], 1), round(pk[0], 1), round(pk[1], 1)])
                    rect = drawing_rules.leader_text_rect(gab.get(f"v{i}.led{j}"), p0,
                                                          CastTo(lo, "ILeader").ShelfDirection)
                except Exception:
                    pass
                leaders.append({"view": i, "id": f"led{j}", "text": t,
                                "text_rect_paper": rect, "lines_paper": segs})
        except Exception:
            pass

    # --- тексты системного вида (знак Ra) и ТТ -----------------------------------
    sys_texts, sys_sheets = [], []
    try:
        dcs = CastTo(views.View(0), "IDrawingContainer").DrawingTexts
        for j in range(dcs.Count):
            try:
                t = dcs.DrawingText(j)
                txt, n = CastTo(t, "IText").Str or "", drawing_rules.sheet_of_x(t.X, sheet_x)
                sys_texts.append(txt)
                sys_sheets.append(n)
            except Exception:
                pass
    except Exception:
        pass
    try:          # штатный знак неуказанной шероховатости (ISpecRough) — тоже знак Ra листа 1
        sr = CastTo(doc, "IDrawingDocument").SpecRough
        if sr.IsCreated and sr.Text:
            sys_texts.append(str(sr.Text))
            sys_sheets.append(1)
    except Exception:
        pass
    tt_lines = []
    try:
        tt = CastTo(doc, "IDrawingDocument").TechnicalDemand.Text
        for j in range(tt.Count):
            tt_lines.append(tt.TextLine(j).Str or "")
    except Exception:
        pass
    # --- Ч19: тексты обозначений и заголовки видов не касаются ЧУЖИХ линий и текстов (точная
    # геометрия: каждое обозначение разбивается на отрезки и тексты, D.obj_parts; шаг 2г вала)
    title_hits = []
    try:
        import drawing_rules as _dr
        tags = {"lin": ("LineDimensions", "LineDimension"), "dia": ("DiametralDimensions", "DiametralDimension"),
                "rad": ("RadialDimensions", "RadialDimension"), "ang": ("AngleDimensions", "AngleDimension"),
                "tol": ("Tolerances", "Tolerance"), "led": ("Leaders", "Leader"), "cut": ("CutLines", "CutLine")}
        parts = {}
        for name in gab:
            m = re.match(r"v(\d+)\.([a-z]+)(\d+)$", name)
            if not m or m.group(2) not in tags:
                continue
            vi, tag, j = int(m.group(1)), m.group(2), int(m.group(3))
            vv = views.View(vi)
            cname, getter = tags[tag]
            obj = getattr(getattr(CastTo(vv, "ISymbols2DContainer"), cname), getter)(j)
            segs, txts = D.obj_parts(doc, vv, obj)
            parts[name] = ([q for q in segs if math.hypot(q[2] - q[0], q[3] - q[1]) >= 4.0], txts)
        for vf in vfacts:
            tr = vf.get("title_rect_paper")
            if tr:
                parts[f"v{vf['idx']}.title"] = ([], [tuple(tr)])
        title_hits = _dr.annotation_hits(parts)
    except Exception as e:
        title_hits.append(f"не проверено: {format_error(e)[:80]}")
    return {"formats": formats, "stamp": stamp, "sheet_markings": sheet_markings, "title_hits": title_hits,
            "views": vfacts, "dims": dims, "tolerances": tolerances,
            "leaders": leaders, "sys_texts": sys_texts, "sys_text_sheets": sys_sheets,
            "tt_lines": tt_lines, "hints": _spec_hints(doc.PathName or "")}


def _spec_hints(cdw_path: str) -> dict:
    """Подсказки проверке из spec, лежащей рядом с чертежом (<имя>.spec.json; для черновика
    <имя>.draft.cdw — та же spec): ra: null → знак Ra не требуется (Ч13, решение 2026-10-15);
    spec с полем intent (утверждённая таблица замысла) → ra_spec (Ч13 требует знак из spec) и
    dia_on_image (Ч10 не судит Ø/М-размеры на изображении ступеней)."""
    hints = {}
    try:
        stem = re.sub(r"\.draft$", "", os.path.splitext(cdw_path)[0], flags=re.IGNORECASE)
        sp = stem + ".spec.json"
        if stem and os.path.isfile(sp):
            with open(sp, encoding="utf-8") as f:
                s = json.load(f)
            if "ra" in s and s["ra"] is None:
                hints["ra_declined"] = True
            # spec от компоновщика по УТВЕРЖДЁННОЙ таблице замысла (composer.py, этап 2в):
            # общий Ra — решение таблицы (Ч13), Ø/М на ступени тела вращения — решение (Ч10)
            if s.get("intent") and isinstance(s.get("ra"), str):
                hints["ra_spec"] = s["ra"]
            if s.get("intent") and any(d.get("pos") is not None and d.get("orient") == "v"
                                       and d.get("prefix") in ("Ø", "М") for d in s.get("dims") or []):
                hints["dia_on_image"] = True
    except Exception:
        pass
    return hints


def _check_report(doc_name: str, checks: list) -> str:
    """Ответ kompas_check_drawing: таблица (её читают люди и drawing_metrics
    по строке «FAIL: n»), подсказки к FAIL и последней строкой `JSON: {...}`."""
    fails = [c for c in checks if not c["ok"]]
    warns = [c for c in checks if c.get("warning")]
    lines = [f"Чертёж: {doc_name}",
             f"Проверок: {len(checks)}, FAIL: {len(fails)}" + (f", предупреждений: {len(warns)}" if warns else ""),
             "", "| Правило | Вердикт | Детали |", "|---|---|---|"]
    for c in checks:
        verdict = "**FAIL**" if not c["ok"] else ("⚠ предупреждение" if c.get("warning") else "OK")
        lines.append(f"| {c['code']} {c['title']} | {verdict} | {c['detail']} |")
    if fails:
        lines += ["", "ИСПРАВИТЬ (хук не даст сохранить с FAIL):"]
        lines += [f"  - {c['code']} {c['title']}: {c['detail']} → {c['fix_hint']}" for c in fails]
    else:
        lines += ["", "Все автоматические проверки пройдены. Остаются [глаз]-пункты — "
                  "компоновка, направление стрелок взгляда — их смотрит человек по превью."]
    if warns:
        lines += ["", "ПРЕДУПРЕЖДЕНИЯ (не блокируют сохранение):"]
        lines += [f"  - {c['code']} {c['title']}: {c['detail']} → {c['fix_hint']}" for c in warns]
    errors = [{"code": c["code"], "object": c["object"], "fix_hint": c["fix_hint"]}
              for c in fails]
    warnings = [{"code": c["code"], "object": c["object"], "fix_hint": c["fix_hint"]}
                for c in warns]
    lines += ["", "JSON: " + json.dumps({"fails": len(fails), "errors": errors, "warnings": warnings},
                                        ensure_ascii=False)]
    return "\n".join(lines)


@mcp.tool()
def kompas_check_drawing(document: str = "") -> str:
    """Проверить открытый чертёж (.cdw) по правилам проекта и ЕСКД. Возвращает
    таблицу «правило — OK/FAIL — где» и пишет вердикт в
    logs/drawing_checks/<имя>.json — его требует хук перед сохранением .cdw.

    Это и есть самопроверка: чертёж не считается готовым, пока здесь есть FAIL.
    Прогонять после каждой порции правок, а не один раз в конце.

    Правила с пометкой [глаз] (структурность компоновки, направление стрелки
    взгляда и т.п.) инструмент не проверяет — их смотрит человек по превью.
    """
    with com_context():
        try:
            import drawing_rules
            app = get_app7()
            err = _activate(app, document)
            if err:
                return err
            doc = app.ActiveDocument
            if doc is None or doc.DocumentType != 1:
                return "Failed: активный документ — не чертёж (.cdw)."

            facts = _collect_drawing_facts(doc)
            checks = drawing_rules.evaluate(facts, drawing_rules.load_rules())

            fails = [c for c in checks if not c["ok"]]

            # вердикт для хука
            try:
                os.makedirs(DRAWING_CHECKS_DIR, exist_ok=True)
                path = doc.PathName or ""
                key = os.path.splitext(os.path.basename(path or doc.Name))[0]
                with open(os.path.join(DRAWING_CHECKS_DIR, key + ".json"), "w",
                          encoding="utf-8") as f:
                    json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "file": path, "fails": len(fails),
                               "failed": [f"{c['code']} {c['title']}" for c in fails]},
                              f, ensure_ascii=False, indent=1)
            except Exception:
                pass
            return _check_report(doc.PathName or doc.Name, checks)
        except Exception as e:
            return f"Failed to check drawing. {format_error(e)}"

@mcp.tool()
def kompas_snapshot(document: str = "", view: int = -1,
                    fields: str = "views,dims,texts,conflicts") -> str:
    """Чертёж как компактный JSON: штамп, виды (bbox на листе), размеры (текст и
    позиция), тексты/ТТ, конфликты правил. Заменяет скриншоты при проверке
    позиций и значений. view=-1 — все виды, иначе только этот индекс;
    fields — views,dims,texts,conflicts. Координаты — мм на листе, шаг 0,1.
    Только чтение.
    """
    with com_context():
        try:
            import drawing_rules
            app = get_app7()
            err = _activate(app, document)
            if err:
                return err
            doc = app.ActiveDocument
            if doc is None or doc.DocumentType != 1:
                return "Failed: активный документ — не чертёж (.cdw)."
            facts = _collect_drawing_facts(doc)
            snap = drawing_rules.snapshot_from_facts(facts, view, fields)
            return json.dumps({"doc": doc.Name, **snap}, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            return f"Failed to snapshot drawing. {format_error(e)}"


def _ref_view_points(av, ref):
    """Ссылка lin → две точки в координатах вида (формат — layout.py)."""
    t = ref.get("type")
    if t == "view_points":
        return [tuple(map(float, ref[k][:2])) for k in ("p1", "p2")]
    if t == "points":
        D = _load_draw()                  # D.project: на виде с разрывом GetProjectionPoint врёт
        out = []
        for k in ("p1", "p2"):
            ok, xv, yv = D.project(av, *map(float, ref[k][:3]))
            if not ok:
                raise ValueError(f"GetProjectionPoint не спроецировал {k}={ref[k]}")
            out.append((xv, yv))
        return out
    raise ValueError(f"ref.type {t!r} не для lin: нужен points или view_points")


DIM_TEXT_TOL = 0.05     # мм: «4,75» → «4,8» (эталон 30.09) — на границе ещё годно
PCD_RAY_OUT = 5.0       # мм листа: осевой луч окружности центров — за неё (задача 16)
ANG_RADIUS = 18.0       # мм листа: радиус дуги углового размера по умолчанию (Р2 plate_leap)


DIM_TEXT_TOL_SQUARE = 0.1   # мм: «□» — одно число на две стороны сетки (модель: 1127,234/1127,306)


def _dim_text_check(text: str, measured: float, tol: float = DIM_TEXT_TOL):
    """Номинал из spec (dims[].text) против измеренного значения размера
    (IDimensionText.NominalValue): (ok, пояснение). Допуск tol (DIM_TEXT_TOL; у «□» —
    DIM_TEXT_TOL_SQUARE: стороны квадратной сетки в модели расходятся на сотые)."""
    try:
        want = float(str(text).strip().replace(",", "."))
    except ValueError:
        return False, f"text {text!r} — не число"
    diff = abs(want - float(measured))
    ok = diff <= tol + 1e-9
    return ok, (f"text «{text}» против измеренного {float(measured):.3f} мм: "
                f"расхождение {diff:.3f} > {tol} — не та ссылка ref или опечатка в text"
                if not ok else "")


@mcp.tool()
def kompas_place_dimensions(document: str, view: int, dims: str) -> str:
    """Поставить размеры в вид чертежа: LLM говорит ЧТО образмерить, код — ГДЕ.
    Вылеты считает лесенка (layout.ladder: снаружи контура, 10 мм + 8 мм на
    ступень, тексты не ближе 7 мм друг к другу и к уже стоящим), номинал — авто.
    Не сохраняет. После — kompas_check_drawing.

    view — индекс вида (1.., не повёрнутый). dims — JSON-список:
      {"id": "L", "kind": "lin"|"dia"|"rad", "ref": {...},
       "side": "top"|"bottom"|"left"|"right"|null,  # lin; null: h — снизу, v — слева
       "orient": "h"|"v"|null,                       # lin; null — по большей разнице
       "angle": 45,                                  # dia/rad, град.
       "thread": "М4-6H" | {"text": "М4-6H", "depth": 8, "drill_d": 3.3, "drill_depth": 11},
                                                     # dia: резьба вместо номинала, без Ø (_thread_parts)
       "prefix": "2 отв. ",                          # lin/dia: Prefix размера («2 отв. Ø4,5»)
       "suffix": "*",                                # lin/dia/rad: Suffix размера («130*», справочный, ГОСТ 2.307)
       "text": "22,6"}                               # номинал вместо авто, |text−измер.| ≤ 0,05
                                                     # (иначе ошибка code dims:text_mismatch)
    ref (мм):
      {"type":"points","p1":[x,y,z],"p2":[x,y,z]}  — точки МОДЕЛИ (проецируются
          на вид IAssociationView.GetProjectionPoint); для lin;
      {"type":"circle","center":[x,y,z],"d":3.3}   — окружность МОДЕЛИ: ищется
          в виде (|Ø−d| ≤ 0,05, центр ≤ 0,5 мм); нет окружности — дуга вида, angle
          внутри дуги или середина дуги (стрелка на дуге); для dia/rad;
      {"type":"view_points","p1":[x,y],"p2":[x,y]} — точки в координатах ВИДА; для lin.
    Возвращает JSON {"placed": [{id, kind, value_text, text_xy_paper}],
    "errors": [{id, error}]}; text_xy_paper — мм листа, как в kompas_snapshot.
    """
    with com_context():
        try:
            import layout
            from win32com.client import CastTo
            try:
                items = json.loads(dims) if isinstance(dims, str) else dims
            except Exception as e:
                return f"Failed: dims — не JSON. {e}"
            if not isinstance(items, list):
                return "Failed: dims — нужен JSON-список."
            app = get_app7()
            err = _activate(app, document)
            if err:
                return err
            doc = app.ActiveDocument
            if doc is None or doc.DocumentType != 1:
                return "Failed: активный документ — не чертёж (.cdw)."
            D = _load_draw()
            views = D.views_of(doc)
            vi = int(view)
            if not 0 < vi < views.Count:
                return f"Failed: вида {view} нет (видов {views.Count}, 0 — системный)."
            v = views.View(vi)
            if abs(getattr(v, "Angle", 0.0) or 0.0) > 1e-6:
                return f"Failed: вид {vi} повёрнут (Angle={v.Angle}) — не поддерживается."
            av = CastTo(v, "IAssociationView")
            vc = D.view_content(v)
            if not vc["bbox"]:
                return f"Failed: в виде {vi} нет геометрии."
            bb = vc["bbox"]
            reg = D.detail_region(v)      # выносной: лесенка — от границы зоны, а не от геометрии
            if reg:                       # (рамка «150,1» лежала на границе зоны, задача 16)
                bb = (min(bb[0], reg[0]), min(bb[1], reg[1]), max(bb[2], reg[2]), max(bb[3], reg[3]))
            region = next((it.get("region") for it in items if isinstance(it, dict) and it.get("region")), None)
            if region:                    # местный вид (crop): видна только область — лесенка от неё
                bb = tuple(float(q) for q in region)
            contour = _paper_rect(v, bb, D)
            # тексты линейных размеров, что уже стоят на листе (Ч12 судит весь лист)
            obstacles = [d["text_xy_paper"] for d in _collect_drawing_facts(doc)["dims"]
                         if d["kind"] == "lin" and d.get("text_xy_paper")]

            errors, lins, rounds, angs, texts, opts = [], [], [], [], {}, {}
            rays_by = {}        # id pcd-размера → осевые лучи (опоры угловых, задача 16)
            for i, it in enumerate(items):
                did = str(it.get("id") or f"d{i}")
                opts[did] = it
                if it.get("text") is not None:
                    texts[did] = str(it["text"])
                try:
                    kind, ref = it.get("kind"), it.get("ref") or {}
                    if kind == "lin":
                        pv = _ref_view_points(av, ref)
                        pp = [D.view_to_paper(v, *q) for q in pv]
                        o, a, b = layout.lin_axis(pp[0], pp[1], it.get("orient"))
                        if abs(b - a) < 0.01:
                            raise ValueError("нулевой пролёт: точки совпадают по оси размера")
                        if ref.get("type") == "points" and D.break_zones(v):
                            rp = [D.raw_projection(av, *map(float, ref[k][:3])) for k in ("p1", "p2")]
                            it["_raw_len"] = abs(rp[1][0] - rp[0][0]) if o == "h" else abs(rp[1][1] - rp[0][1])
                        lins.append({"id": did, "orient": o, "a": a, "b": b,
                                     "side": it.get("side"), "pv": pv, "row": int(it.get("row") or 0),
                                     "pos": it.get("pos"), "text_pos": float(it.get("text_pos") or 0),
                                     "text_at": it.get("text_at"), "shelf": it.get("shelf"),
                                     "prefix": it.get("prefix") or "",
                                     "suffix": it.get("suffix") or ""})
                    elif kind == "dia" and ref.get("type") == "pcd":
                        # окружность центров группы: осевая окружность + лучи через центры
                        # отверстий (опоры угловых размеров) + Ø-размер (Р2 plate_leap)
                        ok, xv, yv = D.project(av, *map(float, ref["center"][:3]))
                        if not ok:
                            raise ValueError("центр окружности центров не спроецирован")
                        rr = float(ref["d"]) / 2
                        D.axis_circle(v, xv, yv, rr)
                        rays = []
                        for p in ref.get("rays") or []:
                            okp, px, py = D.project(av, *map(float, p[:3]))
                            if not okp:
                                raise ValueError(f"точка луча {p} не спроецирована")
                            a_deg = math.degrees(math.atan2(py - yv, px - xv))
                            rays.append(D.ray(v, xv, yv, a_deg, rr + PCD_RAY_OUT / v.Scale))
                        rays_by[did] = rays
                        rounds.append((did, "dia", (xv, yv, rr), float(it.get("angle", 45.0)),
                                       None, it.get("prefix") or "", it.get("suffix") or "",
                                       float(it.get("leg") or D.DIA_SHELF_LEG)))
                    elif kind == "ang":
                        angs.append((did, it))
                    elif kind in ("dia", "rad"):
                        if ref.get("type") != "circle":
                            raise ValueError("для dia/rad нужен ref.type = circle")
                        ok, xv, yv = D.project(av, *map(float, ref["center"][:3]))
                        if not ok:
                            raise ValueError("центр окружности не спроецирован")
                        c = layout.match_circle(vc["circles"], xv, yv, float(ref["d"]))
                        ang = float(it.get("angle", 45.0))
                        if c is None:     # дуга (ушко R10): стрелка — на дугу
                            a = layout.match_circle(vc["arc_list"], xv, yv, float(ref["d"]))
                            if a is None:
                                raise ValueError(f"окружности или дуги Ø{ref['d']} у "
                                                 f"({xv:.2f}, {yv:.2f}) в виде нет")
                            c, ang = a[:3], layout.arc_angle(a, it.get("angle"))
                        th = it.get("thread") if kind == "dia" else None
                        rounds.append((did, kind, c, ang,
                                       {"text": th} if isinstance(th, str) else th,
                                       it.get("prefix") or "", it.get("suffix") or "",
                                       float(it.get("leg") or D.DIA_SHELF_LEG)))
                    else:
                        raise ValueError(f"kind {kind!r}: нужно lin, dia или rad")
                except Exception as e:
                    errors.append({"id": did, "error": str(e)})

            def sc():
                return CastTo(v, "ISymbols2DContainer")   # свежий прокси (stale X3)
            made = []   # (id, kind, индекс в коллекции, объект)
            # pos — размерная линия задана компоновщиком (СК вида: y для h, x для v), лесенка
            # её не двигает (Ø прямо на ступени вала, ряды ГОСТ 2.307 — composer.py)
            free = [d for d in lins if d.get("pos") is None]
            placed = dict(zip([d["id"] for d in free], layout.ladder(contour, free, obstacles=obstacles)))
            for d in lins:
                if d.get("pos") is not None:
                    q = float(d["pos"])
                    px, py = D.view_to_paper(v, q, q)
                    placed[d["id"]] = {"text_xy": (px, py)}
            for d, r in ((d, placed[d["id"]]) for d in lins):
                if "error" in r:
                    errors.append({"id": d["id"], "error": r["error"]})
                    continue
                try:
                    j = sc().LineDimensions.Count
                    (x1, y1), (x2, y2) = d["pv"]
                    ta = d.get("text_at")     # положение текста вдоль линии (СК вида) — с осевой
                    if d["orient"] == "h":
                        o = D.lin(v, x1, y1, x2, y2, orientation=1,
                                  y3=(r["text_xy"][1] - v.Y) / v.Scale, prefix=d["prefix"],
                                  suffix=d["suffix"], text_pos=d["text_pos"],
                                  **({"x3": float(ta)} if ta is not None else {}))
                    else:
                        o = D.lin(v, x1, y1, x2, y2, orientation=2,
                                  x3=(r["text_xy"][0] - v.X) / v.Scale, prefix=d["prefix"],
                                  suffix=d["suffix"], text_pos=d["text_pos"],
                                  **({"y3": float(ta)} if ta is not None else {}))
                    if d.get("shelf"):        # текст на полке с выноской (узкая ступень, опыт 2г)
                        D.lin_shelf(o, *map(float, d["shelf"]))
                    made.append((d["id"], "lin", j, o))
                except Exception as e:
                    errors.append({"id": d["id"], "error": format_error(e)})
            for did, kind, (xc, yc, rr), ang, thread, prefix, suffix, leg in rounds:
                try:
                    if kind == "dia":
                        j = sc().DiametralDimensions.Count
                        if thread:   # резьба: «N отв. » префикс, номинал, глубины — суффикс
                            tp = _thread_parts(thread["text"], thread.get("depth"),
                                               thread.get("drill_d"), thread.get("drill_depth"))
                            o = D.dia(v, xc, yc, rr, ang, prefix=tp["prefix"],
                                      text=tp["nominal"], no_sign=True, leg=leg)
                            if tp["suffix"]:
                                dt = CastTo(o, "IDimensionText")
                                nf = CastTo(dt.NominalText.TextItem(0), "ITextFont")
                                D.text_segments(dt.Suffix, tp["suffix"], height=nf.Height,
                                                font=nf.FontName)
                                CastTo(o, "IDrawingObject").Update()
                        else:
                            o = D.dia(v, xc, yc, rr, ang, prefix=prefix, suffix=suffix, leg=leg)
                    else:
                        j = sc().RadialDimensions.Count
                        o = D.rad(v, xc, yc, rr, ang, suffix=suffix)
                    made.append((did, kind, j, o))
                except Exception as e:
                    errors.append({"id": did, "error": format_error(e)})

            for did, it in angs:        # угловые — по лучам pcd или по вершине и двум точкам
                try:
                    ref = it["ref"]
                    if ref["type"] == "rays":
                        rays = rays_by.get(ref["pcd"])
                        if not rays:
                            raise ValueError(f"pcd {ref['pcd']!r} с лучами не построен в этом вызове")
                        b1, b2 = rays[int(ref["i1"])], rays[int(ref["i2"])]
                    else:
                        okc, cx, cy = D.project(av, *map(float, ref["center"][:3]))
                        ok1, x1, y1 = D.project(av, *map(float, ref["p1"][:3]))
                        ok2, x2, y2 = D.project(av, *map(float, ref["p2"][:3]))
                        if not (okc and ok1 and ok2):
                            raise ValueError("вершина или точки угла не спроецированы")
                        L = max(math.hypot(x1 - cx, y1 - cy), math.hypot(x2 - cx, y2 - cy)) + PCD_RAY_OUT / v.Scale
                        b1 = D.ray(v, cx, cy, math.degrees(math.atan2(y1 - cy, x1 - cx)), L)
                        b2 = D.ray(v, cx, cy, math.degrees(math.atan2(y2 - cy, x2 - cx)), L)
                    j = sc().AngleDimensions.Count
                    o = D.ang(v, b1, b2, float(it.get("radius", ANG_RADIUS)),
                              direction=bool(it.get("direction")), frame=bool(it.get("frame")))
                    made.append((did, "ang", j, o))
                except Exception as e:
                    errors.append({"id": did, "error": format_error(e)})

            for did, kind, j, o in made:     # номинал: text из spec (сотые → десятые) или round
                it = opts.get(did, {})
                try:
                    dt = CastTo(o, "IDimensionText")
                    if did in texts:
                        ok, why = _dim_text_check(texts[did], it.get("_raw_len", dt.NominalValue),
                                                  DIM_TEXT_TOL_SQUARE if it.get("square") else DIM_TEXT_TOL)
                        if not ok:
                            errors.append({"id": did, "code": "dims:text_mismatch", "error": why})
                        else:
                            dt.AutoNominalValue = False
                            dt.NominalText.Str = texts[did].replace(".", ",")
                            CastTo(o, "IDrawingObject").Update()
                    elif it.get("round") and kind != "ang":
                        val = float(dt.NominalValue)
                        r1 = round(val, 1)
                        if abs(r1 - val) > DIM_TEXT_TOL + 1e-9:
                            errors.append({"id": did, "code": "dims:round",
                                           "error": f"номинал {val:.3f} не округляется до десятых в пределах "
                                                    f"±{DIM_TEXT_TOL} — задать text или иные базы"})
                        elif abs(r1 - val) > 1e-9:
                            dt.AutoNominalValue = False
                            dt.NominalText.Str = f"{r1:g}".replace(".", ",")
                            CastTo(o, "IDrawingObject").Update()
                    if it.get("frame") and kind != "ang":      # у ang рамку ставит D.ang
                        D.set_frame(o)
                    if it.get("square"):
                        D.set_square(o)
                    if it.get("shift") and kind == "lin":
                        D.shift_text(v, o, float(it["shift"]))
                except Exception as e:
                    errors.append({"id": did, "error": format_error(e)})

            for did, kind, j, o in made:     # отклонения индексами (ГОСТ 2.307 п. 1.9) и текст с осевой
                it = opts.get(did, {})
                try:
                    if it.get("dev") and kind in ("lin", "dia", "rad"):
                        D.set_deviations(o, it["dev"])
                    if kind == "lin" and it.get("text_at") is not None:
                        ln = next(q for q in lins if q["id"] == did)
                        (x1, y1), _ = ln["pv"]
                        tpv = abs(float(it["text_at"]) - (y1 if ln["orient"] == "v" else x1)) * v.Scale
                        dp = CastTo(o, "IDimensionParams")
                        dp.TextType = 1           # ручное размещение: TextPos — мм листа от точки 1
                        dp.TextPos = int(round(tpv))
                        CastTo(o, "IDrawingObject").Update()
                except Exception as e:
                    errors.append({"id": did, "code": "dims:text", "error": format_error(e)})
            tols = {}                        # рамки допусков под текстом размера (эталон плиты)
            for did, kind, j, o in made:
                tl = opts.get(did, {}).get("tol")
                if not tl or kind == "ang":
                    continue
                try:
                    shelf_right = True
                    if kind in ("dia", "rad"):
                        try:
                            shelf_right = int(CastTo(o, "IDimensionParams").ShelfDirection) != -1
                        except Exception:
                            pass
                    x0, y0 = D.tol_anchor_under_dim(doc, v, o, shelf_right=shelf_right)
                    if kind in ("dia", "rad"):     # стрелка — на кромку окружности, ближнюю к рамке
                        xc, yc, rr = o.Xc, o.Yc, o.Radius
                        a = math.atan2(y0 - yc, x0 - xc)
                        branch = (xc + rr * math.cos(a), yc + rr * math.sin(a))
                    else:
                        branch = (o.X1, o.Y1)
                    sign = tl.get("sign", "pos")
                    dia_sign = tl.get("dia_sign", sign in ("pos", 27))
                    D.tolerance(v, x0, y0, sign=sign, value=tl["value"],
                                prefix_sign=2 if dia_sign else 0, base_pos=3,
                                branch=branch if tl.get("branch", True) else None, base=tl.get("base"))
                    tols[did] = True
                except Exception as e:
                    errors.append({"id": did, "code": "dims:tol", "error": format_error(e)})

            gab = dict(D.gabarits(doc, [vi])) if made else {}
            placed = []
            for did, kind, j, o in made:
                g = gab.get(f"v{vi}.{kind}{j}")
                if kind == "lin":
                    xy = D.dim_text_paper(v, o.X3, o.Y3, g)
                else:
                    xy = [round((g[0] + g[2]) / 2, 1), round((g[1] + g[3]) / 2, 1)] if g else None
                try:
                    txt = (CastTo(o, "IDimensionText").NominalText.Str or "").strip()
                except Exception:
                    txt = ""
                row = {"id": did, "kind": kind, "value_text": txt, "text_xy_paper": xy}
                if opts.get(did, {}).get("tol"):
                    row["tol"] = bool(tols.get(did))
                placed.append(row)
            return json.dumps({"placed": placed, "errors": errors}, ensure_ascii=False,
                              separators=(",", ":"))
        except Exception as e:
            return f"Failed to place dimensions. {format_error(e)}"


# ---------------------------------------------------------------------------
# Drawing spec -> whole drawing in one call (kompas_build_drawing, задача 9)
# ---------------------------------------------------------------------------

SPEC_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "schemas", "drawing_spec.schema.json")
# Optional Markdown file with the drawing review checklist ([глаз] items). Not shipped by default.
RULES_MD_PATH = os.environ.get("KOMPAS_RULES_MD") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "rules.md")
_SECTION_KEY_RE = re.compile(r"^([А-ЯЁA-Z])-\1$")
_DETAIL_KEY_RE = re.compile(r"^[А-ЯЁA-Z]$")
PREVIEW_MAX_PX = 1400


# Оси вида на листе (вправо, вверх) как оси модели — замер v23 на копии проставки
# (задача 9, GetProjectionPoint независимых видов; модель «Z вверх»).
_ORIENT_AXES = {"#Спереди": ((0, 1, 0), (0, 0, 1)), "#Сзади": ((0, -1, 0), (0, 0, 1)),
                "#Сверху": ((0, 1, 0), (-1, 0, 0)), "#Снизу": ((0, 1, 0), (1, 0, 0)),
                "#Слева": ((1, 0, 0), (0, 0, 1)), "#Справа": ((-1, 0, 0), (0, 0, 1))}


def _projection_code(main: str, orient: str):
    """Код AddStandartViews, дающий вид `orient` как ПРОЕКЦИЮ главного вида `main`
    (в проекционной связи, с поворотом по ЕСКД): взгляд со стороны «верха» главного →
    3 (под главным), «низа» → 4 (над), «левой» стороны → 5 (справа), «правой» → 6
    (слева). Направление на зрителя = вправо × вверх. Сверено живьём: «#Слева» и
    «#Спереди» с [3, 4, 5, 6]. None — вид не проекция (тот же или обратный взгляд)."""
    r, u = _ORIENT_AXES[main]
    t = _orient_toward(orient)
    for code, want in ((3, u), (4, tuple(-c for c in u)), (5, tuple(-c for c in r)), (6, r)):
        if t == want:
            return code
    return None


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _orient_toward(orient: str):
    """Направление на зрителя (ось модели) для ориентации «#…»: вправо × вверх."""
    return _cross(*_ORIENT_AXES[orient])


def _toward_from_axes(ax: dict):
    """Направление на зрителя по замеру вида: ax = {"X"|"Y"|"Z": (dx, dy)} — образы
    единичных осей модели на виде (GetProjectionPoint минус образ начала). Строка
    «вправо» = (X.dx, Y.dx, Z.dx), «вверх» = (X.dy, Y.dy, Z.dy), округление до целых."""
    r = tuple(int(round(ax[k][0])) for k in "XYZ")
    u = tuple(int(round(ax[k][1])) for k in "XYZ")
    return _cross(r, u)


def _match_views(want: dict, measured: dict):
    """Ключи spec → индексы видов ПО ИЗМЕРЕННОЙ ориентации, а не по порядку создания.
    want {key: "#…"}, measured {индекс: направление на зрителя}. Возвращает
    (idx, ошибки); ключ без единственного совпадения в idx не попадает."""
    by = {}
    for i, t in measured.items():
        by.setdefault(tuple(t), []).append(i)
    idx, errs = {}, []
    for key, o in want.items():
        hits = by.get(_orient_toward(o), [])
        if len(hits) == 1:
            idx[key] = hits[0]
        else:
            errs.append(f"вид {key!r} ({o}): видов с таким направлением взгляда {len(hits)} "
                        f"(замер {measured})")
    return idx, errs


def _view_axes(v) -> dict:
    """Образы осей модели X/Y/Z на виде (СК вида, мм) через GetProjectionPoint."""
    from win32com.client import CastTo
    av = CastTo(v, "IAssociationView")
    pts = {}
    for k, p in (("o", (0.0, 0.0, 0.0)), ("X", (1.0, 0.0, 0.0)), ("Y", (0.0, 1.0, 0.0)),
                 ("Z", (0.0, 0.0, 1.0))):
        ok, x, y = av.GetProjectionPoint(*p)
        if not ok:
            raise RuntimeError(f"GetProjectionPoint{p} не спроецировал")
        pts[k] = (x, y)
    o = pts["o"]
    return {k: (pts[k][0] - o[0], pts[k][1] - o[1]) for k in "XYZ"}


_THREAD_LATIN = str.maketrans({"M": "М", "x": "х"})


def _num(v) -> str:
    return f"{float(v):g}".replace(".", ",")


def _thread_parts(text: str, depth=None, drill_d=None, drill_depth=None) -> dict:
    """Обозначение резьбы для D.dia: {"prefix": «N отв. », "nominal": «М4-6H»,
    "suffix": сегменты для D.text_segments}. Нормализация (выбрано вместо отказа):
    латинские M/x → кириллические «М»/«х» (так в чертежах пользователя; «×» шрифт ГОСТ
    печатает «Ч»), точка в шаге → запятая; поле допуска (6H/6g) не трогается.
    «N отв. » уходит в префикс: точку в номинале КОМПАС меняет на запятую.
    Глухая (ГОСТ 2.311, Г16): depth → «↧8»; drill_d, drill_depth → « / Ø3,3↧11»."""
    m = re.match(r"^(\d+\s*отв\.\s*)?(.+)$", text.strip())
    prefix, nom = m.group(1) or "", m.group(2)
    head, sep, tol = nom.rpartition("-")
    if not sep:
        head, tol = nom, ""
    nominal = head.translate(_THREAD_LATIN).replace(".", ",") + sep + tol
    suffix = []
    if depth is not None:
        suffix = [("depth",), ("text", _num(depth))]
        if drill_d is not None:
            suffix[-1] = ("text", _num(depth) + " / ")
            suffix += [("dia",), ("text", _num(drill_d))]
            if drill_depth is not None:
                suffix += [("depth",), ("text", _num(drill_depth))]
    return {"prefix": prefix, "nominal": nominal, "suffix": suffix}


def _thread_label(parts: dict) -> str:
    """Текст обозначения резьбы одной строкой (для отчётов и тестов)."""
    sym = {"depth": "↧", "dia": "Ø"}
    return parts["prefix"] + parts["nominal"] + "".join(
        sym.get(s[0]) or s[1] for s in parts["suffix"])


def _scale_value(s: str) -> float:
    """«1:2,5» → 0.4, «2:1» → 2.0."""
    a, b = (float(p.strip().replace(",", ".")) for p in s.split(":"))
    return a / b


def _spec_paths(cdw: str):
    """(черновик <имя>.draft.cdw, снимок <имя>.state.json) рядом с чистовым .cdw."""
    stem = os.path.splitext(cdw)[0]
    return stem + ".draft.cdw", stem + ".state.json"


def _long_path(p: str) -> str:
    """8.3-путь (…\\USERNA~1\\…) → длинный: КОМПАС отдаёт PathName длинным,
    и _activate(document=...) по короткому пути документ не находит."""
    d, name = os.path.split(os.path.abspath(p))
    try:
        import win32api
        return os.path.join(win32api.GetLongPathName(d), name)
    except Exception:
        return os.path.abspath(p)


def _json_path(path) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out or "spec"


def validate_spec(spec) -> list:
    """Ошибки drawing_spec (пусто — годна). Чистая, без COM: структура — JSON-схема
    schemas/drawing_spec.schema.json, затем смысл: абсолютные пути, файл модели есть,
    масштабы из ряда ГОСТ 2.302 (project_rules.json), ключи видов уникальны, первый вид —
    вид модели с orient, `from` разреза/выносного — вид, объявленный ВЫШЕ, обозначения
    «А-А» / «Б», dims/threads/leaders ссылаются на существующий вид, kind ↔ ref.type.
    Смысловые проверки идут только по структурно годной spec."""
    import jsonschema
    import drawing_rules
    with open(SPEC_SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    val = jsonschema.Draft202012Validator(schema)
    errs = [f"{_json_path(e.absolute_path)}: {e.message}"
            for e in sorted(val.iter_errors(spec), key=lambda e: [str(p) for p in e.absolute_path])]
    if errs:
        return errs

    scales = drawing_rules.load_rules()["gost_2302_scales"]
    for k in ("model", "cdw"):
        if not os.path.isabs(spec[k]):
            errs.append(f"{k}: нужен абсолютный путь, а не {spec[k]!r}")
    if os.path.isabs(spec["model"]) and not os.path.isfile(spec["model"]):
        errs.append(f"model: файла модели нет: {spec['model']}")
    if spec["scale"] not in scales:
        errs.append(f"scale: {spec['scale']!r} не из ряда ГОСТ 2.302: {', '.join(scales)}")

    keys, codes = [], {}
    for i, v in enumerate(spec["views"]):
        k, t = v["key"], v.get("type")
        if i and not t:
            main = spec["views"][0].get("orient")
            c = _projection_code(main, v["orient"]) if main in _ORIENT_AXES else None
            if c is None:
                errs.append(f"views[{i}].orient: {v['orient']} не проекция главного {main} — "
                            "допустимы виды сверху/снизу/слева/справа относительно главного")
            elif c in codes:
                errs.append(f"views[{i}]: вид {k!r} повторяет проекцию вида {codes[c]!r}")
            else:
                codes[c] = k
        if k in keys:
            errs.append(f"views[{i}].key: ключ {k!r} повторяется")
        if i == 0 and t:
            errs.append(f"views[0]: первым должен идти вид модели с orient (главный), а не {t}")
        if i == 0 and v.get("sheet", 1) != 1:
            errs.append("views[0].sheet: главный вид — на листе 1 (штамп, Ra, ТТ — лист 1, Р05)")
        if t:
            if v["from"] not in keys:
                errs.append(f"views[{i}].from: вида {v['from']!r} нет среди объявленных выше "
                            f"({', '.join(keys) or 'пусто'})")
            if t == "section" and not _SECTION_KEY_RE.match(k):
                errs.append(f"views[{i}].key: разрез обозначается «А-А» (буква-дефис-та же буква), "
                            f"а не {k!r}")
            if t == "detail" and not _DETAIL_KEY_RE.match(k):
                errs.append(f"views[{i}].key: выносной элемент — одна заглавная буква «Б», а не {k!r}")
            if v.get("scale") is not None and v["scale"] not in scales:
                errs.append(f"views[{i}].scale: {v['scale']!r} не из ряда ГОСТ 2.302")
        keys.append(k)

    on2 = [i for i, v in enumerate(spec["views"]) if v.get("sheet") == 2]
    if "format2" in spec and not on2:
        errs.append("format2: задан формат листа 2, а видов с sheet 2 нет")
    if on2 and (spec["format"] != 3 or spec.get("vertical")):
        errs.append(f"views[{on2[0]}].sheet: лист 2 — только при листе 1 А3 горизонтальном "
                    "(format 3, vertical false): смещение листа 2 замерено живьём только так")
    if on2 and spec.get("format2", 3) != 3:
        errs.append("format2: лист 2 — только А3 горизонтальный (3): иное не замерено")

    # поворот главного вида (задача 16): только когда проекционных видов нет —
    # проекционная связь повёрнутого вида не замерена
    if spec["views"][0].get("rotate") and any(not v.get("type") for v in spec["views"][1:]):
        errs.append("views[0].rotate: поворот главного вида — только без проекционных видов "
                    "(разрезы и выносные допустимы)")
    for i, v in enumerate(spec["views"]):
        if v.get("type") == "detail":
            if v.get("form") == "rect":
                if "w" not in v or "h" not in v:
                    errs.append(f"views[{i}]: form rect требует w и h (мм модели в СК вида {v['from']!r})")
            elif "r" not in v:
                errs.append(f"views[{i}]: выносной с круглой границей требует r (или form rect с w, h)")

    for sec in ("dims", "threads", "leaders", "bases", "gdt", "roughs"):
        for i, d in enumerate(spec.get(sec) or []):
            if d["view"] not in keys:
                errs.append(f"{sec}[{i}].view: вида {d['view']!r} нет; виды spec: {', '.join(keys)}")
    pcd_ids = {(d["view"], d.get("id")) for d in spec.get("dims") or []
               if d["kind"] == "dia" and d["ref"]["type"] == "pcd" and d.get("id")}
    for i, d in enumerate(spec.get("dims") or []):
        need = {"lin": ("points", "view_points"), "dia": ("circle", "pcd"), "rad": ("circle",),
                "ang": ("rays", "angle")}[d["kind"]]
        if d["ref"]["type"] not in need:
            errs.append(f"dims[{i}]: kind {d['kind']} требует ref.type {' или '.join(need)}, "
                        f"а не {d['ref']['type']}")
        if "prefix" in d and d["kind"] in ("rad", "ang"):
            errs.append(f"dims[{i}].prefix: префикс — только у lin/dia, не у {d['kind']}")
        if d.get("square") and d["kind"] != "lin":
            errs.append(f"dims[{i}].square: знак «□» — только у lin")
        if "shift" in d and d["kind"] != "lin":
            errs.append(f"dims[{i}].shift: вынос текста за выносную — только у lin")
        if "row" in d:
            if d["kind"] != "lin":
                errs.append(f"dims[{i}].row: ряд лесенки — только у lin")
            elif isinstance(d["row"], bool) or not isinstance(d["row"], int) or d["row"] < 0:
                errs.append(f"dims[{i}].row: целое ≥ 0")
        if d["ref"]["type"] == "rays":
            r = d["ref"]
            if (d["view"], r["pcd"]) not in pcd_ids:
                errs.append(f"dims[{i}].ref.pcd: в виде {d['view']!r} нет dia-размера с id {r['pcd']!r} "
                            "и ref.type pcd")
            else:
                src = next(x for x in spec["dims"] if x["view"] == d["view"] and x.get("id") == r["pcd"])
                n = len(src["ref"].get("rays") or [])
                if max(r["i1"], r["i2"]) >= n or r["i1"] == r["i2"]:
                    errs.append(f"dims[{i}].ref: индексы лучей i1={r['i1']}, i2={r['i2']} — у pcd "
                                f"{r['pcd']!r} лучей {n}, индексы должны быть разными и < {n}")
        if d.get("tol"):
            sg = d["tol"].get("sign", "pos")
            if isinstance(sg, str) and sg not in ("pos", "flat", "straight", "round", "cyl",
                                                  "par", "perp", "coax"):
                errs.append(f"dims[{i}].tol.sign: {sg!r} — ключ pos/flat/straight/round/cyl/par/perp/coax "
                            "или номер спецзнака")
            if d["kind"] == "ang":
                errs.append(f"dims[{i}].tol: допуск под угловым размером не ставится")
    for i, d in enumerate(spec.get("threads") or []):
        if d["ref"]["type"] != "circle":
            errs.append(f"threads[{i}]: нужен ref.type circle (окружность отверстия)")
        if ("drill_d" in d) != ("drill_depth" in d):
            errs.append(f"threads[{i}]: сверление глухой резьбы — drill_d и drill_depth вместе")
        if "drill_d" in d and "depth" not in d:
            errs.append(f"threads[{i}]: при drill_d/drill_depth нужна глубина резьбы depth")
        if "drill_depth" in d and "depth" in d and d["drill_depth"] < d["depth"]:
            errs.append(f"threads[{i}]: drill_depth {d['drill_depth']} меньше глубины резьбы "
                        f"depth {d['depth']}")
    return errs


def _mass_check(stamp_txt: str, model_grams: float, unit: int):
    """Ruling 9: масса в штампе против массы модели. unit — SheetStampMassUnit
    (0 г, 1 кг, 2 т; прочее — как кг). Допуск — половина последнего ПОКАЗАННОГО
    разряда: ловит усечение вместо округления (сбойный процесс КОМПАС, задача 6:
    0,147 → «0,14»). В граммах КОМПАС пишет единицу: «0,54 г» — она должна совпасть
    с unit. Возвращает (ok, пояснение)."""
    s = (stamp_txt or "").strip().replace(" ", "").replace(".", ",")
    m = re.fullmatch(r"(\d+)(?:,(\d+))?(г|кг|т)?", s)
    if not m:
        return False, f"масса в штампе {stamp_txt!r} — не число"
    digits = len(m.group(2) or "")
    shown = float((m.group(1) + "." + (m.group(2) or "0")))
    name, k = {0: ("г", 1.0), 2: ("т", 1e6)}.get(int(unit), ("кг", 1000.0))
    if m.group(3) and m.group(3) != name:
        return False, f"масса в штампе {stamp_txt!r}, а единица чертежа — {name}"
    model = model_grams / k
    tol = 0.5 * 10 ** -digits
    ok = abs(shown - model) <= tol + 1e-9
    return ok, (f"штамп «{stamp_txt}» {name}, модель {model:.{digits + 2}f} {name}, "
                f"допуск ±{tol:g}")


def _glaz_aspects(text: str = None) -> list:
    """Короткие имена [глаз]-пунктов rules.md (их автопроверка не судит): пункт с
    меткой `[глаз]` или любой пункт раздела, в заголовке которого `[глаз]`.
    Имя — код + текст до первого « (», «;», « — », «. », не длиннее 60 символов."""
    if text is None:
        try:
            with open(RULES_MD_PATH, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return []
    out, sec_glaz = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            sec_glaz = "[глаз]" in line
            continue
        m = re.match(r"^- ([РГ]\d+)\s+(.*)$", line)
        if not m or not (sec_glaz or "`[глаз]`" in m.group(2)):
            continue
        body = re.sub(r"`\[[^\]]*\]`", "", m.group(2)).strip()
        body = re.split(r" \(|;| — |\. ", body)[0].rstrip(" .")
        out.append(f"{m.group(1)} {body}"[:60].rstrip())
    return out


def _model_mass_grams(app, D, model: str) -> float:
    """TopPart.Mass модели. Открытую пользователем модель не закрывает; иначе
    открывает невидимой только для чтения и закрывает."""
    from win32com.client import CastTo
    d = D.find_doc(app, model)
    opened = d is None
    if opened:
        d = app.Documents.Open(model, False, True)
    try:
        return float(CastTo(d, "IKompasDocument3D").TopPart.Mass)
    finally:
        if opened:
            try:
                d.Close(KD_DO_NOT_SAVE_CHANGES)
            except Exception:
                pass


def _preview_paths(png: str, n_sheets: int) -> list:
    """Файлы растрового экспорта: один лист — png как есть; N листов — КОМПАС пишет
    «<имя>(1).png» … «<имя>(N).png» (замер 14d), png без номера не создаётся."""
    if n_sheets <= 1:
        return [png]
    stem, ext = os.path.splitext(png)
    return [f"{stem}({k}){ext}" for k in range(1, n_sheets + 1)]


def _sheet_preview(doc, D, png: str, max_px: int = PREVIEW_MAX_PX):
    """PNG КАЖДОГО листа через ksDocument2D.SaveAsToRasterFormat (Ruling 10:
    kompas_screenshot показывал устаревший кадр). Разрешение — чтобы длинная
    сторона листа была ≤ max_px; пути — новые (поверх PNG КОМПАС задаёт модальный
    «заменить?»). Возвращает ([пути по листам], [[ширина, высота], ...])."""
    D.uncur(doc)
    sheets = doc.LayoutSheets
    long_mm = min({3: 420.0, 4: 297.0}.get(int(sheets.Item(i).Format.Format), 420.0)
                  for i in range(sheets.Count))
    d5 = D.api5().ActiveDocument2D()
    p = d5.RasterFormatParam()
    p.Init()
    p.format = 3                     # PNG
    p.colorBPP = 24
    p.extResolution = int(max_px * 25.4 / long_mm)
    paths = _preview_paths(png, sheets.Count)
    for f in paths:
        if os.path.exists(f):
            os.remove(f)
    if not d5.SaveAsToRasterFormat(png, p) or not all(os.path.exists(f) for f in paths):
        raise RuntimeError("SaveAsToRasterFormat не записал файл(ы) " + ", ".join(paths))
    from PIL import Image
    sizes = []
    for f in paths:
        with Image.open(f) as im:
            im.load()
            size = list(im.size)
            if max(size) > max_px:
                im.thumbnail((max_px, max_px))
                im.save(f)
                size = list(im.size)
        sizes.append(size)
    return paths, sizes


def _check_json(text: str):
    """Последняя строка `JSON: {...}` ответа kompas_check_drawing; None — проверка не прошла."""
    last = (text or "").rstrip("\n").rsplit("\n", 1)[-1]
    if not last.startswith("JSON: "):
        return None
    return json.loads(last[6:])


_FAULT_STAGE = [None]     # инъекция сбоя для живой проверки путей отказа (только тесты)


def _fault(stage: str) -> None:
    if _FAULT_STAGE[0] == stage:
        raise RuntimeError(f"инъекция сбоя на этапе {stage} (проверка пути отказа)")


def _open_doc_keys(app) -> set:
    """(Name, PathName) открытых документов — чтобы после сбоя закрыть только свои."""
    keys = set()
    for i in range(app.Documents.Count):
        d = app.Documents.Item(i)
        if d is not None:
            keys.add((d.Name, d.PathName))
    return keys


def _close_new_docs(app, before: set) -> int:
    """Закрыть без сохранения документы, которых не было в before. Возвращает число."""
    n = 0
    for i in reversed(range(app.Documents.Count)):
        d = app.Documents.Item(i)
        if d is not None and (d.Name, d.PathName) not in before:
            d.Close(KD_DO_NOT_SAVE_CHANGES)
            n += 1
    return n


def _failure_result(stage: str, error: str, saved_file, closed: bool) -> dict:
    """Ответ kompas_build_drawing при исключении на этапе stage — тот же JSON, что и
    обычно (никогда не голое «Failed»): уровень call_returned, ошибка exception:<этап>,
    cdw — уже записанный файл (черновик/чистовой) или None, document_closed — документ
    сборки закрыт без сохранения."""
    kind = ("none" if not saved_file else
            "draft" if saved_file.lower().endswith(".draft.cdw") else "final")
    return {"cdw": saved_file, "saved": kind,
            "verification": {"level": "call_returned", "unverified_aspects": _glaz_aspects()},
            "lint": {"fails": None, "errors": [{"code": f"exception:{stage}", "object": stage,
                                                "fix_hint": error}]},
            "document_closed": bool(closed)}


_WARNING_CODES = ("build:title",)   # оформление «по возможности»: не блокирует сохранение


def _split_warnings(issues):
    """(ошибки, предупреждения): коды из _WARNING_CODES (заголовок вида не встал
    над размерами) — предупреждения, чистовой из-за них сохраняется."""
    warn = [e for e in issues if e.get("code") in _WARNING_CODES]
    return [e for e in issues if e.get("code") not in _WARNING_CODES], warn


def _dim_sides(spec, key) -> set:
    """Стороны вида key, где встанут линейные размеры spec (лесенка): side или
    умолчание layout — h снизу, v слева, без orient — обе."""
    out = set()
    for d in spec.get("dims") or []:
        if d["view"] != key or d["kind"] != "lin":
            continue
        if d.get("side"):
            out.add(d["side"])
        else:
            out |= {"h": {"bottom"}, "v": {"left"}}.get(d.get("orient"), {"bottom", "left"})
    return out


def _sheet_offset(spec, sheet) -> float:
    """Бумажная X начала листа sheet в общей СК чертежа: лист 2 — вплотную правее листа 1
    (замер 14d: А3 гориз. → 420). x видов в spec — в СК своего листа."""
    import drawing_rules
    if int(sheet or 1) == 1:
        return 0.0
    return drawing_rules.sheet_x_ranges([spec["format"]], [bool(spec.get("vertical"))])[0][1]


def _off_sheet_rect(rect, off, fmt=3, vertical=False):
    """Бумажный bbox вида (общая СК чертежа) против рамки листа, начинающегося с x=off:
    None — внутри (допуск 0,5 мм), иначе bbox в СК этого листа (для подсказки)."""
    import drawing_rules
    f = drawing_rules.sheet_frame(fmt, vertical)
    loc = [round(rect[0] - off, 1), round(rect[1], 1), round(rect[2] - off, 1), round(rect[3], 1)]
    inside = (loc[0] >= f[0] - 0.5 and loc[1] >= f[1] - 0.5
              and loc[2] <= f[2] + 0.5 and loc[3] <= f[3] + 0.5)
    return None if inside else loc


def _sync_sheet2_marking(doc, D):
    """Графа 2 штампа листа 2 (форма 2а) = отображаемое обозначение листа 1 (после
    D.stamp: spec.stamp.marking или поле модели). Пусто или не записалось — RuntimeError."""
    import drawing_rules
    sheets = doc.LayoutSheets
    m1 = drawing_rules.stamp_text(sheets.Item(0).Stamp.Text(2).Str or "").strip()
    if not m1:
        raise RuntimeError("обозначение в штампе листа 1 пусто — листу 2 (форма 2а) нечего "
                           "показать; задать spec.stamp.marking")
    st2 = sheets.Item(1).Stamp
    if drawing_rules.stamp_text(st2.Text(2).Str or "").strip() != m1:
        st2.Text(2).Str = m1
        st2.Update()
    got = drawing_rules.stamp_text(st2.Text(2).Str or "").strip()
    if got != m1:
        raise RuntimeError(f"графа 2 листа 2 «{got}» вместо «{m1}» — не записалась")
    return m1


def _sync_sheet2_cells(doc, D, cells=(1, 3, 9)):
    """Графы 1/3/9 листа 2 = отображаемые графы листа 1 (форма 2а их не показывает, но
    в данных документа лист 2 нёс графы модели — «Плита опорная», «Сталь 10» при штампе
    «Плита основания», «Сталь 40»; дамп 2026-10-15). Ошибка записи — не сбой сборки."""
    import drawing_rules
    sheets = doc.LayoutSheets
    st1, st2 = sheets.Item(0).Stamp, sheets.Item(1).Stamp
    done = {}
    for n in cells:
        val = drawing_rules.stamp_text(st1.Text(n).Str or "").strip()
        if not val:
            continue
        if drawing_rules.stamp_text(st2.Text(n).Str or "").strip() != val:
            try:
                st2.Text(n).Str = val
                done[n] = val
            except Exception:
                pass
    if done:
        st2.Update()
    return done


def _dims_error_hint(error: str) -> str:
    """Подсказка к ошибке размера: «нет места» в лесенке — по Р05 второй лист."""
    if "нет места" in (error or ""):
        return (error + " — вынести виды на лист 2 (views[].sheet=2) по Р05 "
                "(тесно — второй лист, а не уплотнение)")
    return error


HATCH_BIG = 40.0   # мм листа: сечение с меньшей стороной не мельче — шаг штриховки 4 вместо 2


def _build_sheet(D, spec, draft):
    """Виды → размеры/резьбы → выноски → Ra → ТТ → штамп. Возвращает
    (doc, {key: индекс вида}, ошибки построения). Падает только если не создан
    сам чертёж с главным видом."""
    from win32com.client import CastTo
    errors = []

    def err(code, obj, e):
        errors.append({"code": code, "object": obj,
                       "fix_hint": e if isinstance(e, str) else format_error(e)})

    s = _scale_value(spec["scale"])
    main = spec["views"][0]
    extra = [v for v in spec["views"][1:] if not v.get("type")]
    codes = [_projection_code(main["orient"], v["orient"]) for v in extra]
    # прочие виды модели — проекции главного (коды 3/4/5/6, создаются по порядку codes);
    # project_dims=False — размеры только из spec (Ruling 11: без 3D-аннотаций модели)
    doc, views, ok = D.new_drawing(draft, spec["model"], main["orient"], codes or [1],
                                   main["x"], main["y"], s, fmt=spec["format"],
                                   vertical=bool(spec.get("vertical", False)),
                                   project_dims=False,
                                   mass_grams=bool(spec.get("mass_grams", False)))
    if not ok or views.Count != 2 + len(extra):
        raise RuntimeError(f"AddStandartViews {main['orient']} {codes or [1]}: видов "
                           f"{views.Count - 1}, ждали {1 + len(extra)}")
    if any(v.get("sheet") == 2 for v in spec["views"]):
        try:     # лист 2: штамп формы 2а (обозначение + «Лист 2»), «Листов 2» — в штампе листа 1
            D.sheet2(doc, (spec.get("stamp") or {}).get("marking"),
                     spec.get("format2", spec["format"]))
        except Exception as e:
            err("build:sheet2", "лист 2", e)
    # ключ → индекс по ИЗМЕРЕННОЙ ориентации вида (порядок создания при нескольких
    # кодах не гарантирован): направление на зрителя из GetProjectionPoint осей модели
    measured = {i: _toward_from_axes(_view_axes(views.View(i))) for i in range(1, views.Count)}
    idx, verrs = _match_views({v["key"]: v["orient"] for v in [main] + extra}, measured)
    for e in verrs:
        err("build:view", "ориентация", e)
    if main["key"] not in idx:
        raise RuntimeError(f"главный вид {main['orient']} не опознан по ориентации: {verrs}")
    if main.get("rotate"):       # поворот без знака «повёрнуто» (Э7 plate_leap) — ДО размеров
        mv = views.View(idx[main["key"]])
        D.rotate_view(doc, mv, main["rotate"])
        # ProjectionMatrix поворачивает вид вокруг своей точки: начало модели уезжало
        # (пилот PRJ.02: (35; 228) → (−146,5; 216,5)) — вернуть его в точку spec x, y
        ok, ox, oy = CastTo(mv, "IAssociationView").GetProjectionPoint(0.0, 0.0, 0.0)
        if ok:
            mv.X += float(main["x"]) - (mv.X + ox * mv.Scale)
            mv.Y += float(main["y"]) - (mv.Y + oy * mv.Scale)
            mv.Update()
            D.rebuild(doc)
    for brk in main.get("break") or []:    # разрыв вида (ГОСТ 2.305 п. 6.3) — до размеров
        try:
            mv = views.View(idx[main["key"]])
            u1 = D.raw_projection(mv, *map(float, brk["p1"]))[0]
            u2 = D.raw_projection(mv, *map(float, brk["p2"]))[0]
            D.add_break(doc, mv, u1, u2, float(brk.get("gap", D.BREAK_GAP)))
        except Exception as e:
            err("build:break", main["key"], e)
    for v in extra:
        if v["key"] not in idx:
            continue
        try:              # место проекции — из spec (KOMPAS ставит её на dx/dy от главного)
            pv = views.View(idx[v["key"]])
            off = _sheet_offset(spec, v.get("sheet"))
            if off:       # на другом листе проекционная связь не держится — снять
                CastTo(pv, "IAssociationView").ProjectionLink = False
            pv.X, pv.Y = float(v["x"]) + off, float(v["y"])
            pv.Update()
            if abs(pv.X - v["x"] - off) > 0.05 or abs(pv.Y - v["y"]) > 0.05:
                raise RuntimeError(f"вид встал в ({pv.X - off:.1f}, {pv.Y:.1f}), а не в spec "
                                   f"({v['x']}, {v['y']}) — проекционная связь")
        except Exception as e:
            err("build:view", v["key"], e)
    crop_regions = {}                    # ключ вида → область местного вида (СК вида) для лесенки
    for v in spec["views"][1:]:          # разрезы и выносные — по порядку spec
        t = v.get("type")
        if not t:
            continue
        try:
            n0 = D.views_of(doc).Count
            parent = D.views_of(doc).View(idx[v["from"]])
            vs = _scale_value(v["scale"]) if v.get("scale") else s
            letter = v["key"].split("-")[0]
            off = _sheet_offset(spec, v.get("sheet"))     # лист 2: x — в СК листа 2
            if t == "section":
                (x1, y1), (x2, y2) = v["line"]
                cl = D.cutline(parent, x1, y1, x2, y2, letter)
                D.section_view(doc, cl, v["x"] + off, v["y"], vs, show_scale=abs(vs - s) > 1e-9)
                if v.get("crop"):     # частичный разрез: обрезать окружностью или прямоугольником
                    sv = D.views_of(doc).View(D.views_of(doc).Count - 1)    # (местный вид, Э2)
                    av = CastTo(sv, "IAssociationView")
                    c = v["crop"]
                    if c.get("rect"):   # прямой край по обрыву (секущая — на всю длину, Вал_v7)
                        (ok1, ax, ay), (ok2, bx, by) = (av.GetProjectionPoint(*map(float, q)) for q in c["rect"])
                        ok = ok1 and ok2
                        reg = [min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)]
                        ok = ok and D.local_view_rect(doc, sv, *reg)
                    else:
                        ok, cx, cy = av.GetProjectionPoint(*map(float, c["center"]))
                        rr = float(c["r"])
                        reg = [cx - rr, cy - rr, cx + rr, cy + rr]
                        ok = ok and D.local_view(doc, sv, cx, cy, rr)
                    if not ok:
                        err("build:crop", v["key"], "местный вид разреза не создан")
                    else:     # заголовок — над видимым фрагментом, а не над всем разрезом
                        crop_regions[v["key"]] = reg
                        try:
                            sv = D.views_of(doc).View(D.views_of(doc).Count - 1)
                            pcx, pcy = D.view_to_paper(sv, (reg[0] + reg[2]) / 2, reg[3])
                            D.place_view_title(doc, D.views_of(doc).Count - 1, pcx, pcy + 4.0)
                        except Exception as e:
                            err("build:crop", v["key"], f"заголовок не перенесён: {e}")
                if v.get("wave"):     # обрыв разреза по концу секущей — волнистая линия (ГОСТ 2.305)
                    sv = D.views_of(doc).View(D.views_of(doc).Count - 1)
                    ok, wx, wy = CastTo(sv, "IAssociationView").GetProjectionPoint(*map(float, v["wave"]["at"]))
                    if not ok:
                        err("build:wave", v["key"], f"точка {v['wave']['at']} не спроецирована")
                    else:     # в разрезе вдоль оси X модели вертикаль вида — Y модели (опыт probe_B)
                        D.wave_line(sv, wx, wy, float(v["wave"]["half"]))
            else:
                (xc, yc), ps = v["center"], parent.Scale
                rect = v.get("form") == "rect"
                # эквивалентный радиус зоны (для полки буквы): полудиагональ прямоугольника
                r = (math.hypot(float(v["w"]), float(v["h"])) / 2 if rect else float(v["r"]))
                if v.get("shelf"):
                    sx, sy = map(float, v["shelf"])
                else:   # буква не на контуре/штриховке родителя (задача 14c)
                    sx, sy = xc + r + 5.0 / ps, yc + r + 5.0 / ps
                    try:
                        pc = D.view_to_paper(parent, xc, yc)
                        segs = D.view_segments(parent)   # bbox — по ним: у view_content дуги целыми кругами
                        bb = (min(min(q[0], q[2]) for q in segs), min(min(q[1], q[3]) for q in segs),
                              max(max(q[0], q[2]) for q in segs), max(max(q[1], q[3]) for q in segs))
                        sp = D.detail_shelf(pc[0], pc[1], r * ps, segs, bb,
                                            avoid=_dim_sides(spec, v["from"]))
                        sx, sy = D.paper_to_view(parent, *sp)
                    except Exception:
                        pass       # по возможности: остаётся прежнее умолчание
                if rect:     # прямоугольная зона (Э5 plate_leap): Width/Height — мм листа родителя
                    rem = D.remote(parent, xc, yc, r * ps, sx, sy, letter, form="rect",
                                   w_paper=float(v["w"]) * ps, h_paper=float(v["h"]) * ps)
                else:
                    rem = D.remote(parent, xc, yc, r * ps, sx, sy, letter)
                # x,y выносного — бумажная точка ЦЕНТРА элемента (геометрия — в СК родителя)
                D.detail_view(doc, rem, v["x"] + off - xc * vs, v["y"] - yc * vs, vs,
                              show_scale=abs(vs - s) > 1e-9)
            n1 = D.views_of(doc).Count
            if n1 != n0 + 1:
                raise RuntimeError(f"видов было {n0}, стало {n1} — вид не добавлен")
            idx[v["key"]] = n1 - 1
            if v.get("title_prefix"):   # «2 места А (1:5)» (Э6 plate_leap: IView.Name)
                try:
                    D.title_prefix(D.views_of(doc).View(n1 - 1), v["title_prefix"])
                except Exception as e:
                    err("build:title_prefix", v["key"], e)
        except Exception as e:
            err("build:view", v["key"], e)

    import drawing_rules
    frame2 = drawing_rules.sheet_frame(spec.get("format2", spec["format"]))
    for v in spec["views"]:      # виды листа 2 — внутри его рамки (x,y spec — в СК листа 2)
        if v.get("sheet") == 2 and v["key"] in idx:
            try:
                r = D.view_rect(D.views_of(doc).View(idx[v["key"]]))
                loc = r and _off_sheet_rect(r, _sheet_offset(spec, 2),
                                            spec.get("format2", spec["format"]))
                if loc:
                    err("build:sheet2_bounds", v["key"],
                        f"вид {loc} вне рамки листа 2 {list(frame2)} (СК листа 2, мм) — "
                        "сдвинуть x,y вида или разнести виды")
            except Exception as e:
                err("build:sheet2_bounds", v["key"], e)

    # размеры и резьбы — kompas_place_dimensions по видам (лесенка, проекция точек модели)
    by_view = {}
    for i, it in enumerate(spec.get("dims") or []):
        d = {k: val for k, val in it.items() if k != "view"}
        d["id"] = it.get("id") or f"dims{i}"
        by_view.setdefault(it["view"], []).append(d)
    for i, it in enumerate(spec.get("threads") or []):
        d = {"id": it.get("id") or f"threads{i}", "kind": "dia", "ref": it["ref"],
             "thread": {k: it[k] for k in ("text", "depth", "drill_d", "drill_depth") if k in it}}
        if "angle" in it:
            d["angle"] = it["angle"]
        by_view.setdefault(it["view"], []).append(d)
    place = _unwrapped("kompas_place_dimensions")
    for key, items in by_view.items():
        if key not in idx:
            err("build:dims", key, "вид не построен — размеры не ставились")
            continue
        if key in crop_regions:
            items = [dict(it, region=crop_regions[key]) for it in items]
        r = place(document=draft, view=idx[key], dims=json.dumps(items, ensure_ascii=False))
        if r.startswith(_ERR_PREFIXES):
            err("build:dims", key, r)
            continue
        for e in json.loads(r)["errors"]:
            err(e.get("code", "build:dims"), f"{key}.{e['id']}", _dims_error_hint(e["error"]))

    views = D.views_of(doc)
    for i, it in enumerate(spec.get("leaders") or []):
        try:
            v = views.View(idx[it["view"]])
            at = [float(c) for c in it["at"]]
            if len(at) == 3:
                ok, xt, yt = D.project(v, *at)
                if not ok:
                    raise ValueError(f"точка модели {at} не спроецирована")
            else:
                xt, yt = at
            if it.get("shelf"):
                x0, y0 = it["shelf"]
            else:      # полка над видом выше лесенки размеров side=top (10 мм + 8 мм на размер)
                n_top = sum(1 for d in spec.get("dims") or []
                            if d["view"] == it["view"] and d.get("side") == "top")
                bb, sv = D.view_content(v)["bbox"], v.Scale
                x0, y0 = xt + 10.0 / sv, (bb[3] if bb else yt) + (10.0 + 8.0 * n_top) / sv
            D.leader(v, x0, y0, xt, yt, it["text"], under=it.get("under"), arrow=it.get("arrow"))
        except Exception as e:
            err("build:leader", f"leaders[{i}]", e)

    def view_point(v, at):
        """[x,y] СК вида или [x,y,z] точка модели → СК вида."""
        at = [float(c) for c in at]
        if len(at) == 3:
            ok, x, y = D.project(v, *at)
            if not ok:
                raise ValueError(f"точка модели {at} не спроецирована")
            return x, y
        return at[0], at[1]

    for i, it in enumerate(spec.get("bases") or []):       # знаки баз (ГОСТ 2.308)
        try:
            v = views.View(idx[it["view"]])
            x0, y0 = view_point(v, it["at"])
            D.base_sign(v, x0, y0, float(it["frame"][0]), float(it["frame"][1]), it["letter"])
        except Exception as e:
            err("build:base", f"bases[{i}]", e)
    for i, it in enumerate(spec.get("gdt") or []):         # рамки допусков на выносках
        try:
            v = views.View(idx[it["view"]])
            xt, yt = view_point(v, it["at"])
            D.tolerance(v, float(it["frame"][0]), float(it["frame"][1]), sign=it["sign"],
                        value=it["value"], prefix_sign=2 if it.get("dia_sign") else 0,
                        base_pos=int(it.get("base_pos", 6)), branch=(xt, yt), base=it.get("base"))
        except Exception as e:
            err("build:gdt", f"gdt[{i}]", e)
    for i, it in enumerate(spec.get("roughs") or []):      # Ra на поверхностях (ГОСТ 2.309)
        try:
            v = views.View(idx[it["view"]])
            x, y = view_point(v, it["at"])
            D.rough_sign(v, x, y, it["ra"], shelf=view_point(v, it["shelf"]) if it.get("shelf") else None)
        except Exception as e:
            err("build:rough", f"roughs[{i}]", e)

    for v in [main] + extra:        # ГОСТ 2.303/2.305: оси симметрии и центры проекций (Ч16)
        if v["key"] in idx:         # после размеров: лесенки считаются от контура, не от осей
            try:
                pv = D.views_of(doc).View(idx[v["key"]])
                D.add_symmetry_axes(pv)
                if v.get("axes"):    # принудительные оси (плита с одним несимметричным отверстием)
                    D.force_axes(pv, v["axes"])
                D.add_centre_marks(pv)
            except Exception as e:
                err("build:axes", v["key"], e)
    for v in spec["views"][1:]:     # выносные и сечения: кресты на окружностях (эталон плиты, Э11; ревьюер вала)
        if v.get("type") in ("detail", "section") and v["key"] in idx:
            try:
                if v.get("type") == "section" and v.get("axes"):   # ось симметрии сечения (ревьюер .008)
                    D.force_axes(D.views_of(doc).View(idx[v["key"]]), v["axes"])
                D.add_centre_marks(D.views_of(doc).View(idx[v["key"]]))
            except Exception as e:
                err("build:axes", v["key"], e)
    for v in spec["views"][1:]:     # крупное сечение: шаг штриховки 4 мм — центровые видны (ГОСТ 2.306)
        if v.get("type") == "section" and v["key"] in idx:
            try:
                pv = D.views_of(doc).View(idx[v["key"]])
                r = D.view_rect(pv)
                if r and min(r[2] - r[0], r[3] - r[1]) >= HATCH_BIG:
                    D.hatch_step(pv, 4.0)
            except Exception as e:
                err("build:hatch", v["key"], e)
    for key, i in idx.items():      # выступ осевых и центров КОМПАС — 3 мм (умолчание 2)
        try:
            D.set_axis_jut(D.views_of(doc).View(i))
        except Exception as e:
            err("build:axes", key, e)

    for v in spec["views"][1:]:     # заголовок разреза/выносного — над его размерами, ≥3 мм
        if v.get("type") and v["key"] in idx:
            try:
                D.lift_view_title(doc, idx[v["key"]])
            except Exception as e:
                err("build:title", v["key"], e)

    if spec.get("ra") and spec.get("ra_form") == "spec":
        try:     # штатный знак ISpecRough «√Ra … (√)» — как в эталоне инженера (spec от composer)
            D.spec_rough(doc, spec["ra"], add_sign=True)
        except Exception as e:
            err("build:ra", "Ra", e)
    elif spec.get("ra"):
        try:     # Ra — ручной знак (закон пользователя), правый верхний угол листа 1
            f = doc.LayoutSheets.Item(0).Format
            short, long_ = {2: (420.0, 594.0), 3: (297.0, 420.0), 4: (210.0, 297.0)}[int(f.Format)]
            w, h = (short, long_) if f.VerticalOrientation else (long_, short)
            D.radical(doc, w - 48.0, h - 23.0, spec["ra"])
        except Exception as e:
            err("build:ra", "Ra", e)
    if spec.get("tt"):
        try:
            D.tech_req(doc, spec["tt"])
        except Exception as e:
            err("build:tt", "ТТ", e)
    try:
        D.stamp(doc, **(spec.get("stamp") or {}))
    except Exception as e:
        err("build:stamp", "штамп", e)
    if doc.LayoutSheets.Count > 1:
        try:      # форма 2а листа 2: то же обозначение, что на листе 1 (явно, не молча)
            _sync_sheet2_marking(doc, D)
            _sync_sheet2_cells(doc, D)
        except Exception as e:
            err("build:sheet2", "лист 2, графа 2", e)
    try:      # неметалл (материал штампа, в т.ч. spec.stamp.material) — штриховка «в клетку»
        import drawing_rules
        mat = ((spec.get("stamp") or {}).get("material")
               or drawing_rules.stamp_text(doc.LayoutSheets.Item(0).Stamp.Text(3).Str or ""))
        if drawing_rules.is_nonmetal(mat.strip(), drawing_rules.load_rules()):
            for v in spec["views"][1:]:
                if v.get("type") and v["key"] in idx:
                    D.hatch_style(D.views_of(doc).View(idx[v["key"]]), 1)
    except Exception as e:
        err("build:hatch", "штриховка неметалла", e)
    for v in spec["views"][1:]:     # «2 места»: высоту заголовка сбрасывает одна из операций выше
        if v.get("title_prefix") and v["key"] in idx:
            try:
                D.set_title_height(D.views_of(doc).View(idx[v["key"]]))
            except Exception as e:
                err("build:title_prefix", v["key"], e)
    return doc, idx, errors


@mcp.tool()
def kompas_build_drawing(spec_path: str) -> str:
    """Построить ВЕСЬ чертёж детали по drawing_spec.json одним вызовом: LLM пишет
    решения (виды, что образмерить, ТТ), код — раскладку, проверку и сохранение.
    Формат spec — schemas/drawing_spec.schema.json, пример —
    schemas/examples/spacer.json; ref размеров — как у kompas_place_dimensions.

    Порядок: validate_spec (ДО КОМПАС — при ошибке документ не создаётся) →
    чертёж <имя>.draft.cdw: главный вид (views[0]), прочие виды модели — его проекции
    (AddStandartViews, коды 3/4/5/6 по orient), переставленные в x,y; разрезы (cutline + section_view) и
    выносные (remote + detail_view) → размеры и резьбы (kompas_place_dimensions по
    видам) → выноски → заголовки разрезов/выносных над их размерами (≥3 мм) → знак Ra (ручной радикал) → ТТ → штамп (spec.stamp перебивает графы 1/2/3) →
    материал штампа — неметалл проекта: штриховка разрезов/выносных «в клетку» (ГОСТ 2.306) →
    kompas_check_drawing → сверка массы штампа с моделью (mass_mismatch) →
    если ошибок нет: SaveAs в spec.cdw, повторная проверка (свежий вердикт для хука),
    черновик удаляется; иначе остаётся черновик → <имя>.state.json (kompas_snapshot) →
    превью PNG каждого листа (растровый экспорт, ≤1400 px). Всё под HideMessage=1.
    Листы (задача 14d): views[].sheet = 2 — вид на листе 2 (D.sheet2: штамп формы 2а,
    формат format2 или format), x,y — в СК своего листа; Ra и ТТ — только лист 1.

    Ответ — JSON: {"cdw": сохранённый файл, "saved": "final"|"draft",
    "verification": {"level": "call_returned" (проверка не прошла) |
    "geometry_checked" (есть ошибки) | "lint_clean" (0 ошибок, масса верна,
    чистовой сохранён), "unverified_aspects": [глаз]-пункты rules.md},
    "lint": {"fails": FAIL проверки, "errors": [{code, object, fix_hint}]} (+ build:* —
    не построенное по spec, mass_mismatch), "warnings": [то же] — не блокируют сохранение
    (build:title — заголовок разреза/выносного не встал над размерами), "views": {key: [индекс, имя]},
    "mass", "snapshot_path", "preview" (PNG листа 1), "previews" (PNG всех листов),
    "elapsed_s"}; у вида в "views" — [индекс, имя, лист]. Ключи видов сопоставляются
    индексам по ИЗМЕРЕННОЙ ориентации (GetProjectionPoint осей), не по порядку создания.
    Чертёж остаётся открытым: точечно править — kompas_place_dimensions(view=индекс).
    Исключение на этапе (build/check/save/snapshot) — тот же JSON с level
    call_returned, ошибкой exception:<этап>, document_closed: документ сборки закрыт
    без сохранения, уже записанный файл (cdw) остаётся. Масса модели не прочитана —
    ошибка mass_check в списке, не обрыв. «Failed: …» — только до создания документа
    (spec не прочитана/не прошла проверку, файл уже открыт).

    НЕ в BATCHABLE: операция над целым документом (создаёт, проверяет, сохраняет),
    внутри пакета её ошибки и время смешались бы с соседними действиями.
    """
    t0 = time.perf_counter()
    try:
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as e:
        return f"Failed: spec не прочитана ({spec_path}). {format_error(e)}"
    errs = validate_spec(spec)
    if errs:
        return ("Failed: spec не прошла проверку — КОМПАС не трогался, документ не создавался.\n"
                + "\n".join(f"  - {e}" for e in errs))
    spec = dict(spec, model=_long_path(spec["model"]), cdw=_long_path(spec["cdw"]))
    cdw = spec["cdw"]
    draft, state_path = _spec_paths(cdw)
    with com_context():
        app, before, stage, final = None, None, "connect", False
        try:
            import drawing_rules
            from win32com.client import CastTo
            D = _load_draw()
            app = get_app7()
            for p in (cdw, draft):
                if D.find_doc(app, p) is not None:
                    return f"Failed: {p} уже открыт в КОМПАС — закрой его и повтори сборку."
            before = _open_doc_keys(app)
            with D.quiet(app):
                stage = "build"
                _fault(stage)
                doc, idx, errors = _build_sheet(D, spec, draft)
                errors, warnings = _split_warnings(errors)
                doc.Save()

                stage = "check"
                _fault(stage)
                lint = _check_json(_unwrapped("kompas_check_drawing")(document=draft))
                if lint is not None:
                    errors = lint["errors"] + errors
                    warnings = warnings + list(lint.get("warnings") or [])

                stage = "mass"
                st = doc.LayoutSheets.Item(0).Stamp
                stamp_mass = drawing_rules.stamp_text(st.Text(5).Str or "").strip()
                model_g, mass_ok = None, False
                try:     # модель не открылась — ошибка в списке, не обрыв сборки
                    unit = CastTo(doc.DocumentSettings, "IDrawingDocumentSettings").SheetStampMassUnit
                    _fault(stage)
                    model_g = _model_mass_grams(app, D, spec["model"])
                    mass_ok, mass_why = _mass_check(stamp_mass, model_g, unit)
                    if not mass_ok:
                        errors.append({"code": "mass_mismatch", "object": "штамп, графа 5",
                                       "fix_hint": mass_why + " — пересохранить модель (масса) или "
                                       "перезапустить КОМПАС (сбой округления), собрать заново"})
                except Exception as e:
                    errors.append({"code": "mass_check", "object": spec["model"],
                                   "fix_hint": "масса модели не прочитана: " + format_error(e)})

                stage = "save"
                saved = draft
                if lint is not None and not errors:
                    doc.SaveAs(cdw)
                    saved, final = cdw, True
                    lint = _check_json(_unwrapped("kompas_check_drawing")(document=cdw))
                    if lint is None or lint["errors"]:
                        errors = (lint or {}).get("errors") or [
                            {"code": "check", "object": cdw, "fix_hint": "повторная проверка не прошла"}]
                    for junk in (draft, draft + ".bak"):     # черновик и его .bak от КОМПАС
                        try:
                            os.remove(junk)
                        except Exception:
                            pass

                stage = "snapshot"
                _fault(stage)
                snap = _unwrapped("kompas_snapshot")(document=saved)
                snapshot_path = None
                if not snap.startswith(_ERR_PREFIXES):
                    with open(state_path, "w", encoding="utf-8") as f:
                        f.write(snap)
                    snapshot_path = state_path
                previews, sizes, preview_err = [], [], None
                try:      # сбой превью — не ошибка чертежа, уровень не понижает
                    previews, sizes = _sheet_preview(
                        doc, D, os.path.splitext(cdw)[0] + time.strftime(".preview_%Y%m%d_%H%M%S.png"))
                except Exception as e:
                    preview_err = format_error(e)

            level = ("call_returned" if lint is None else
                     "lint_clean" if final and not errors else "geometry_checked")
            names = D.views_of(doc)
            sheet_of = {v["key"]: v.get("sheet", 1) for v in spec["views"]}
            return json.dumps({
                "cdw": saved, "saved": "final" if final else "draft",
                "verification": {"level": level, "unverified_aspects": _glaz_aspects()},
                "lint": {"fails": (lint or {}).get("fails"), "errors": errors},
                "warnings": warnings,
                "views": {k: [i, names.View(i).Name, sheet_of.get(k, 1)] for k, i in idx.items()},
                "mass": {"stamp": stamp_mass,
                         "model_g": None if model_g is None else round(model_g, 3), "ok": mass_ok},
                "snapshot_path": snapshot_path,
                "preview": previews[0] if previews else None,       # лист 1 (как раньше)
                "previews": previews, "preview_px": sizes[0] if sizes else None,
                **({"preview_error": preview_err} if preview_err else {}),
                "elapsed_s": round(time.perf_counter() - t0, 1)}, ensure_ascii=False)
        except Exception as e:
            # сбой этапа: созданные этим вызовом документы закрыть БЕЗ сохранения,
            # уже записанный файл остаётся и возвращается
            closed = False
            if app is not None and before is not None:
                try:
                    with D.quiet(app):
                        _close_new_docs(app, before)
                    closed = True
                except Exception:
                    closed = False
            done = cdw if final and os.path.exists(cdw) else (draft if os.path.exists(draft) else None)
            res = _failure_result(stage, format_error(e), done, closed)
            res["elapsed_s"] = round(time.perf_counter() - t0, 1)
            return json.dumps(res, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
