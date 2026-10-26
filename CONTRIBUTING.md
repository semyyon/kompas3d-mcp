# Contributing

Thanks for your interest. Issues and pull requests are welcome.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Tests must pass without KOMPAS-3D installed. A test that needs the real application or SDK files
must skip itself (`unittest.skipUnless(...)`).

## Guidelines

- Keep a change focused: one fix or feature per pull request.
- Add or update a test for new behaviour, preferably one that needs no KOMPAS.
- Do not commit KOMPAS models or drawings, SDK files, or documents that belong to someone else.
  Test fixtures must be anonymised: no real file paths, company or customer names.
- Tools that change a document should take a `document=` argument and verify their result with numbers.
- Describe what you tested in the pull request, including the KOMPAS version if you ran it for real.

## Reporting bugs

Open an issue with the KOMPAS-3D version, Python version, the tool call, and the error text.
For security problems see [SECURITY.md](SECURITY.md).
