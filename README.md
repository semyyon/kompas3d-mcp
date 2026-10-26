# kompas3d-mcp

[![CI](https://github.com/semyyon/kompas3d-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/semyyon/kompas3d-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Desktop, Claude Code and
any other MCP client) drive **KOMPAS-3D** through its COM API: open and save documents, build parts,
assemble and move components, measure models, and generate fully dimensioned drawings.

> Русская версия: [README.ru.md](README.ru.md)

> **Unofficial project.** KOMPAS-3D is a product and trademark of ASCON. This repository is not affiliated
> with or endorsed by ASCON and contains no ASCON code or SDK files.

## Who is it for

Mechanical designers and CAD automation engineers who already work in KOMPAS-3D and want an assistant that
can do the repetitive part of the job (inserting 50 fasteners, checking a bill of materials, producing a
first draft of a drawing) while every result is verified with numbers (bounding box, mass, solid count)
rather than taken on faith.

## Requirements

- Windows 10/11
- KOMPAS-3D **v23** installed (the only version this was developed and tested against)
- Python 3.10 or newer (developed on 3.12)
- Python packages from [requirements.txt](requirements.txt): `mcp` (1.x), `pywin32`, `Pillow`, `numpy`, `jsonschema`

The server talks to a running KOMPAS-3D through COM, so it must run on the same Windows machine and
under the same user as KOMPAS.

## Installation

```powershell
git clone https://github.com/semyyon/kompas3d-mcp.git
cd kompas3d-mcp
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Check that the server starts (it does not launch KOMPAS by itself):

```powershell
.venv\Scripts\python -c "import server; print(server.kompas_status())"
```

## Connect it to an MCP client

All clients need the same thing: a command that starts `server.py` over stdio.
Use absolute paths; the examples assume the repository is in `C:\tools\kompas3d-mcp`.

**Claude Desktop** — edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "kompas3d": {
      "command": "C:\\tools\\kompas3d-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\tools\\kompas3d-mcp\\server.py"]
    }
  }
}
```

**Claude Code**:

```powershell
claude mcp add kompas3d -- C:\tools\kompas3d-mcp\.venv\Scripts\python.exe C:\tools\kompas3d-mcp\server.py
```

**Other clients** (Cursor, Cline, ...) accept the same `command` / `args` pair in their MCP settings.

Optional environment variables:

| Variable | Purpose |
|---|---|
| `KOMPAS_BIN` | Folder with KOMPAS type libraries (default `C:\Program Files\ASCON\Bin`) |
| `KOMPAS_SDK_HELP_ZIP` | Path to the KOMPAS SDK help archive, enables offline `kompas_sdk_search` |
| `KOMPAS_STUBS_DIR` | Folder with SDK `.pyi` stubs for richer `kompas_api` hints (not shipped here) |
| `KOMPAS_RULES_MD` | Markdown checklist of visual drawing-review items (not shipped here) |

## Tools

| Tool | What it does |
|---|---|
| `kompas_status` | Tells whether KOMPAS is running and lists open documents (does not start it) |
| `kompas_launch` | Starts KOMPAS or attaches to a running instance |
| `kompas_create_document` | Creates a part, assembly, drawing, fragment, specification or text document |
| `kompas_open_document` | Opens `.m3d`, `.a3d`, `.cdw` and other KOMPAS files |
| `kompas_save_document` | Save / Save As; `.step`, `.stl`, `.pdf`, `.dxf` extensions convert |
| `kompas_close_document` | Closes a document by path or name fragment |
| `kompas_backup` | Makes a `<name>_backup.<ext>` file copy next to the document |
| `kompas_screenshot` | PNG screenshot of the KOMPAS window |
| `kompas_view` | Sets standard projections and captures them in one call |
| `kompas_export_raster` | Exports the 3D model to a clean PNG/JPG/BMP render |
| `kompas_auto_dialogs` | Auto-answers KOMPAS pop-up dialogs that would block COM calls |
| `kompas_get_tree` | Component tree of an assembly with world-space origins and grouped duplicates |
| `kompas_get_bbox` | Bounding box of a document or of a component found by name |
| `kompas_measure` | Numeric self-check: bounding box, mass, faces, edges, solids, properties |
| `kompas_insert` | Inserts components into an assembly as external references, in verified batches |
| `kompas_move` | Moves top-level components and verifies the resulting displacement |
| `kompas_part_props` | Sets name, marking, material and colour of a part |
| `kompas_shape` | Builds one solid operation (base, boss, cut, revolve) from a JSON sketch |
| `kompas_feature` | Chamfer, fillet or cosmetic thread with edge selection by points |
| `kompas_variables` | Reads, adds and sets model variables, then rebuilds and checks |
| `kompas_audit_fasteners` | Groups and counts fasteners in an assembly and checks engagement |
| `kompas_interference` | Detects real solid-body intersections (not just bounding boxes) |
| `kompas_bom` | Reads the bill of materials of an assembly (read-only) |
| `kompas_model_summary` | Geometry summary of a part used to plan a drawing (read-only) |
| `kompas_snapshot` | Compact JSON snapshot of a drawing: title block, views, dimensions |
| `kompas_place_dimensions` | Places dimensions on a view: the model decides what, the code decides where |
| `kompas_check_drawing` | Checks an open drawing against project and ESKD rules |
| `kompas_build_drawing` | Builds a whole part drawing from a `drawing_spec.json` in one call, saving only if checks pass |
| `kompas_api` | Gateway to the whole COM API (API7 and API5) without writing a script, with name validation |
| `kompas_sdk_search` | Offline search through the KOMPAS SDK help (interfaces, methods, properties) |
| `kompas_run_job` | Runs a named script from `jobs/` with JSON parameters |
| `kompas_run_script` | Executes arbitrary Python against the live COM API (last resort) |
| `kompas_batch` | Runs a chain of `kompas_*` calls in one round trip under a single lock |

## Example requests

- "Open `Bracket.m3d`, set material to Steel 12Kh18N10T, and report bounding box, mass and solid count."
- "Insert `M6x20.m3d` 24 times into the open assembly at these coordinates, then show me the fastener audit."
- "Check the assembly for solid interferences and list the colliding pairs."
- "Build a drawing for `Plate.m3d` from this spec and fix whatever `kompas_check_drawing` reports."
- "Which KOMPAS API method sets the depth of an extrusion? Search the SDK help."

## How it works

- `server.py` is a [FastMCP](https://github.com/modelcontextprotocol/python-sdk) server over stdio.
- It connects to KOMPAS through `pywin32` using both COM APIs: API7 (`IApplication`) and API5 (`KompasObject`).
- KOMPAS has a single COM instance, so all access is guarded by an inter-process lock;
  `kompas_batch` holds the lock for a whole chain of calls.
- Mutating tools accept `document=` so an edit cannot land in whatever document KOMPAS made active.
- `jobs/` holds named scripts for `kompas_run_job`; `jobs/_draw.py` is the 2D drawing engine.
- Drawings are driven by a JSON spec ([schema](schemas/drawing_spec.schema.json),
  [examples](schemas/examples)); `drawing_rules.py` implements the checks and `project_rules.json` holds
  the tunable thresholds and standard texts.
- `hooks/` contains optional Claude Code hooks, for example one that refuses to save an assembly without a fresh backup.
- Every call is timed and appended to `logs/calls.jsonl`.

## Limitations

- Windows and KOMPAS-3D v23 only. Other versions may work but are untested.
- Tool descriptions are partly in Russian, and the drawing rules follow Russian ESKD/GOST conventions.
  `project_rules.json` contains the author's defaults; edit it for your own standards.
- The SDK stub files and the SDK help archive belong to ASCON and are not included.
  Without them `kompas_api` falls back to your installed type libraries and `kompas_sdk_search` needs `KOMPAS_SDK_HELP_ZIP`.
- Some KOMPAS operations open modal dialogs that block COM; see `kompas_auto_dialogs`.
- `kompas_run_script` runs arbitrary code with your user rights. Only connect clients you trust.

## Development

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The test suite does not need KOMPAS; tests that do are skipped automatically. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgements

Built on the [Model Context Protocol](https://modelcontextprotocol.io) and its Python SDK, and on
[pywin32](https://github.com/mhammond/pywin32). Developed together with Claude.

## License

[MIT](LICENSE)
