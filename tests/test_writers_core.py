from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from conftest import create_core_schema, exec_sql

from upstream_edge.obsidian_db import (
    CapexItem,
    CapexJobType,
    DailyRow,
    Database,
    DuplicateError,
    ForecastSegment,
    InterestSegment,
    MonthlyRow,
    PerfsInput,
    Phase,
    RsvCat,
    SurveyPointInput,
    ValidationError,
    WellNotFoundError,
)


def test_add_well_writes_default_cascade(tmp_path):
    db_path = tmp_path / "add_well.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well(
            "P1", rsv_cat=RsvCat.PUD, api_10="1234567890", lease="MITCHELL", well_number="1H"
        )

        well = db.well("P1")
        well_models = db.well_models("P1", "MAIN")[0]
        interest = db.interest("P1", "MAIN")[0]
        abandonment = db.abandonment("P1", "MAIN")[0]
        attrs = db.well_attributes("P1")

    assert well is not None
    assert well.well_name == "MITCHELL 1H"
    assert well_models.interest_model == "MAIN"
    assert interest.wi_pct == 100.0
    assert interest.nri_pct == 75.0
    assert abandonment.cost_gross == 100000.0
    assert attrs == {
        "Basin": "",
        "WorkingInterest": 0.0,
        "LeaseExpiry": date(2000, 1, 1),
    }


def test_add_well_duplicate_and_header_validation(tmp_path):
    db_path = tmp_path / "header.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(DuplicateError):
            db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(ValidationError, match="unknown field"):
            db.set_well_header("P1", leese="bad")

        with pytest.raises(ValidationError, match="at least one"):
            db.set_well_header("P1")

        with pytest.raises(WellNotFoundError):
            db.set_well_header("P2", lease="MITCHELL")

        db.set_well_header("P1", lease="Updated", first_prod=date(2025, 1, 1))
        assert db.well("P1").lease == "Updated"  # type: ignore[union-attr]


def test_delete_well_cascades_related_rows(tmp_path):
    db_path = tmp_path / "delete_well.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), 1.0, 2.0, 3.0)])
        db.set_forecast(
            "P1", "BASE", Phase.OIL, [ForecastSegment(date(2025, 1, 1), 1.0, 0.5, 1.0, 0.05)]
        )

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_well("P1", confirm=False)
        db.delete_well("P1", confirm=True)

        assert db.well("P1") is None
        assert db.production_monthly("P1") == []
        assert db.forecasts("P1") == []
        assert db.well_models("P1") == []
        assert db.interest("P1") == []
        with pytest.raises(WellNotFoundError):
            db.well_attributes("P1")


def test_copy_well_copies_prop_id_keyed_rows(tmp_path):
    db_path = tmp_path / "copy_well.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP, lease="MITCHELL")
        db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), 1.0, 2.0, 3.0)])
        db.set_well_attribute("P1", "Basin", "Midland")

        db.copy_well("P1", "P2")

        copied = db.well("P2")
        assert copied is not None
        assert copied.lease == "MITCHELL"
        assert db.production_monthly("P2")[0].oil_bbl == 1.0
        assert db.well_attributes("P2")["Basin"] == "Midland"

        with pytest.raises(DuplicateError):
            db.copy_well("P1", "P2")


def test_set_monthly_prod_upserts_and_backfills_header_dates(tmp_path):
    db_path = tmp_path / "monthly.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), 10.0, 20.0, None)])
        db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), 11.0, 21.0, 1.0)])

        rows = db.production_monthly("P1")
        well = db.well("P1")

    assert rows[0].oil_bbl == 11.0
    assert well is not None
    assert well.spud == date(2024, 5, 1)
    assert well.completion == date(2024, 5, 1)
    assert well.first_prod == date(2024, 5, 1)


def test_set_monthly_prod_stores_native_yyyymm_text(tmp_path):
    db_path = tmp_path / "monthly_fmt.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), 10.0, 20.0, 30.0)])

    conn = sqlite3.connect(db_path)
    try:
        stored = conn.execute("select month from Monthly").fetchone()[0]
    finally:
        conn.close()
    assert stored == "202405"


def test_writers_reject_raw_enum_strings(tmp_path):
    db_path = tmp_path / "raw_enums.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        with pytest.raises(ValidationError, match="rsv_cat must be a RsvCat"):
            db.add_well("P1", rsv_cat="NOT_A_CATEGORY")  # type: ignore[arg-type]

        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(ValidationError, match="rsv_cat must be a RsvCat"):
            db.set_well_header("P1", rsv_cat="PDP")
        with pytest.raises(ValidationError, match="phase must be a Phase"):
            db.set_forecast(
                "P1",
                "BASE",
                "Oil",  # type: ignore[arg-type]
                [ForecastSegment(date(2026, 1, 1), 1.0, 0.5, 1.0, 0.05)],
            )
        with pytest.raises(ValidationError, match="job_type must be a CapexJobType"):
            db.set_capex(
                "P1",
                "MAIN",
                [
                    CapexItem(
                        date=date(2026, 1, 1),
                        job_type="Pump Repair",  # type: ignore[arg-type]
                        cost_gross=1.0,
                    )
                ],
            )


