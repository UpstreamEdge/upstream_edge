from __future__ import annotations

from datetime import date

import pytest
from conftest import exec_sql

from upstream_edge.obsidian_db import Database, DataIntegrityError, RsvCat

MAIN_SCHEMA = (
    "create table Main (prop_id text, api_10 text, rsv_cat text, lease text, "
    'well_number text, field text, operator text, category text, "group" text, '
    "reservoir text, tvd real, md real, lateral_length real, spud text, completion text, "
    "first_prod text, state text, county text, surface_latitude real, surface_longitude real, "
    "bh_latitude real, bh_longitude real)"
)


def test_wells_and_single_well_mapping(tmp_path):
    db_path = tmp_path / "readers.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            (
                "insert into Main values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "P1",
                    "1234567890",
                    "PUD",
                    "MITCHELL",
                    "1H",
                    "Field",
                    "Operator",
                    "Cat",
                    "Group",
                    "Wolfcamp",
                    10000.0,
                    15000.0,
                    7500.0,
                    "2024-01-01",
                    "2024-02-01",
                    "2024-03-01",
                    "TX",
                    "Midland",
                    31.0,
                    -102.0,
                    31.1,
                    -102.1,
                ),
            ),
        ],
    )

    with Database.open(db_path) as db:
        well = db.well("P1")

    assert well is not None
    assert well.rsv_cat is RsvCat.PUD
    assert well.well_name == "MITCHELL 1H"
    assert well.first_prod == date(2024, 3, 1)


def test_well_by_api_and_absent_well(tmp_path):
    db_path = tmp_path / "api.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            (
                "insert into Main (prop_id, api_10, rsv_cat, lease) values (?, ?, ?, ?)",
                ("P2", "1234567890", "PDP", "MITCHELL"),
            ),
        ],
    )

    with Database.open(db_path) as db:
        assert db.well("missing") is None
        assert db.well_by_api("1234567890").prop_id == "P2"  # type: ignore[union-attr]


def test_production_readers_filter_and_parse_dates(tmp_path):
    db_path = tmp_path / "prod.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Monthly (prop_id text, month text, oil_monthly_bbl real, "
                "gas_monthly_mscf real, water_monthly_bbl real)"
            ),
            (
                "create table Daily (prop_id text, date text, oil_bopd real, gas_mcfd real, "
                "water_bwpd real)"
            ),
            (
                "insert into Monthly values (?, ?, ?, ?, ?)",
                ("P1", "2024-01-01", 10.0, 20.0, None),
            ),
            (
                "insert into Monthly values (?, ?, ?, ?, ?)",
                ("P2", "2024-01-01", 30.0, 40.0, 50.0),
            ),
            (
                "insert into Daily values (?, ?, ?, ?, ?)",
                ("P1", "2024-01-02", 1.0, 2.0, 3.0),
            ),
        ],
    )

    with Database.open(db_path) as db:
        monthly = db.production_monthly("P1")
        daily = db.production_daily()

    assert monthly[0].month == date(2024, 1, 1)
    assert monthly[0].water_bbl is None
    assert [row.prop_id for row in daily] == ["P1"]
    assert daily[0].date == date(2024, 1, 2)


def test_reader_rejects_unknown_enum(tmp_path):
    db_path = tmp_path / "bad.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            ("insert into Main (prop_id, rsv_cat) values (?, ?)", ("P1", "BAD")),
        ],
    )

    with Database.open(db_path) as db:
        with pytest.raises(DataIntegrityError, match="rsv_cat"):
            db.wells()
