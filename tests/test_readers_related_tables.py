from __future__ import annotations

from datetime import date

from conftest import exec_sql

from upstream_edge.obsidian_db import CapexJobType, Database


def test_per_well_economic_linkage_readers(tmp_path):
    db_path = tmp_path / "linkage.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table WellModels (prop_id text, scenario text, exp_model_name text, "
                "capex_model_name text, diff_model_name text, tax_model_name text, "
                "shrink_yield_model_name text, interest_model_name text)"
            ),
            (
                "create table Interest (prop_id text, model text, start text, wi_pct real, "
                "nri_pct real)"
            ),
            (
                "create table Capex (prop_id text, model text, date text, job_type text, "
                "cost_gross real, description text)"
            ),
            "create table Abandonment (prop_id text, model text, cost_gross real)",
            (
                "create table Scenario (scenario text, forecast_model_name text, "
                "price_model_name text)"
            ),
            (
                "insert into WellModels values (?, ?, ?, ?, ?, ?, ?, ?)",
                ("P1", "MAIN", "OPEX", "CAPEX", "DIFF", "TAX", "SY", "MAIN"),
            ),
            (
                "insert into Interest values (?, ?, ?, ?, ?)",
                ("P1", "MAIN", "2000-01-01", 1.0, 0.75),
            ),
            (
                "insert into Capex values (?, ?, ?, ?, ?, ?)",
                ("P1", "MAIN", "2026-01-01", "Drilling", 100.0, "AFE"),
            ),
            ("insert into Abandonment values (?, ?, ?)", ("P1", "MAIN", 100000.0)),
            ("insert into Scenario values (?, ?, ?)", ("MAIN", "BASE", "STRIP")),
        ],
    )

    with Database.open(db_path) as db:
        well_models = db.well_models("P1", "MAIN")[0]
        interest = db.interest("P1", "MAIN")[0]
        capex = db.capex("P1", "MAIN")[0]
        abandonment = db.abandonment("P1", "MAIN")[0]
        scenario = db.scenarios("MAIN")[0]

    assert well_models.exp_model == "OPEX"
    assert interest.start == date(2000, 1, 1)
    assert capex.job_type is CapexJobType.DRILLING
    assert abandonment.cost_gross == 100000.0
    assert scenario.forecast_model == "BASE"


def test_geology_completion_readers(tmp_path):
    db_path = tmp_path / "geo.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Survey (prop_id text, point_md real, point_tvd real, "
                "azimuth_angle real, inclination_angle real, deviation_ns real, deviation_ew real)"
            ),
            (
                "create table Reservoir (prop_id text, reservoir text, top_depth_ft real, "
                "gross_thickness_ft real)"
            ),
            (
                "create table Completion (prop_id text, frac_proppant_lb real, frac_fluid_bbl real, "
                "frac_stages integer)"
            ),
            (
                "create table Perfs (prop_id text, perf_start_md_ft real, perf_end_md_ft real, "
                "producing integer)"
            ),
            (
                "insert into Survey values (?, ?, ?, ?, ?, ?, ?)",
                ("P1", 100.0, 99.0, 1.0, 2.0, 3.0, 4.0),
            ),
            (
                "insert into Reservoir values (?, ?, ?, ?)",
                ("P1", "Wolfcamp", 9000.0, 250.0),
            ),
            (
                "insert into Completion values (?, ?, ?, ?)",
                ("P1", 1000000.0, 200000.0, 40),
            ),
            ("insert into Perfs values (?, ?, ?, ?)", ("P1", 9500.0, 9700.0, 1)),
        ],
    )

    with Database.open(db_path) as db:
        survey = db.surveys("P1")[0]
        reservoir = db.reservoirs("P1")[0]
        completion = db.completions("P1")[0]
        perfs = db.perfs("P1")[0]

    assert survey.point_md == 100.0
    assert reservoir.thickness_ft == 250.0
    assert completion.frac_stages == 40
    assert perfs.producing is True
