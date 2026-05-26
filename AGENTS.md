# AI Agent Guide

## Orientation
`upstream_edge.obsidian_db` is a pure-Python client for Obsidian SQLite databases. It loads and reads data; forecasting and economic calculations happen in Obsidian, not in this package. Every reader has a `_df` sibling that returns a pandas DataFrame — install `upstream-edge[pandas]` to use them.

## Domain Glossary
`PropID` is the primary well identifier used by the library. `API10` is an optional ten-digit regulatory identifier and may map to more than one PropID.

`RsvCat` is the reserve category stored on each well: PDP, ShutIn, DUC, PUD, PROB, POSS, LOC, TA, P&A, SWD, Blank, Data.

A scenario groups forecast and economic-model assignments. `MAIN` is the default scenario created by Obsidian workflows.

Price models are global commodity price decks. Expense, tax, differential, and shrink/yield models can be shared named models or per-well overrides linked through scenarios.

Well attributes are user-defined per-well columns.

## Always Do / Never Do
- Always wrap multi-row writes in `db.transaction()`.
- Always pass `confirm=True` explicitly when calling destructive methods.
- Always call writer methods by keyword past the first positional identifiers.
- Always pass enum values, not their string equivalents (`RsvCat.PUD`, `Phase.OIL`) — writers reject raw strings.
- Never open the SQLite file with `sqlite3.connect()` directly for writes; use the typed API so invariants are preserved.

## Common Pitfalls
Expense and tax segment kinds drive adjacent fields: `AGE_BASED` requires `age_months`, `DATE_BASED` requires `effective_date`, and `SIMPLE` rejects both.

Monthly production dates must be the first day of the month.

Whole-list `set_*` writers replace the full list under their natural key. Use the corresponding `delete_*` method when you intend to clear data.

Writers never accept `prop_ids=None` to mean all wells.

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

How do I link a well to a model? Call `db.set_well_models(prop_ids, scenario="MAIN", exp_model="STD_OPEX")`.

What happens if a referenced named model does not exist? The writer raises `ModelNotFoundError` before writing.

How do I create a new well? Call `db.add_well(prop_id, rsv_cat=RsvCat.PUD, ...)`.

How do I add a custom attribute? Call `db.add_well_attribute_column(name, AttributeType.NUMERIC)` and then set values.

How do I inspect custom columns? Call `db.list_attribute_columns()`.

How do I avoid partial writes? Use `with db.transaction():` around related operations.

How do I delete broad data? Pass `confirm=True` and use the narrowest filter available.
