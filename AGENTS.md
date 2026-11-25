# AI Agent Guide

## Orientation
`upstream_edge.obsidian_db` is a pure-Python client for Obsidian SQLite databases. It loads and reads data; forecasting and economic calculations happen in Obsidian, not in this package. Every reader has a `_df` sibling that returns a pandas DataFrame — install `upstream-edge[pandas]` to use them.

## Domain Glossary
`PropID` is the primary well identifier used by the library. `API10` is an optional ten-digit regulatory identifier and may map to more than one PropID.

`RsvCat` is the reserve category stored on each well: PDP, PDNP, ShutIn, DUC, PUD, PROB, POSS, LOC, TA, P&A, SWD, Blank, Data.

A scenario groups forecast and economic-model assignments. `MAIN` is the default scenario created by Obsidian workflows.

Price models are global commodity price decks. Expense, tax, differential, and shrink/yield models are shared, named models that wells reference through scenarios via `set_well_models`.

Well attributes are user-defined per-well columns.

Run settings (`run_settings()`) hold what an economics run is made with: the effective date, the cutoff, the abandonment delay, the after-tax choices. Only the effective date can be set (`set_run_settings(name, effective_date=...)`, the first day of a month).

Type curves (`type_curves()`) are read-only: one row per curve and phase, with rates at `normalization_lateral_length_ft`.

Interest values (`wi_pct`, `nri_pct`) are percentages from 0 to 100, e.g. 75.0 for 75%.

## Always Do / Never Do
- Always open with `Database.open(path, read_only=True)` when the task only reads. It cannot change the file or take a write lock on it.
- Always wrap multi-row writes in `db.transaction()`.
- Always pass `confirm=True` explicitly when calling destructive methods.
- Always call writer methods by keyword past the first positional identifiers.
- Always pass enum values, not their string equivalents (`RsvCat.PUD`, `Phase.OIL`) — writers reject raw strings.
- Never open the SQLite file with `sqlite3.connect()` directly for writes; use the typed API so invariants are preserved.

## Common Pitfalls
Model assignment has two tiers, and forecast and price models sit in a different tier than the rest. Forecast and price models are **scenario-global** — one of each applies to every well in the scenario — so they are set with `set_scenario(scenario, forecast_model=..., price_model=...)` and read from the `Scenario` row. The other model kinds (expense, tax, diff, shrink/yield, capex, interest) are **per-well** and are set with `set_well_models(prop_ids, scenario, ...)` and read from `well_models`. `set_well_models` has no `forecast_model` or `price_model` parameter on purpose; reaching for it to assign those is the usual wrong turn.

Assigning a forecast or price model is two steps. `set_forecast(prop_id, model=..., ...)` and `set_price_model(name, ...)` only *store* the curve or deck under a name; nothing is applied until a scenario points at that name with `set_scenario`. To read what a scenario actually uses, read the name from `scenarios()` first, then pass it to `forecasts(model=...)` or `price_models(name=...)`.

Expense and tax segment kinds drive adjacent fields: `AGE_BASED` requires `age_months`, `DATE_BASED` requires `effective_date`, and `SIMPLE` rejects both.

Monthly production dates must be the first day of the month.

Forecast segments driven by a type curve carry the curve name in `type_curve` and have `None` for `rate_init`, `decline_init`, `b_factor`, and `decline_min`. Check `row.kind` when reading forecasts rather than assuming the rate fields are numbers.

`Database.open()` never creates files; it raises `FileNotFoundError` when the path does not exist.

Whole-list `set_*` writers replace the full list under their natural key. Use the corresponding `delete_*` method when you intend to clear data.

A few `set_*` writers patch rather than replace: `set_well_models`, `set_scenario`, `set_completion`, and `set_well_header` update only the fields you pass and leave the rest unchanged.

The `set_*` writers never take `None` to mean every well. The `delete_*` writers do: called without a well id, `delete_forecast`, `delete_interest`, `delete_capex`, `delete_monthly_prod` and the other filtered deletes remove rows for every well. Never let a missing or `None` id reach a delete.

A forecast can start with stored monthly volumes, written when a Spatial Forecasting run is saved as a forecast. They come back from `forecasts()` as rows whose `kind` is `ForecastKind.MONTHLY_VOLUMES`, with the volumes in `monthly_volumes` and the rate fields `None`. Obsidian uses them as they are for the months they cover, and the segments carry on after. Such a row is not a segment: never build a `ForecastSegment` from it (`set_forecast` refuses one with no numbers). `set_forecast` leaves stored months in place; a segment that starts after them is the normal case, and one that starts earlier warns with `MonthlyVolumesWarning` because it has no effect until they end, which the user should hear. `delete_forecast` removes them with the forecast, and with a `phase` clears that phase's stored months only. The library cannot write them.

A field of an expense, tax, differential or shrink/yield model can be read per well from a well attribute column; the number on the model is then only a fallback. `model_field_refs()` lists those fields, so read it before reporting a model's numbers as what every well is charged. Writing such a model warns with `ModelFieldRefWarning`; tell the user rather than silence it, since the numbers written for those fields change no well's economics. A write that changes the model's number of segments is refused with `ValidationError`, and so is `delete_well_attribute_column` on a column a model reads unless `detach_model_refs=True`. The library does not create or edit the references themselves; that is done in Obsidian's model editor.

A write to a forecast, a well header, interests, capital, model assignments, a named model or a run settings is recorded in the database's `ChangeLog` (which well, table and model changed; never the values), the way Obsidian's own edits are. Production, surveys, attributes and scenarios are not logged, in Obsidian or here. Pass `user="..."` to `Database.open` when you know who the change is for; Obsidian shows it as Changed By. Pass `change_log=False` only for the first bulk load of a new database. Never write `ChangeLog` rows yourself.

