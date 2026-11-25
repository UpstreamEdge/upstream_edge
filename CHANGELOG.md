# Changelog

All notable changes to `upstream-edge` are documented in this file.


## 1.3.0
- Writes now show in Obsidian's change history.
  `Database.open(path, user="...")` names who; `change_log=False` turns it off
- New `read_only=True` for scripts that only read, and `snapshot()` to save a
  restore point Obsidian lists as a version
- New readers `run_settings()`, `type_curves()` and `model_field_refs()`, and
  `set_run_settings(name, effective_date=...)`
- `forecasts()` also returns the stored monthly volumes of a saved Spatial
  Forecasting run, as rows with `monthly_volumes`; check `row.kind`
- Econ model fields linked to a well attribute stay linked through model,
  well and attribute edits
- Clearer field names: `spud_date`, `completion_date`,
  `first_production_date`, `bottom_hole_latitude`, `bottom_hole_longitude`,
  `expense_model`, `ad_valorem_tax`. The old names still work in code;
  DataFrame columns carry the new ones
- Writers refuse input Obsidian could not read back: rates and costs that are
  not numbers, dates that are not dates, well ids over 16 bytes

## 1.2.1
- Fix `add_well` and other well writers on databases missing newer tables
- New `MissingTableError` for a call that needs a table the database does not
  have yet; it says to open the file once in Obsidian

## 1.2.0
- Add `RsvCat` `PDNP` variant
- Improve default economic model assignment/handling

## 1.1.1
- Clearer docs on model assignment: forecast and price models are scenario-global
  (set with `set_scenario`), while expense, tax, diff, and shrink/yield are
  per-well (set with `set_well_models`). README, `AGENTS.md`, and method
  docstrings now spell out the distinction.

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
