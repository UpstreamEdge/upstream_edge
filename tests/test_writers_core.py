from __future__ import annotations

from datetime import date

import pytest
from conftest import create_core_schema

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
    assert interest.wi_pct == 1.0
    assert interest.nri_pct == 0.75
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

        db.delete_daily_prod("P1")
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
            db.delete_forecast(model="BASE")
        db.delete_forecast("P1", model="BASE", phase=Phase.OIL)

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
            [InterestSegment(start=date(2026, 1, 1), wi_pct=0.8, nri_pct=0.6)],
        )

        p1 = db.interest("P1", "CASE_A")[0]
        p2 = db.interest("P2", "CASE_A")[0]

    assert p1.wi_pct == 0.8
    assert p1.nri_pct == 0.6
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
        with pytest.raises(ValidationError, match="between 0 and 1"):
            db.set_interest(
                "P1",
                "MAIN",
                [InterestSegment(start=date(2026, 1, 1), wi_pct=1.2, nri_pct=0.6)],
            )

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_interest()
        db.delete_interest("P1", "MAIN")
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
            db.delete_capex()
        db.delete_capex("P1", "MAIN")
        assert db.capex("P1", "MAIN") == []


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

        db.delete_surveys("P1")
        db.delete_reservoir_data("P1", reservoir="Wolfcamp")
        db.delete_completion("P1", confirm=True)
        db.delete_perfs("P1")

        assert db.surveys("P1") == []
        assert db.reservoirs("P1") == []
        assert db.completions("P1") == []
        assert db.perfs("P1") == []


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
