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


# The schema of the file Obsidian's Blank Database button wrote, unchanged,
# until October 2026 (Obsidian's `assets/BlankDatabase.db`, user_version 0),
# copied from its sqlite_master. It predates WellAttributes, so it is the file
# a customer has when they create a blank database in that Obsidian and write
# to it from Python before Obsidian ever loads it. The file's seed rows (the
# MAIN scenario and default models) are left out; no test needs them.
_OBSIDIAN_2026_02_BLANK_SCHEMA: tuple[str, ...] = (
    (
        "CREATE TABLE 'Monthly'([prop_id] TEXT, [month] TEXT, [oil_monthly_bbl] REAL, "
        "[gas_monthly_mscf] REAL, [water_monthly_bbl] REAL)"
    ),
    (
        "CREATE TABLE 'Daily'([prop_id] TEXT, [date] DATE, [oil_bopd] REAL, [gas_mcfd] REAL, "
        "[water_bwpd] REAL)"
    ),
    (
        "CREATE TABLE 'TypeCurve'([name] TEXT, [phase] TEXT, [normalization] TEXT, "
        "[start_rate] REAL, [build_up_months] INTEGER, [initial_rate] REAL, "
        "[bridge_months] INTEGER, [bridge_decline] REAL, [initial_decline] REAL, "
        "[b_factor] REAL, [minimum_decline] REAL, [ref_apis] TEXT)"
    ),
    "CREATE TABLE 'Abandonment'([prop_id] TEXT, [model] TEXT, [cost_gross] REAL)",
    (
        "CREATE TABLE 'Capex'([prop_id] TEXT, [model] TEXT, [date] DATE, [job_type] TEXT, "
        "[cost_gross] REAL, [description] TEXT)"
    ),
    (
        "CREATE TABLE 'PriceModel'([price_model_name] TEXT, [start_date] DATE, [oil] REAL, "
        "[gas] REAL, [ngl] REAL)"
    ),
    (
        "CREATE TABLE 'Survey'([prop_id] TEXT, [point_md] REAL, [point_tvd] REAL, "
        "[azimuth_angle] REAL, [inclination_angle] REAL, [deviation_ns] REAL, "
        "[deviation_ew] REAL)"
    ),
    (
        "CREATE TABLE 'Reservoir'([prop_id] TEXT, [reservoir] TEXT, [top_depth_ft] REAL, "
        "[gross_thickness_ft] REAL, [porosity_pct] REAL, [matrix_perm_md] REAL, "
        "[pressure_init_psi] REAL, [sat_oil_init] REAL, [sat_gas_init] REAL, "
        "[sat_water_init] REAL)"
    ),
    "CREATE TABLE 'Metadata'([type] TEXT, [value] TEXT)",
    (
        "CREATE TABLE 'Casing'([prop_id] TEXT, [casing_od_in] REAL, [casing_depth_md_ft] REAL, "
        "[cement_top_md_ft] REAL)"
    ),
    (
        "CREATE TABLE 'Perfs'([prop_id] TEXT, [perf_start_md_ft] REAL, [perf_end_md_ft] REAL, "
        "[producing] INTEGER)"
    ),
    (
        'CREATE TABLE "Main" (prop_id TEXT, api_10 TEXT, rsv_cat TEXT, lease TEXT, '
        'well_number TEXT, field TEXT, operator TEXT, category TEXT, "group" TEXT, '
        "reservoir TEXT, tvd REAL, md REAL, lateral_length REAL, spud TEXT, completion TEXT, "
        "first_prod TEXT, state TEXT, county TEXT, surface_latitude REAL, "
        "surface_longitude REAL, bh_latitude REAL, bh_longitude REAL)"
    ),
    (
        'CREATE TABLE "DiffModel" (diff_model_name TEXT, start_date DATE, oil_diff_method TEXT, '
        "oil_diff REAL, gas_diff_method TEXT, gas_diff REAL, ngl_diff_method TEXT, "
        "ngl_diff REAL, condensate_diff_method TEXT, condensate_diff REAL)"
    ),
    (
        'CREATE TABLE "ExpenseModel" (exp_model_name TEXT, model_type TEXT, '
        "fixed_monthly REAL, variable_oil REAL, variable_gas REAL, variable_water REAL)"
    ),
    (
        'CREATE TABLE "Forecast" (prop_id TEXT, model TEXT, phase TEXT, start DATE, '
        "type_curve TEXT, rate_init REAL, decline_init REAL, b_factor REAL, decline_min REAL)"
    ),
    'CREATE TABLE "Interest" (prop_id TEXT, model TEXT, start DATE, wi_pct REAL, nri_pct REAL)',
    (
        'CREATE TABLE "Completion" (prop_id TEXT, frac_proppant_lb REAL, frac_fluid_bbl REAL, '
        "frac_stages INTEGER)"
    ),
    (
        'CREATE TABLE "RunSettings" (settings_name TEXT, effective_date DATE, '
        "cutoff_method TEXT, abandon_timing TEXT, afit_enabled BOOLEAN, "
        "fed_corp_tax_rate REAL, tangible_recovery_period_yrs INTEGER, idc_method TEXT, "
        "capex_tangible_percents TEXT)"
    ),
    'CREATE TABLE "Scenario" (scenario TEXT, forecast_model_name TEXT, price_model_name TEXT)',
    (
        'CREATE TABLE "ShrinkYieldModel" (shrink_yield_model_name TEXT, gas_shrink_frac REAL, '
        "ngl_yield_bbl_mmscf REAL, condensate_yield_bbl_mmscf REAL)"
    ),
    (
        'CREATE TABLE "TaxModel" (tax_model_name TEXT, model_type TEXT, sev_tax_oil REAL, '
        "sev_tax_gas REAL, sev_tax_ngl REAL, ad_valorum_tax REAL)"
    ),
    (
        'CREATE TABLE "WellModels" (prop_id TEXT, scenario TEXT, exp_model_name TEXT, '
        "capex_model_name TEXT, diff_model_name TEXT, tax_model_name TEXT, "
        "shrink_yield_model_name TEXT, interest_model_name TEXT)"
    ),
    'CREATE TABLE "PredictorModel" (name TEXT, model BLOB, training_apis TEXT)',
    'CREATE TABLE "PredictorAssignment" (prop_id TEXT, model_name TEXT)',
    'CREATE UNIQUE INDEX idx_Monthly on Monthly("prop_id", "month")',
    'CREATE UNIQUE INDEX idx_Daily on Daily("prop_id", "date")',
    'CREATE UNIQUE INDEX idx_TypeCurve on TypeCurve("name", "phase")',
    'CREATE UNIQUE INDEX idx_Abandonment on Abandonment("prop_id", "model")',
    'CREATE INDEX idx_Capex on Capex("prop_id", "model", "date", "job_type")',
    'CREATE INDEX idx_PriceModel on PriceModel("price_model_name", "start_date")',
    'CREATE UNIQUE INDEX idx_Survey on Survey("prop_id", "point_md")',
    'CREATE UNIQUE INDEX idx_Reservoir on Reservoir("prop_id", "reservoir")',
    'CREATE UNIQUE INDEX idx_Metadata on Metadata("type")',
    'CREATE UNIQUE INDEX idx_Casing on Casing("prop_id", "casing_od_in")',
    'CREATE UNIQUE INDEX idx_Perfs on Perfs("prop_id", "perf_start_md_ft")',
    "CREATE UNIQUE INDEX idx_Main ON Main (prop_id)",
    "CREATE UNIQUE INDEX idx_DiffModel ON DiffModel (diff_model_name, start_date)",
    "CREATE UNIQUE INDEX idx_ExpenseModel ON ExpenseModel (exp_model_name, model_type)",
    "CREATE UNIQUE INDEX idx_Forecast ON Forecast (prop_id, model, phase, start)",
    "CREATE UNIQUE INDEX idx_Interest ON Interest (prop_id, model, start)",
    "CREATE UNIQUE INDEX idx_Completion ON Completion (prop_id)",
    "CREATE UNIQUE INDEX idx_RunSettings ON RunSettings (settings_name)",
    "CREATE UNIQUE INDEX idx_Scenario ON Scenario (scenario)",
    "CREATE UNIQUE INDEX idx_ShrinkYieldModel ON ShrinkYieldModel (shrink_yield_model_name)",
    "CREATE UNIQUE INDEX idx_TaxModel ON TaxModel (tax_model_name, model_type)",
    "CREATE UNIQUE INDEX idx_WellModels ON WellModels (prop_id, scenario)",
    "CREATE UNIQUE INDEX idx_PredictorModel ON PredictorModel (name)",
    "CREATE UNIQUE INDEX idx_PredictorAssignment ON PredictorAssignment (prop_id)",
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


def create_obsidian_2026_02_blank_schema(path: Path | str) -> None:
    """Build the schema of the blank database Obsidian wrote until October 2026."""
    exec_sql(path, _OBSIDIAN_2026_02_BLANK_SCHEMA)


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
