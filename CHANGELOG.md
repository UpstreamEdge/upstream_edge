# Changelog

All notable changes to `upstream-edge` are documented in this file.


## 1.1.0
- Readers now handle every native data format, including `YYYYMM` production
  months, uppercase forecast phases, type-curve forecast segments, and blank
  optional values
- Interest values (`wi_pct`, `nri_pct`) are percentages from 0 to 100
  (e.g. 75.0 for 75%), matching Obsidian
- Every `delete_*` method now takes `confirm=True`, so destructive calls are
  always explicit
- Clearer validation: enum parameters are checked up front, and data issues
  raise `DataIntegrityError` naming the exact column
- `Database.open()` raises `FileNotFoundError` for missing paths instead of
  creating an empty file

## 1.0.2
- Bundle `AGENTS.md` (the AI-agent orientation guide) inside the wheel at `upstream_edge/obsidian_db/AGENTS.md`, so it ships with `pip install`
- Add an AI-agent pointer to the package docstring naming the bundled guide and its GitHub location
- Promote the `AGENTS.md` callout in the README intro

## 1.0.1
- Version bump for the first PyPI publish; no code changes

## 1.0.0
- Initial version