def test_set_monthly_prod_validates_well_and_month(tmp_path):
    db_path = tmp_path / "monthly_bad.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        with pytest.raises(WellNotFoundError):
            db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), None, None, None)])

        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(ValidationError, match="first day"):
            db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 2), None, None, None)])


def test_daily_prod_upsert_and_delete_methods(tmp_path):
    db_path = tmp_path / "daily.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well("P2", rsv_cat=RsvCat.PDP)

        db.set_daily_prod([DailyRow("P1", date(2024, 5, 2), 1.0, 2.0, 3.0)])
        db.set_daily_prod([DailyRow("P1", date(2024, 5, 2), 4.0, 5.0, 6.0)])
        assert db.production_daily("P1")[0].oil_bopd == 4.0

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_daily_prod("P1")
        db.delete_daily_prod("P1", confirm=True)
        assert db.production_daily("P1") == []

        db.set_daily_prod([DailyRow("P2", date(2024, 5, 2), 1.0, 2.0, 3.0)])
        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_daily_prod()
        db.delete_daily_prod(confirm=True)
        assert db.production_daily() == []


def test_delete_monthly_prod_requires_confirm_for_broad_delete(tmp_path):
    db_path = tmp_path / "delete_monthly.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_monthly_prod([MonthlyRow("P1", date(2024, 5, 1), 1.0, 2.0, 3.0)])

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_monthly_prod()
        db.delete_monthly_prod(confirm=True)

        assert db.production_monthly() == []


def test_set_forecast_replaces_segments_and_delete_requires_confirm(tmp_path):
    db_path = tmp_path / "forecast_writer.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        db.set_forecast(
            "P1",
            "BASE",
            Phase.OIL,
            [ForecastSegment(date(2026, 1, 1), 100.0, 0.7, 1.1, 0.06)],
        )
        db.set_forecast(
            "P1",
            "BASE",
            Phase.OIL,
            [
                ForecastSegment(date(2026, 1, 1), 110.0, 0.7, 1.1, 0.06),
                ForecastSegment(date(2027, 1, 1), 90.0, 0.6, 1.0, 0.06),
            ],
        )

        rows = db.forecasts("P1", "BASE")
        assert [row.rate_init for row in rows] == [110.0, 90.0]

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_forecast("P1", model="BASE", phase=Phase.OIL)
        db.delete_forecast("P1", model="BASE", phase=Phase.OIL, confirm=True)

        assert db.forecasts("P1", "BASE") == []


def test_set_forecast_validates_empty_and_duplicate_segments(tmp_path):
    db_path = tmp_path / "forecast_bad.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(ValidationError, match="segments cannot be empty"):
            db.set_forecast("P1", "BASE", Phase.OIL, [])

        with pytest.raises(ValidationError, match="duplicate"):
            db.set_forecast(
                "P1",
                "BASE",
                Phase.OIL,
                [
                    ForecastSegment(date(2026, 1, 1), 100.0, 0.7, 1.1, 0.06),
                    ForecastSegment(date(2026, 1, 1), 90.0, 0.6, 1.0, 0.06),
                ],
            )


def test_set_interest_gap_fills_new_model(tmp_path):
    db_path = tmp_path / "interest.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well("P2", rsv_cat=RsvCat.PDP)

        db.set_interest(
            "P1",
            "CASE_A",
            [InterestSegment(start=date(2026, 1, 1), wi_pct=80.0, nri_pct=60.0)],
        )

        p1 = db.interest("P1", "CASE_A")[0]
        p2 = db.interest("P2", "CASE_A")[0]

    assert p1.wi_pct == 80.0
    assert p1.nri_pct == 60.0
    assert p2.start == date(2000, 1, 1)
    assert p2.wi_pct == 0.0
    assert p2.nri_pct == 0.0


def test_interest_delete_and_validation(tmp_path):
    db_path = tmp_path / "interest_delete.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(ValidationError, match="segments cannot be empty"):
            db.set_interest("P1", "MAIN", [])
        with pytest.raises(ValidationError, match="between 0 and 100"):
            db.set_interest(
                "P1",
                "MAIN",
                [InterestSegment(start=date(2026, 1, 1), wi_pct=120.0, nri_pct=60.0)],
            )

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_interest("P1", "MAIN")
        db.delete_interest("P1", "MAIN", confirm=True)
        assert db.interest("P1", "MAIN") == []


