# upstream-edge

[![PyPI version](https://img.shields.io/pypi/v/upstream-edge.svg)](https://pypi.org/project/upstream-edge/)
[![Python versions](https://img.shields.io/pypi/pyversions/upstream-edge.svg)](https://pypi.org/project/upstream-edge/)
[![License](https://img.shields.io/pypi/l/upstream-edge.svg)](https://github.com/UpstreamEdge/upstream_edge/blob/main/LICENSE)
[![CI](https://github.com/UpstreamEdge/upstream_edge/actions/workflows/ci.yml/badge.svg)](https://github.com/UpstreamEdge/upstream_edge/actions/workflows/ci.yml)

A Python library for working with Obsidian databases — the SQLite files behind Upstream Edge's reserves and production forecasting application.  See [UpstreamEdge.com](https://UpstreamEdge.com) for more information.

If you've wanted to script a custom Obsidian integration with your data sources, batch-update price decks, pull production into a DataFrame, or build economic models automatically, this is the library to make it happen.

- **No build tools or compilers.** `pip install upstream-edge` and you're done.
- **Pandas if you want it.** Every reader has a `_df` sibling that returns a DataFrame. Skip the install and the library never touches pandas.
- **Won't quietly break your database.** Writes that touch more than one table run in a transaction, bad inputs raise clear errors instead of corrupting state, and every delete requires an explicit `confirm=True`.
- **Plays well with AI assistants.** Claude, Cursor, Copilot, and the like can read the library's structured types and docstrings and write working scripts on your behalf.

> **Using an AI assistant?** Point it at [`AGENTS.md`](AGENTS.md) before it writes any code. It's a one-read orientation doc — domain glossary, do's and don'ts, common pitfalls, and an FAQ — built specifically for agents driving this library.

---

## Installation

```bash
pip install upstream-edge                  # core library
pip install upstream-edge[pandas]          # add DataFrame support
```

Requires Python 3.11 or newer.

---

## Quickstart

```python
from upstream_edge.obsidian_db import Database

with Database.open("wells.obsdb") as db:
    for w in db.wells():
        print(w.prop_id, w.lease, w.rsv_cat.value)

    monthly = db.production_monthly("PROP_001")
    print(f"{len(monthly)} months of production")
```

Writes work the same way. Wrap multi-row work in a transaction for speed.

Load production data:

```python
from datetime import date
from upstream_edge.obsidian_db import Database, MonthlyRow

with Database.open("wells.obsdb") as db:
    with db.transaction():
        db.set_monthly_prod([
            MonthlyRow(prop_id="PROP_001", month=date(2026, 1, 1),
                       oil_bbl=2150.0, gas_mscf=3420.0, water_bbl=180.0),
            MonthlyRow(prop_id="PROP_001", month=date(2026, 2, 1),
                       oil_bbl=1980.0, gas_mscf=3180.0, water_bbl=165.0),
            MonthlyRow(prop_id="PROP_001", month=date(2026, 3, 1),
                       oil_bbl=1840.0, gas_mscf=2950.0, water_bbl=152.0),
        ])
```

Or build out a price deck:

```python
from datetime import date
from upstream_edge.obsidian_db import Database, PriceModelSegment

with Database.open("wells.obsdb") as db:
    with db.transaction():
        db.set_price_model("STRIP_2026", [
            PriceModelSegment(date(2026, 1, 1), oil=72.50, gas=3.40, ngl=24.10),
            PriceModelSegment(date(2027, 1, 1), oil=70.00, gas=3.20, ngl=23.00),
        ])
```

---

## Concept tour

If you haven't worked with an Obsidian database before, this is the vocabulary:

- **Databases** are SQLite files, conventionally with the `.obsdb` extension.
- **Wells** are the unit of analysis. Each well has a `PropID` (the library's primary identifier) and optionally an `API10`. Wells carry header info — lease, operator, dates, location, reserve category — plus links to economic models.
- **Production data** comes in two forms: monthly (keyed to the first of the month) and daily.
- **Forecasts** are per-well, per-model, per-phase Arps declines. A `(prop_id, model, phase)` triple can hold multiple segments, each starting on a different date. `model` here is a forecast-model *name*; a scenario applies one such name to all its wells.
- **Economic models** come in five forms — price, expense, tax, differential, shrink/yield — written as shared, named models. They attach to wells in two different ways (see below).
- **Model assignment has two tiers.** **Forecast** and **price** models are *scenario-global*: one of each applies to every well in the scenario, set with `set_scenario`. **Expense, tax, differential, shrink/yield** (plus capex and interest) are *per-well within a scenario*, set with `set_well_models`. The split is deliberate — `set_well_models` has no forecast or price parameter.
- **Interest** (WI / NRI) and **Capex** schedules are per-well, per-model, with their own multi-segment shapes.
- **Scenarios** group these assignments: the scenario-global forecast and price models live on the scenario itself, while the per-well model links live in `well_models`. The `MAIN` scenario always exists.
- **Well attributes** is a user-defined table — extra columns you add at runtime (text, numeric, or date).

---

## API reference

Everything lives on the `Database` class.

### Opening, closing, transactions

```python
Database.open(path: str | Path) -> Database
db.close() -> None
with Database.open(path) as db: ...
db.path -> Path
db.transaction() -> ContextManager[None]
```

Opening gives you read/write access. The path must be an existing database — `Database.open` raises `FileNotFoundError` rather than creating an empty file. Reads share the file with Obsidian normally. Write transactions raise `DatabaseLockedError` if Obsidian (or another process) is mid-write. Writers called outside a transaction get one implicitly; wrap multi-row work explicitly.

### Readers

Most readers are collection readers: they take optional filters (positional or keyword), and omitting them — or passing `None` — returns every row. The single-item lookups (`well`, `well_by_api`, `well_attributes`) instead take a required identifier and return one result or `None`.

**Wells**

```python
db.wells() -> list[Well]
db.well(prop_id: str) -> Well | None
db.well_by_api(api_10: str) -> Well | None
```

**Production**

```python
db.production_monthly(prop_id: str | None = None) -> list[MonthlyRow]
db.production_daily(prop_id: str | None = None) -> list[DailyRow]
```

**Forecasts**

```python
db.forecasts(prop_id: str | None = None, model: str | None = None) -> list[Forecast]
```

**Economic models**

```python
db.price_models(name: str | None = None) -> list[PriceModel]
db.expense_models(name: str | None = None) -> list[ExpenseModel]
db.tax_models(name: str | None = None) -> list[TaxModel]
db.diff_models(name: str | None = None) -> list[DiffModel]
db.shrink_yield_models(name: str | None = None) -> list[ShrinkYieldModel]
```

**Per-well economic linkage**

```python
db.well_models(prop_id: str | None = None, scenario: str | None = None) -> list[WellModels]
db.interest(prop_id: str | None = None, model: str | None = None) -> list[Interest]
db.capex(prop_id: str | None = None, model: str | None = None) -> list[Capex]
db.abandonment(prop_id: str | None = None, model: str | None = None) -> list[Abandonment]
```

**Scenarios**

```python
db.scenarios(name: str | None = None) -> list[Scenario]
```

A `Scenario` row carries the scenario-level models — `forecast_model` and `price_model`. Per-well model links (expense, tax, diff, etc.) live in `well_models`, not here.

**Reservoir and Subsurface**

```python
db.surveys(prop_id: str | None = None) -> list[SurveyPoint]
db.reservoirs(prop_id: str | None = None) -> list[Reservoir]
db.completions(prop_id: str | None = None) -> list[Completion]
db.perfs(prop_id: str | None = None) -> list[Perfs]
```

**Well attributes**

```python
db.list_attribute_columns() -> list[AttributeColumn]
db.well_attributes(prop_id: str) -> dict[str, str | float | date]
db.all_well_attributes() -> dict[str, dict[str, str | float | date]]
```

### DataFrame adapter

Every reader that returns a list or dict has a `_df` sibling that returns a pandas DataFrame (the single-well lookups `well` / `well_by_api` are the exception):

```python
db.wells_df() -> pandas.DataFrame
db.production_monthly_df(prop_id=None) -> pandas.DataFrame
# ... and so on for every reader.
```

A few conventions across the board:

- Columns match the row dataclass fields in declaration order.
- The index is always a default `RangeIndex` — time-series readers leave `month` / `date` as regular columns.
- Optional fields become nullable (`NaN`, `NaT`, `None`).
- Enums show up as their `.value` string.
- Empty results return an empty DataFrame with the right columns — never `None`.

Pandas is imported lazily inside the method; if it isn't installed, you'll get `MissingDependencyError`.

### Writers

**Wells**

```python
db.add_well(prop_id: str, *, rsv_cat: RsvCat, api_10: str | None = None,
            **header_fields) -> None
db.set_well_header(prop_id: str, **fields) -> None
db.copy_well(from_prop_id: str, to_prop_id: str) -> None
db.delete_well(prop_id: str, *, confirm: bool) -> None
```

`add_well` creates the well so it is immediately usable; follow with `set_interest`, `set_abandonment`, `set_well_models`, or `set_well_attribute` to customize as needed.

**Production**

```python
db.set_monthly_prod(rows: list[MonthlyRow]) -> None
db.set_daily_prod(rows: list[DailyRow]) -> None
db.delete_monthly_prod(prop_id: str | None = None, *, confirm: bool = False) -> None
db.delete_daily_prod(prop_id: str | None = None, *, confirm: bool = False) -> None
```

Production writers take a flat list of rows — single-well or multi-well, the library groups by `prop_id` internally. `set_monthly_prod` upserts by `(prop_id, month)`; `set_daily_prod` upserts by `(prop_id, date)`.

**Forecasts**

```python
db.set_forecast(prop_id: str, model: str, phase: Phase,
                segments: list[ForecastSegment]) -> None
db.delete_forecast(prop_id: str | None = None, model: str | None = None,
                   phase: Phase | None = None, *, confirm: bool = False) -> None
```

**Reservoir and Subsurface**

```python
db.set_surveys(prop_id: str, points: list[SurveyPointInput]) -> None
db.set_reservoir(prop_id: str, reservoir: str, *,
                 top_depth_ft: float,
                 thickness_ft: float | None = None) -> None
db.set_completion(prop_id: str, *,
                  frac_proppant_lb: float | None = None,
                  frac_fluid_bbl: float | None = None,
                  frac_stages: int | None = None) -> None
db.set_perfs(prop_id: str, perfs: list[PerfsInput]) -> None

db.delete_surveys(prop_id: str | None = None, *, confirm: bool = False) -> None
db.delete_reservoir_data(prop_id: str | None = None, *,
                         reservoir: str | None = None,
                         confirm: bool = False) -> None
db.delete_completion(prop_id: str, *, confirm: bool = False) -> None
db.delete_perfs(prop_id: str | None = None, *, confirm: bool = False) -> None
```

Whole-list writers (`set_surveys`, `set_perfs`) replace every row for the prop_id; field-level setters leave the other fields alone.

**Economic models**

```python
db.set_price_model(name: str, segments: list[PriceModelSegment]) -> None

# Named, shared models — any well can reference the same name.
db.set_expense_model(name: str, segments: list[ExpenseModelSegment]) -> None
db.set_tax_model(name: str, segments: list[TaxModelSegment]) -> None
db.set_diff_model(name: str, segments: list[DiffModelSegment]) -> None
db.set_shrink_yield_model(name: str, *,
                          gas_shrink_frac: float,
                          ngl_yield_bbl_mmscf: float) -> None

db.delete_price_model(name: str, *, confirm: bool = False) -> None
db.delete_expense_model(name: str, *, confirm: bool = False) -> None
# Same shape for tax / diff / shrink_yield.
```

`set_*_model` writes a shared, named model; wells reference it by name through `set_well_models`.

**Interest, capex, abandonment**

```python
db.set_interest(prop_ids: str | list[str], model: str,
                segments: list[InterestSegment]) -> None
db.set_capex(prop_id: str, model: str, items: list[CapexItem]) -> None
db.set_abandonment(prop_id: str, model: str, cost_gross: float) -> None

db.delete_interest(prop_ids: str | list[str] | None = None,
                   model: str | None = None, *, confirm: bool = False) -> None
db.delete_capex(prop_id: str | None = None, model: str | None = None,
                *, confirm: bool = False) -> None
```

`set_interest` writes the same schedule to every well in `prop_ids` (single string or list). Interest values are percentages from 0 to 100, e.g. 75.0 for 75%.

Every well keeps an abandonment cost for each model, so there is no `delete_abandonment` — call `set_abandonment(prop_id, model, 0.0)` to clear it.

**Scenarios and well-to-model assignment**

```python
db.create_scenario(name: str, *, copy_from: str | None = None) -> None
db.delete_scenario(name: str, *, confirm: bool) -> None
db.set_scenario(name: str, *,
                forecast_model: str | None = None,
                price_model: str | None = None) -> None

db.set_well_models(prop_ids: str | list[str], scenario: str, *,
                   exp_model: str | None = None,
                   capex_model: str | None = None,
                   diff_model: str | None = None,
                   tax_model: str | None = None,
                   shrink_yield_model: str | None = None,
                   interest_model: str | None = None) -> None
```

`set_well_models` is a partial update — only kwargs you pass get written. Named-entity references (`exp_model`, `diff_model`, `tax_model`, `shrink_yield_model`) are validated before the transaction opens. Label tags (`capex_model`, `interest_model`) emit a `UserWarning` if no matching rows exist yet — a friendly typo guard.

**Well attributes**

```python
db.set_well_attribute(prop_id: str, column: str,
                      value: str | float | date) -> None
db.set_well_attributes_bulk(updates: list[WellAttributeUpdate]) -> None

db.add_well_attribute_column(name: str, attr_type: AttributeType,
                             default: str | float | date | None = None) -> None
db.rename_well_attribute_column(old: str, new: str) -> None
db.delete_well_attribute_column(name: str, *, confirm: bool) -> None
```

Adding a column backfills the default for every existing well.

### Row dataclasses

All row types are immutable.

**Read types:** `Well`, `MonthlyRow`, `DailyRow`, `Forecast`, `PriceModel`, `ExpenseModel`, `TaxModel`, `DiffModel`, `ShrinkYieldModel`, `WellModels`, `Interest`, `Capex`, `Abandonment`, `Scenario`, `SurveyPoint`, `Reservoir`, `Completion`, `Perfs`, `AttributeColumn`.

**Write-input types** (body-only — the natural-key columns come from the writer's positional args): `ForecastSegment`, `PriceModelSegment`, `ExpenseModelSegment`, `TaxModelSegment`, `DiffModelSegment`, `InterestSegment`, `CapexItem`, `WellAttributeUpdate`, `SurveyPointInput`, `PerfsInput`.

`MonthlyRow` and `DailyRow` work for both reads and writes. The production writers take a flat list with each row carrying its own `prop_id`, so the same call works for one well or for thousands.

### Enums

```python
RsvCat       = PDP | SHUT_IN | DUC | PUD | PROB | POSS |
               LOC | TA | PA | SWD | UNSPECIFIED | DATA
Phase        = OIL | GAS | WATER
CapexJobType = DRILLING | COMPLETIONS | FACILITIES | SURFACE_WORK |
               PUMP_REPAIR | TUBING_REPAIR | ROD_REPAIR |
               CASING_REPAIR | OTHER
DiffType      = DOLLAR | FRACTION
AttributeType = TEXT | NUMERIC | DATE
```

Expense and tax model segments carry a `kind` plus the field that kind needs:

```python
ExpenseModelKind = SIMPLE | AGE_BASED | DATE_BASED
TaxModelKind     = SIMPLE | AGE_BASED | DATE_BASED

ExpenseModelSegment(kind=ExpenseModelKind.SIMPLE, fixed_monthly=2500.0, ...)
ExpenseModelSegment(kind=ExpenseModelKind.AGE_BASED, age_months=18,
                    fixed_monthly=2500.0, ...)
ExpenseModelSegment(kind=ExpenseModelKind.DATE_BASED,
                    effective_date=date(2026, 1, 1),
                    fixed_monthly=2500.0, ...)
```

`SIMPLE` rejects both `age_months` and `effective_date`; `AGE_BASED` requires `age_months`; `DATE_BASED` requires `effective_date`. Same shape on `TaxModelSegment`.

### Exceptions

All inherit from `ObsidianDbError`.

| Exception | When |
|---|---|
| `DatabaseLockedError` | Another process holds the SQLite write lock during a write transaction. |
| `DataIntegrityError` | The database contains data the library can't interpret (e.g. missing column). |
| `WellNotFoundError` | A PropID you referenced doesn't exist. Carries `.prop_id`. |
| `ModelNotFoundError` | A named model you referenced doesn't exist. Carries `.model_kind` and `.name`. |
| `DuplicateError` | An `add_*` was called for a key that already exists. |
| `ValidationError` | Invalid input — bad value, unknown column, mutually-exclusive kwargs. |
| `MissingDependencyError` | Pandas isn't installed and you called a `_df` method. |

Error messages name the method, the offending input, and the valid alternatives where the API can determine them.

### Values and conventions

- **Dates** use `datetime.date`. Monthly fields are keyed to the first of the month.
- **Percentages** use two conventions: severance/ad-valorem tax rates and `gas_shrink_frac` are fractions in `[0, 1]` (`0.046` for 4.6%); interest `wi_pct` / `nri_pct` run `0`–`100` (`75.0` for 75%). Out-of-range values raise `ValidationError`.
- **Volumes**: oil in barrels (`bbl`), gas in MSCF, water in barrels. Daily rates use `bopd` / `mcfd` / `bwpd`.
- **Identifiers**: `PropID` is a plain string; `API10` is exactly ten digits.
- **Enums** round-trip as their `.value` string.
- **Empty inputs** to whole-replace writers raise `ValidationError` and point at the corresponding `delete_*` method. Empty inputs to per-key upserts are a silent no-op.
- **`set_*` is idempotent.** Safe to retry after a hiccup.
- **Every delete** requires `confirm=True`. The library would rather make you type one extra word than vaporize a quarter's work by accident.

### Logging

The library logs to `logging.getLogger("upstream_edge.obsidian_db")` with a `NullHandler` attached at import. Configure handlers in your application; the library never calls `print`.

### Thread safety

`Database` instances are not thread-safe. Open one per thread.

---

## Cookbook

Worked recipes for the common things people do. Each block stands alone — copy, paste, edit the names, run.

### List every well in a database

```python
from upstream_edge.obsidian_db import Database

with Database.open("wells.obsdb") as db:
    for w in db.wells():
        print(w.prop_id, w.lease, w.rsv_cat.value)
```

### Multiple writes in one transaction

```python
from upstream_edge.obsidian_db import Database, RsvCat

with Database.open("wells.obsdb") as db:
    with db.transaction():
        db.add_well("PROP_999", rsv_cat=RsvCat.PUD, lease="NEW LEASE 1H")
        db.set_well_header("PROP_999", operator="Mitchell", state="TX", county="Midland")
```

### Filter wells and pull first-prod dates

```python
from upstream_edge.obsidian_db import Database, RsvCat

with Database.open("wells.obsdb") as db:
    targets = [w for w in db.wells()
               if w.rsv_cat == RsvCat.PUD and w.operator == "Mitchell"]
    for w in targets:
        print(w.prop_id, w.first_prod)
```

### Export monthly production to a DataFrame

```python
with Database.open("wells.obsdb") as db:
    df = db.production_monthly_df("PROP_001")
    df.to_csv("prop_001_monthly.csv", index=False)
```

### Cumulative oil through an as-of date

```python
from datetime import date

with Database.open("wells.obsdb") as db:
    cutoff = date(2026, 1, 1)
    for prop_id in ("PROP_001", "PROP_002"):
        rows = db.production_monthly(prop_id)
        cum = sum(r.oil_bbl or 0.0 for r in rows if r.month < cutoff)
        print(prop_id, f"{cum:,.0f} bbl")
```

### Three-segment price model

```python
from datetime import date
from upstream_edge.obsidian_db import Database, PriceModelSegment

with Database.open("wells.obsdb") as db:
    db.set_price_model("STRIP_2026", [
        PriceModelSegment(date(2026, 1, 1), oil=72.50, gas=3.40, ngl=24.10),
        PriceModelSegment(date(2027, 1, 1), oil=70.00, gas=3.20, ngl=23.00),
        PriceModelSegment(date(2028, 1, 1), oil=68.00, gas=3.10, ngl=22.00),
    ])
```

### Apply a forecast and price deck to a scenario

Forecast and price models are scenario-global: build them under a name, then point the scenario at that name. (Use `set_scenario`, not `set_well_models` — the latter handles only the per-well model kinds.)

```python
from datetime import date
from upstream_edge.obsidian_db import (
    Database, ForecastSegment, Phase, PriceModelSegment,
)

with Database.open("wells.obsdb") as db:
    with db.transaction():
        # 1. Store the curve under a forecast-model name and the deck under its name.
        db.set_forecast("PROP_001", model="BASE", phase=Phase.OIL, segments=[
            ForecastSegment(start=date(2026, 6, 1), rate_init=450.0,
                            decline_init=0.65, b_factor=1.1, decline_min=0.06),
        ])
        db.set_price_model("STRIP_2026", [
            PriceModelSegment(date(2026, 1, 1), oil=72.50, gas=3.40, ngl=24.10),
        ])
        # 2. Apply both to the scenario — globally, for every well in it.
        db.set_scenario("MAIN", forecast_model="BASE", price_model="STRIP_2026")
```

To read back what a scenario actually uses, resolve the name through the scenario:

```python
with Database.open("wells.obsdb") as db:
    sc = db.scenarios("MAIN")[0]
    curves = db.forecasts("PROP_001", model=sc.forecast_model)
    deck = db.price_models(sc.price_model)
```

### Simple expense model assigned to a scenario

```python
from upstream_edge.obsidian_db import (
    Database, ExpenseModelSegment, ExpenseModelKind,
)

with Database.open("wells.obsdb") as db:
    with db.transaction():
        db.set_expense_model(
            "STD_OPEX",
            segments=[ExpenseModelSegment(
                kind=ExpenseModelKind.SIMPLE,
                fixed_monthly=2500.0,
                variable_oil=4.50, variable_gas=0.30, variable_water=1.10,
            )],
        )
        db.set_well_models(
            ["PROP_001", "PROP_002"], scenario="MAIN",
            exp_model="STD_OPEX",
        )
```

### Age-based tax model

```python
from upstream_edge.obsidian_db import (
    Database, TaxModelSegment, TaxModelKind,
)

with Database.open("wells.obsdb") as db:
    db.set_tax_model(
        "TX_PERMIAN",
        segments=[
            TaxModelSegment(
                kind=TaxModelKind.AGE_BASED, age_months=18,
                sev_tax_oil=0.046, sev_tax_gas=0.075,
                sev_tax_ngl=0.046, ad_valorum_tax=0.025,
            ),
            TaxModelSegment(
                kind=TaxModelKind.SIMPLE,
                sev_tax_oil=0.046, sev_tax_gas=0.075,
                sev_tax_ngl=0.046, ad_valorum_tax=0.025,
            ),
        ],
    )
```

### Date-based diff model

```python
from datetime import date
from upstream_edge.obsidian_db import (
    Database, DiffModelSegment, DiffType,
)

with Database.open("wells.obsdb") as db:
    db.set_diff_model(
        "MIDLAND_DIFF",
        segments=[DiffModelSegment(
            start_date=date(2026, 1, 1),
            oil_method=DiffType.DOLLAR, oil_diff=-2.50,
            gas_method=DiffType.FRACTION, gas_diff=0.92,
            ngl_method=DiffType.FRACTION, ngl_diff=0.35,
        )],
    )
```

### Single-segment Arps decline forecast

```python
from datetime import date
from upstream_edge.obsidian_db import Database, ForecastSegment, Phase

with Database.open("wells.obsdb") as db:
    db.set_forecast("PROP_001", model="BASE", phase=Phase.OIL,
                    segments=[ForecastSegment(
                        start=date(2026, 6, 1),
                        rate_init=450.0,
                        decline_init=0.65,
                        b_factor=1.1,
                        decline_min=0.06,
                    )])
```

### Multi-segment forecast (flush, decline, terminal)

```python
with Database.open("wells.obsdb") as db:
    db.set_forecast("PROP_001", model="BASE", phase=Phase.OIL, segments=[
        ForecastSegment(start=date(2026, 6, 1),  rate_init=620.0, decline_init=0.95, b_factor=1.4, decline_min=0.06),
        ForecastSegment(start=date(2026, 12, 1), rate_init=410.0, decline_init=0.65, b_factor=1.1, decline_min=0.06),
        ForecastSegment(start=date(2030, 1, 1),  rate_init=120.0, decline_init=0.06, b_factor=0.0, decline_min=0.06),
    ])
```

### Bulk-import monthly production for many wells

```python
import csv
from datetime import date
from upstream_edge.obsidian_db import Database, MonthlyRow

with Database.open("wells.obsdb") as db, db.transaction():
    with open("production.csv") as f:
        rows = [MonthlyRow(
            prop_id=r["prop_id"],
            month=date.fromisoformat(r["month"]),
            oil_bbl=float(r["oil_bbl"]),
            gas_mscf=float(r["gas_mscf"]),
            water_bbl=float(r["water_bbl"]),
        ) for r in csv.DictReader(f)]
    db.set_monthly_prod(rows)
```

### Replace a well's directional survey

```python
from upstream_edge.obsidian_db import SurveyPointInput

points = [SurveyPointInput(
    point_md=md, point_tvd=tvd,
    azimuth_angle=az, inclination_angle=incl,
    deviation_ns=ns, deviation_ew=ew,
) for md, tvd, az, incl, ns, ew in load_survey_csv("survey.csv")]

with Database.open("wells.obsdb") as db:
    db.set_surveys("PROP_001", points)
```

### Add a custom date attribute and backfill from CSV

```python
import csv
from datetime import date
from upstream_edge.obsidian_db import (
    Database, AttributeType, WellAttributeUpdate,
)

with Database.open("wells.obsdb") as db, db.transaction():
    db.add_well_attribute_column("LeaseExpiry", AttributeType.DATE)
    updates = []
    with open("lease_expiry.csv") as f:
        for r in csv.DictReader(f):
            updates.append(WellAttributeUpdate(
                prop_id=r["prop_id"],
                column="LeaseExpiry",
                value=date.fromisoformat(r["expiry"]),
            ))
    db.set_well_attributes_bulk(updates)
```

### Copy a scenario and swap its diff model

```python
with Database.open("wells.obsdb") as db, db.transaction():
    db.create_scenario("MIDLAND_DIFF_TEST", copy_from="MAIN")
    prop_ids = [wm.prop_id for wm in db.well_models(scenario="MIDLAND_DIFF_TEST")]
    db.set_well_models(prop_ids, scenario="MIDLAND_DIFF_TEST",
                       diff_model="MIDLAND_DIFF")
```

### Bulk-update multi-segment interest for 200 wells

```python
from datetime import date
from upstream_edge.obsidian_db import Database, InterestSegment

prop_ids = [f"PROP_{i:03d}" for i in range(1, 201)]
segments = [
    InterestSegment(start=date(2026, 1, 1), wi_pct=100.0, nri_pct=75.0),
    InterestSegment(start=date(2031, 1, 1), wi_pct=100.0, nri_pct=80.0),
]
with Database.open("wells.obsdb") as db, db.transaction():
    db.set_interest(prop_ids, model="MAIN", segments=segments)
```

---

## Working with AI assistants

AI coding assistants — Claude, Cursor, Copilot, and the rest — drive this library well. Point one at your repository and ask it to *"pull last quarter's production for every Permian well into a CSV"* or *"rebuild the STRIP_2026 price deck from this spreadsheet"* and you should get something runnable on the first pass.

A few things make that work:

- **[`AGENTS.md`](AGENTS.md)** ships at the repository root. It's a dense, agent-targeted bootstrap doc — domain glossary, do's and don'ts, common pitfalls, a startup sequence, and an FAQ. One read and the agent is oriented; you don't have to write the prompt yourself.
- **Methods have typed signatures and source docstrings.** Agents can inspect the public API directly instead of guessing table names or row shapes.
- **Errors point at the fix.** Misspell a field and the exception names the method, the bad value, and the valid alternatives when available.
- **Destructive operations need `confirm=True`.** An agent can't quietly wipe data; it has to name the flag.

A reasonable workflow for an agent: read `AGENTS.md`, open the database, then do its work inside a `transaction()`.

---

## License

Apache License 2.0. See `LICENSE`.

---

## Contributing

Issues and pull requests are welcome.
