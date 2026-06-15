from __future__ import annotations

from datetime import date

from conftest import exec_sql

from upstream_edge.obsidian_db import (
    Database,
    DiffType,
    ExpenseModelKind,
    Phase,
    TaxModelKind,
)


def test_forecasts_reader(tmp_path):
    db_path = tmp_path / "forecast.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Forecast (prop_id text, model text, phase text, start text, "
                "type_curve text, rate_init real, decline_init real, b_factor real, decline_min real)"
            ),
            (
                "insert into Forecast values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("P1", "BASE", "OIL", "2026-01-01", "", 500.0, 0.7, 1.1, 0.06),
            ),
            # Phase casing is read case-insensitively.
            (
                "insert into Forecast values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("P1", "BASE", "Gas", "2026-01-01", "", 900.0, 0.7, 1.1, 0.06),
            ),
            # Segments driven by a type curve have no decline parameters of their own.
            (
                "insert into Forecast values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("P1", "BASE", "WATER", "2026-01-01", "TC_A", None, None, None, None),
            ),
        ],
    )

    with Database.open(db_path) as db:
        forecasts = db.forecasts(prop_id="P1")

    oil = next(row for row in forecasts if row.phase is Phase.OIL)
    gas = next(row for row in forecasts if row.phase is Phase.GAS)
    water = next(row for row in forecasts if row.phase is Phase.WATER)

    assert oil.start == date(2026, 1, 1)
    # Obsidian databases may store missing optional values as empty strings; read back as None.
    assert oil.type_curve is None
    assert oil.rate_init == 500.0
    assert gas.rate_init == 900.0
    assert water.type_curve == "TC_A"
    assert water.rate_init is None
    assert water.decline_min is None


def test_shared_economic_model_readers(tmp_path):
    db_path = tmp_path / "econ.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table PriceModel (price_model_name text, start_date text, oil real, "
                "gas real, ngl real)"
            ),
            (
                "create table ExpenseModel (exp_model_name text, model_type text, fixed_monthly real, "
                "variable_oil real, variable_gas real, variable_water real)"
            ),
            (
                "create table TaxModel (tax_model_name text, model_type text, sev_tax_oil real, "
                "sev_tax_gas real, sev_tax_ngl real, ad_valorum_tax real)"
            ),
            (
                "create table DiffModel (diff_model_name text, start_date text, oil_diff_method text, "
                "oil_diff real, gas_diff_method text, gas_diff real, ngl_diff_method text, "
                "ngl_diff real, condensate_diff_method text, condensate_diff real)"
            ),
            (
                "create table ShrinkYieldModel (shrink_yield_model_name text, gas_shrink_frac real, "
                "ngl_yield_bbl_mmscf real, condensate_yield_bbl_mmscf real)"
            ),
            (
                "insert into PriceModel values (?, ?, ?, ?, ?)",
                ("STRIP", "2026-01-01", 70.0, 3.0, 20.0),
            ),
            (
                "insert into ExpenseModel values (?, ?, ?, ?, ?, ?)",
                ("OPEX", "AGEBASED(18)", 1.0, 2.0, 3.0, 4.0),
            ),
            (
                "insert into TaxModel values (?, ?, ?, ?, ?, ?)",
                ("TAX", "DATEBASED(2026-01-01)", 0.1, 0.2, 0.3, 0.4),
            ),
            (
                "insert into DiffModel values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "DIFF",
                    "2026-01-01",
                    "DOLLAR",
                    -2.0,
                    "FRACTION",
                    0.9,
                    "FRACTION",
                    0.5,
                    "FRACTION",
                    0.0,
                ),
            ),
            (
                "insert into ShrinkYieldModel values (?, ?, ?, ?)",
                ("SY", 0.12, 45.0, 0.0),
            ),
        ],
    )

    with Database.open(db_path) as db:
        price = db.price_models("STRIP")[0]
        expense = db.expense_models("OPEX")[0]
        tax = db.tax_models("TAX")[0]
        diff = db.diff_models("DIFF")[0]
        shrink_yield = db.shrink_yield_models("SY")[0]

    assert price.start_date == date(2026, 1, 1)
    assert expense.kind is ExpenseModelKind.AGE_BASED
    assert expense.age_months == 18
    assert tax.kind is TaxModelKind.DATE_BASED
    assert tax.effective_date == date(2026, 1, 1)
    assert diff.oil_method is DiffType.DOLLAR
    assert shrink_yield.gas_shrink_frac == 0.12
