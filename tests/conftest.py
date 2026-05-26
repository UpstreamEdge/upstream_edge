from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path


def exec_sql(path: Path | str, statements: Iterable[str | tuple[str, tuple[object, ...]]]) -> None:
    """Run a sequence of SQL statements against a SQLite file path.

    Each item is either a SQL string or a (sql, params) tuple. Used by tests
    to construct schemas and seed rows without needing a library-level raw-SQL
    escape hatch.
    """
    conn = sqlite3.connect(str(path))
    try:
        for item in statements:
            if isinstance(item, tuple):
                sql, params = item
                conn.execute(sql, params)
            else:
                conn.execute(item)
        conn.commit()
    finally:
        conn.close()


_CORE_SCHEMA: tuple[str, ...] = (
    (
        "create table Main (prop_id text primary key, api_10 text, rsv_cat text not null, "
        'lease text, well_number text, field text, operator text, category text, "group" text, '
        "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
        "first_prod text, state text, county text, surface_latitude real, surface_longitude real, "
        "bh_latitude real, bh_longitude real)"
    ),
    (
        "create table WellModels (prop_id text, scenario text, exp_model_name text, "
        "capex_model_name text, diff_model_name text, tax_model_name text, "
        "shrink_yield_model_name text, interest_model_name text)"
    ),
    "create table Interest (prop_id text, model text, start text, wi_pct real, nri_pct real)",
    "create table Abandonment (prop_id text, model text, cost_gross real)",
    (
        "create table Capex (prop_id text, model text, date text, job_type text, "
        "cost_gross real, description text)"
    ),
    (
        "create table WellAttributes (prop_id text primary key, Basin text, "
        "WorkingInterest real, LeaseExpiry date)"
    ),
    (
        "create table Monthly (prop_id text, month text, oil_monthly_bbl real, "
        "gas_monthly_mscf real, water_monthly_bbl real, unique(prop_id, month))"
    ),
    (
        "create table Daily (prop_id text, date text, oil_bopd real, "
        "gas_mcfd real, water_bwpd real, unique(prop_id, date))"
    ),
    (
        "create table Forecast (prop_id text, model text, phase text, start text, "
        "type_curve text, rate_init real, decline_init real, b_factor real, decline_min real, "
        "unique(prop_id, model, phase, start))"
    ),
)


_RELATED_SCHEMA: tuple[str, ...] = (
    (
        "create table Survey (prop_id text, point_md real, point_tvd real, azimuth_angle real, "
        "inclination_angle real, deviation_ns real, deviation_ew real)"
    ),
    (
        "create table Reservoir (prop_id text, reservoir text, top_depth_ft real, "
        "gross_thickness_ft real)"
    ),
    (
        "create table Completion (prop_id text primary key, frac_proppant_lb real, "
        "frac_fluid_bbl real, frac_stages integer)"
    ),
    (
        "create table Perfs (prop_id text, perf_start_md_ft real, perf_end_md_ft real, "
        "producing integer)"
    ),
)


_ATTRIBUTE_SCHEMA: tuple[str, ...] = (
    (
        "create table Main (prop_id text primary key, api_10 text, rsv_cat text not null, "
        'lease text, well_number text, field text, operator text, category text, "group" text, '
        "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
        "first_prod text, state text, county text, surface_latitude real, surface_longitude real, "
        "bh_latitude real, bh_longitude real)"
    ),
    (
        "create table WellModels (prop_id text, scenario text, exp_model_name text, "
        "capex_model_name text, diff_model_name text, tax_model_name text, "
        "shrink_yield_model_name text, interest_model_name text)"
    ),
    "create table Interest (prop_id text, model text, start text, wi_pct real, nri_pct real)",
    "create table Abandonment (prop_id text, model text, cost_gross real)",
    "create table WellAttributes (prop_id text primary key)",
)


_MODEL_SCHEMA: tuple[str, ...] = (
    (
        "create table PriceModel (price_model_name text, start_date text, oil real, gas real, ngl real)"
    ),
    (
        "create table ExpenseModel (exp_model_name text, model_type text, fixed_monthly real, "
        "variable_oil real, variable_gas real, variable_water real, "
        "unique(exp_model_name, model_type))"
    ),
    (
        "create table TaxModel (tax_model_name text, model_type text, sev_tax_oil real, "
        "sev_tax_gas real, sev_tax_ngl real, ad_valorum_tax real, "
        "unique(tax_model_name, model_type))"
    ),
    (
        "create table DiffModel (diff_model_name text, start_date text, oil_diff_method text, "
        "oil_diff real, gas_diff_method text, gas_diff real, ngl_diff_method text, "
        "ngl_diff real, condensate_diff_method text, condensate_diff real)"
    ),
    (
        "create table ShrinkYieldModel (shrink_yield_model_name text primary key, "
        "gas_shrink_frac real, ngl_yield_bbl_mmscf real, condensate_yield_bbl_mmscf real)"
    ),
    "create table WellAttributes (prop_id text primary key, WellOpex real, TaxRate real)",
)