A database can be missing tables until Obsidian next opens it: a file last saved by an older Obsidian, or a new Blank Database written from Python first. A missing `WellAttributes` table is handled for you: `add_well` and `add_well_attribute_column` create it, the attribute readers return no attributes, and the other attribute writers raise `ValidationError` for an unknown column (add the column first). `delete_well` and `copy_well` skip any of their tables the file does not have. Anything else that needs a missing table raises `MissingTableError` and writes nothing.

`delete_well` and `copy_well` cover Main, Monthly, Daily, Forecast (stored monthly volumes included), WellModels, Interest, Capex, Abandonment, Survey, Reservoir, Completion, Perfs, WellAttributes, a few rarely used per-well tables and the well's individual econ models, not every table with a `prop_id` column.

A new `WellAttributes` row (from `add_well`, or `set_well_attribute` on a well with no row) takes each column's declared default. Obsidian gives wells it adds the type default instead. To build a database from scratch, have the user create it with Obsidian's Blank Database button.

## Errors
All library errors subclass `ObsidianDbError`. The ones you will actually catch:
- `DatabaseLockedError` — another program has the file busy (Obsidian reading or saving it, or another script). Nothing was written; wait and retry.
- `ReadOnlyDatabaseError` — a write on a database opened with `read_only=True`. Open it without `read_only` if the task really is to write.
- `ModelNotFoundError` (`.model_kind`, `.name`) — a writer referenced a named model that does not exist.
- `WellNotFoundError` (`.prop_id`) — a writer, or `well_attributes`, named a PropID absent from Main.
- `ValidationError` — bad input: a raw string where an enum was required, a missing `confirm=True`, or no fields to write.
- `DataIntegrityError` — existing database contents could not be interpreted (names the column).
- `MissingTableError` (`.table`, a `DataIntegrityError`) — the file has no table the call needs, usually because it predates it. Tell the user to open the file once in Obsidian, which adds every current table, then retry. Never create the table yourself with `sqlite3`.
- `DuplicateError` — an `add_*` call targeted a key that already exists.

## Recommended Startup Sequence
Open the database with `Database.open()`, identify the typed readers and writers needed, assemble dataclass inputs, then run related writes inside one transaction.

## FAQ
How do I list every well? Call `db.wells()`.

How do I read a well's whole forecast? Call `db.forecasts(prop_id, model)`. Each phase's rows are in order of `start`; check `row.kind`, because a row is a decline segment, a type-curve segment or stored monthly volumes.

How do I export production to a DataFrame? Install `upstream-edge[pandas]` and call `db.production_monthly_df(prop_id)`.

How do I import monthly or daily production? Build `MonthlyRow` (or `DailyRow`) instances and pass them as a flat list to `db.set_monthly_prod(rows)` (or `db.set_daily_prod(rows)`) inside a transaction. Monthly dates must be the first of the month. Each row carries its own `prop_id`, so one call can cover one well or thousands.

How do I import production keyed by API10 instead of PropID? Build a lookup from `db.wells()` first, then translate before writing:

```python
api_to_prop = {w.api_10: w.prop_id for w in db.wells() if w.api_10}
rows = [
    MonthlyRow(prop_id=api_to_prop[r["api_10"]], month=date.fromisoformat(r["month"]),
               oil_bbl=float(r["oil_bbl"]), gas_mscf=float(r["gas_mscf"]),
               water_bbl=float(r["water_bbl"]))
    for r in csv_rows
]
db.set_monthly_prod(rows)
```

One `API10` may map to more than one `PropID`; build `dict[str, list[str]]` and fan out the rows if your data needs to land on every match.

How do I link a well to a model? Call `db.set_well_models(prop_ids, scenario="MAIN", expense_model="STD_OPEX")`. This covers the per-well kinds (expense, tax, diff, shrink/yield, capex, interest) only.

How do I set the forecast or price model for a scenario? Use `set_scenario`, not `set_well_models` — these two are scenario-global. Build the curve or deck first, then point the scenario at it by name:

```python
with db.transaction():
    db.set_forecast("PROP_001", model="BASE", phase=Phase.OIL, segments=[...])
    db.set_price_model("STRIP_2026", [...])
    db.set_scenario("MAIN", forecast_model="BASE", price_model="STRIP_2026")
```

How do I read the forecast or price model a scenario actually uses? Read the name off the scenario, then look it up:

```python
sc = db.scenarios("MAIN")[0]
curves = db.forecasts("PROP_001", model=sc.forecast_model)
deck = db.price_models(sc.price_model)
```

What happens if a referenced named model does not exist? The writer raises `ModelNotFoundError` before writing. (`set_scenario` is the one nuance: a missing `price_model` is a hard error, but a missing `forecast_model` only warns, since forecast model names are labels you may populate later.)

How do I create a new well? Call `db.add_well(prop_id, rsv_cat=RsvCat.PUD, ...)`.

How do I change the effective date? Call `db.set_run_settings("STANDARD", effective_date=date(2026, 7, 1))`. Each run settings has its own; `db.run_settings()` lists them.

How do I make a restore point? Call `db.snapshot()` before the writes, outside any transaction. It returns the path of the copy, in the `_Archive` folder beside the database, and Obsidian's Database mode lists it as a version. Each call saves a full copy of the database, so take one only when the user asks for it or before a bulk change that is hard to undo (deleting wells, replacing forecasts or models across a field), and at most once per task. Never take one before every write.

How do I add a custom attribute? Call `db.add_well_attribute_column(name, AttributeType.NUMERIC)` and then set values.

How do I inspect custom columns? Call `db.list_attribute_columns()`.

How do I avoid partial writes? Use `with db.transaction():` around related operations.

How do I delete data? Pass `confirm=True` and use the narrowest filter available.
