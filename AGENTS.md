# AI Agent Guide

## Orientation
`upstream_edge.obsidian_db` is a pure-Python client for Obsidian SQLite databases. It loads and reads data; forecasting and economic calculations happen in Obsidian, not in this package. Every reader has a `_df` sibling that returns a pandas DataFrame — install `upstream-edge[pandas]` to use them.

## Domain Glossary
`PropID` is the primary well identifier used by the library. `API10` is an optional ten-digit regulatory identifier and may map to more than one PropID.

`RsvCat` is the reserve category stored on each well: PDP, ShutIn, DUC, PUD, PROB, POSS, LOC, TA, P&A, SWD, Blank, Data.

A scenario groups forecast and economic-model assignments. `MAIN` is the default scenario created by Obsidian workflows.

Price models are global commodity price decks. Expense, tax, differential, and shrink/yield models are shared, named models that wells reference through scenarios via `set_well_models`.

Well attributes are user-defined per-well columns.

Interest values (`wi_pct`, `nri_pct`) are percentages from 0 to 100, e.g. 75.0 for 75%.

## Always Do / Never Do
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

Forecast segments driven by a type curve carry the curve name in `type_curve` and have `None` for `rate_init`, `decline_init`, `b_factor`, and `decline_min` — handle both shapes when reading forecasts.

`Database.open()` never creates files; it raises `FileNotFoundError` when the path does not exist.

Whole-list `set_*` writers replace the full list under their natural key. Use the corresponding `delete_*` method when you intend to clear data.

A few `set_*` writers patch rather than replace: `set_well_models`, `set_scenario`, `set_completion`, and `set_well_header` update only the fields you pass and leave the rest unchanged.

Writers never accept `prop_ids=None` to mean all wells.

## Errors
All library errors subclass `ObsidianDbError`. The ones you will actually catch:
- `DatabaseLockedError` — Obsidian (or more likely another process) holds the write lock. Close the database and retry.
- `ModelNotFoundError` (`.model_kind`, `.name`) — a writer referenced a named model that does not exist.
- `WellNotFoundError` (`.prop_id`) — a writer referenced a PropID absent from Main.
- `ValidationError` — bad input: a raw string where an enum was required, a missing `confirm=True`, or no fields to write.
- `DataIntegrityError` — existing database contents could not be interpreted (names the column).
- `DuplicateError` — an `add_*` call targeted a key that already exists.

## Recommended Startup Sequence
Open the database with `Database.open()`, identify the typed readers and writers needed, assemble dataclass inputs, then run related writes inside one transaction.

## FAQ
How do I list every well? Call `db.wells()`.

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

How do I link a well to a model? Call `db.set_well_models(prop_ids, scenario="MAIN", exp_model="STD_OPEX")`. This covers the per-well kinds (expense, tax, diff, shrink/yield, capex, interest) only.

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

How do I add a custom attribute? Call `db.add_well_attribute_column(name, AttributeType.NUMERIC)` and then set values.

How do I inspect custom columns? Call `db.list_attribute_columns()`.

How do I avoid partial writes? Use `with db.transaction():` around related operations.

How do I delete data? Pass `confirm=True` and use the narrowest filter available.