_SCENARIO_SCHEMA: tuple[str, ...] = (
    (
        "create table Main (prop_id text primary key, api_10 text, rsv_cat text not null, "
        'lease text, well_number text, field text, operator text, category text, "group" text, '
        "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
        "first_prod text, state text, county text, surface_latitude real, surface_longitude real, "
        "bh_latitude real, bh_longitude real)"
    ),
    (
        "create table WellModels (prop_id text, scenario text, exp_model_name text, "
        "capex_model_name text, diff_model_name text, tax_model_name text, "
        "shrink_yield_model_name text, interest_model_name text, unique(prop_id, scenario))"
    ),
    "create table Interest (prop_id text, model text, start text, wi_pct real, nri_pct real)",
    (
        "create table Capex (prop_id text, model text, date text, job_type text, "
        "cost_gross real, description text)"
    ),
    "create table Abandonment (prop_id text, model text, cost_gross real)",
    "create table WellAttributes (prop_id text primary key)",
    (
        "create table Scenario (scenario text primary key, forecast_model_name text, "
        "price_model_name text)"
    ),
    (
        "create table Forecast (prop_id text, model text, phase text, start text, type_curve text, "
        "rate_init real, decline_init real, b_factor real, decline_min real)"
    ),
    (
        "create table Monthly (prop_id text, month text, oil_monthly_bbl real, "
        "gas_monthly_mscf real, water_monthly_bbl real)"
    ),
    ("create table Daily (prop_id text, date text, oil_bopd real, gas_mcfd real, water_bwpd real)"),
    (
        "create table PriceModel (price_model_name text, start_date text, oil real, gas real, "
        "ngl real)"
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
        "oil_diff real, gas_diff_method text, gas_diff real, ngl_diff_method text, ngl_diff real, "
        "condensate_diff_method text, condensate_diff real)"
    ),
    (
        "create table ShrinkYieldModel (shrink_yield_model_name text primary key, "
        "gas_shrink_frac real, ngl_yield_bbl_mmscf real, condensate_yield_bbl_mmscf real)"
    ),
)


def create_core_schema(path: Path | str) -> None:
    """Build the core schema used by writer tests."""
    exec_sql(path, [*_CORE_SCHEMA, *_RELATED_SCHEMA])


def create_attribute_schema(path: Path | str) -> None:
    """Build the schema used by WellAttributes writer tests."""
    exec_sql(path, _ATTRIBUTE_SCHEMA)


def create_model_schema(path: Path | str) -> None:
    """Build the schema used by economic-model writer tests."""
    exec_sql(path, _MODEL_SCHEMA)


def create_scenario_schema(path: Path | str) -> None:
    """Build the schema used by scenario writer tests."""
    exec_sql(path, [*_SCENARIO_SCHEMA, *_RELATED_SCHEMA])


def seed_models(path: Path | str) -> None:
    """Insert canonical PriceModel / ExpenseModel / TaxModel / DiffModel / ShrinkYieldModel rows."""
    exec_sql(
        path,
        [
            ("insert into Scenario values (?, ?, ?)", ("MAIN", "BASE", "STRIP")),
            (
                "insert into PriceModel values (?, ?, ?, ?, ?)",
                ("STRIP", "2026-01-01", 70.0, 3.0, 20.0),
            ),
            (
                "insert into ExpenseModel values (?, ?, ?, ?, ?, ?)",
                ("OPEX", "SIMPLE", 1.0, 2.0, 3.0, 4.0),
            ),
            (
                "insert into TaxModel values (?, ?, ?, ?, ?, ?)",
                ("TAX", "SIMPLE", 0.1, 0.1, 0.1, 0.1),
            ),
            (
                "insert into DiffModel values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "DIFF",
                    "2026-01-01",
                    "DOLLAR",
                    -1.0,
                    "FRACTION",
                    0.9,
                    "FRACTION",
                    0.5,
                    "FRACTION",
                    0.0,
                ),
            ),
            ("insert into ShrinkYieldModel values (?, ?, ?, ?)", ("SY", 0.1, 40.0, 0.0)),
        ],
    )
