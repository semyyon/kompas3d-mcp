# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-26

First public release.

### Added
- MCP server for KOMPAS-3D v23 over stdio with 33 tools: documents, tree and measurements,
  assembly insert/move, part properties, solid features, interference and BOM checks,
  `kompas_batch` for chains of calls, and a cross-process lock around COM.
- Drawing pipeline: `kompas_snapshot`, `kompas_place_dimensions`, `kompas_check_drawing`,
  `kompas_build_drawing` driven by a JSON spec with a schema and examples.
- Offline SDK help search and a COM API gateway with name validation.
- Optional Claude Code hooks (`hooks/`).
- Test suite that runs without KOMPAS; GitHub Actions workflow on Windows.