def test_set_capex_and_abandonment(tmp_path):
    db_path = tmp_path / "capex_abandonment.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        db.set_capex(
            "P1",
            "MAIN",
            [
                CapexItem(
                    date=date(2026, 1, 1),
                    job_type=CapexJobType.DRILLING,
                    cost_gross=1000.0,
                    description="drill",
                )
            ],
        )
        db.set_abandonment("P1", "MAIN", 125000.0)

        capex = db.capex("P1", "MAIN")[0]
        abandonment = db.abandonment("P1", "MAIN")[0]

        assert capex.job_type is CapexJobType.DRILLING
        assert abandonment.cost_gross == 125000.0

        with pytest.raises(ValidationError, match="items cannot be empty"):
            db.set_capex("P1", "MAIN", [])
        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_capex("P1", "MAIN")
        db.delete_capex("P1", "MAIN", confirm=True)
        assert db.capex("P1", "MAIN") == []

        # Abandonment is cleared by setting the cost to zero, not by deleting the
        # row; Obsidian keeps an abandonment cost for every well.
        db.set_abandonment("P1", "MAIN", 0.0)
        assert db.abandonment("P1", "MAIN")[0].cost_gross == 0.0


def test_geology_completion_writers_round_trip(tmp_path):
    db_path = tmp_path / "geo_writers.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        db.set_surveys(
            "P1",
            [
                SurveyPointInput(
                    point_md=200.0,
                    point_tvd=190.0,
                    azimuth_angle=1.0,
                    inclination_angle=2.0,
                    deviation_ns=3.0,
                    deviation_ew=4.0,
                ),
                SurveyPointInput(
                    point_md=100.0,
                    point_tvd=95.0,
                    azimuth_angle=1.0,
                    inclination_angle=2.0,
                    deviation_ns=3.0,
                    deviation_ew=4.0,
                ),
            ],
        )
        db.set_reservoir("P1", "Wolfcamp", top_depth_ft=9000.0, thickness_ft=250.0)
        db.set_completion("P1", frac_proppant_lb=1000000.0, frac_stages=40)
        db.set_perfs("P1", [PerfsInput(9500.0, 9700.0, True)])

        assert [point.point_md for point in db.surveys("P1")] == [100.0, 200.0]
        assert db.reservoirs("P1")[0].thickness_ft == 250.0
        assert db.completions("P1")[0].frac_stages == 40
        assert db.perfs("P1")[0].producing is True

        db.delete_surveys("P1", confirm=True)
        db.delete_reservoir_data("P1", reservoir="Wolfcamp", confirm=True)
        db.delete_completion("P1", confirm=True)
        db.delete_perfs("P1", confirm=True)

        assert db.surveys("P1") == []
        assert db.reservoirs("P1") == []
        assert db.completions("P1") == []
        assert db.perfs("P1") == []


def test_set_reservoir_preserves_unmanaged_columns(tmp_path):
    # Reservoir rows may carry geology columns the library does not manage;
    # updating top depth / thickness must not wipe them.
    db_path = tmp_path / "reservoir_preserve.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Main (prop_id text primary key, api_10 text, rsv_cat text not null, "
                'lease text, well_number text, field text, operator text, category text, "group" text, '
                "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
                "first_prod text, state text, county text, surface_latitude real, "
                "surface_longitude real, bh_latitude real, bh_longitude real)"
            ),
            (
                "create table Reservoir (prop_id text, reservoir text, top_depth_ft real, "
                "gross_thickness_ft real, porosity_pct real, matrix_perm_md real, "
                "pressure_init_psi real, sat_oil_init real, sat_gas_init real, sat_water_init real)"
            ),
            ("insert into Main (prop_id, rsv_cat) values (?, ?)", ("P1", "PDP")),
            (
                "insert into Reservoir values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("P1", "Wolfcamp", 9000.0, 250.0, 8.5, 0.1, 4200.0, 0.6, 0.2, 0.2),
            ),
        ],
    )

    with Database.open(db_path) as db:
        db.set_reservoir("P1", "Wolfcamp", top_depth_ft=9100.0, thickness_ft=260.0)
        reservoir = db.reservoirs("P1")[0]

    assert reservoir.top_depth_ft == 9100.0
    assert reservoir.thickness_ft == 260.0

    conn = sqlite3.connect(db_path)
    try:
        extras = conn.execute(
            "select porosity_pct, matrix_perm_md, pressure_init_psi, "
            "sat_oil_init, sat_gas_init, sat_water_init from Reservoir where prop_id = 'P1'"
        ).fetchone()
    finally:
        conn.close()
    assert extras == (8.5, 0.1, 4200.0, 0.6, 0.2, 0.2)


def test_geology_writers_validate_empty_inputs_and_confirm(tmp_path):
    db_path = tmp_path / "geo_validation.obsdb"
    create_core_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(ValidationError, match="points cannot be empty"):
            db.set_surveys("P1", [])
        with pytest.raises(ValidationError, match="perfs cannot be empty"):
            db.set_perfs("P1", [])
        with pytest.raises(ValidationError, match="at least one"):
            db.set_completion("P1")
        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_surveys()
        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_reservoir_data()
        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_completion("P1")
