from __future__ import annotations

import sqlite3
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


def test_wells_treats_empty_strings_as_missing(tmp_path):
    # Obsidian databases may store missing optional values as empty strings
    # rather than NULL; readers must map them to None instead of crashing.
    db_path = tmp_path / "empty.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            (
                "insert into Main values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "P1",
                    "",
                    "PDP",
                    "MITCHELL",
                    "1H",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ),
            ),
        ],
    )

    with Database.open(db_path) as db:
        wells = db.wells()

    assert len(wells) == 1
    well = wells[0]
    assert well.api_10 is None
    assert well.field is None
    assert well.tvd_ft is None
    assert well.spud is None
    assert well.surface_latitude is None
    assert well.bh_longitude is None


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


def test_production_monthly_reads_native_yyyymm_dates(tmp_path):
    db_path = tmp_path / "yyyymm.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Monthly (prop_id text, month text, oil_monthly_bbl real, "
                "gas_monthly_mscf real, water_monthly_bbl real)"
            ),
            ("insert into Monthly values (?, ?, ?, ?, ?)", ("P1", "202306", 10.0, 20.0, 30.0)),
            ("insert into Monthly values (?, ?, ?, ?, ?)", ("P1", "2023-07-01", 11.0, 21.0, 31.0)),
        ],
    )

    with Database.open(db_path) as db:
        rows = db.production_monthly("P1")

    assert sorted(row.month for row in rows) == [date(2023, 6, 1), date(2023, 7, 1)]

    bad_path = tmp_path / "yyyymm_bad.obsdb"
    exec_sql(
        bad_path,
        [
            (
                "create table Monthly (prop_id text, month text, oil_monthly_bbl real, "
                "gas_monthly_mscf real, water_monthly_bbl real)"
            ),
            ("insert into Monthly values (?, ?, ?, ?, ?)", ("P1", "202313", 1.0, 2.0, 3.0)),
        ],
    )
    with Database.open(bad_path) as db:
        with pytest.raises(DataIntegrityError, match="YYYYMM"):
            db.production_monthly("P1")


# Obsidian's own table of spellings (its `rsvcat.rs` tests), each also with a
# leading ARIES sort digit, plus the whole-text ones and the fallback.
_RSV_CAT_SPELLINGS = [
    ("PDP", RsvCat.PDP),
    ("PDNP", RsvCat.PDNP),
    ("DNP", RsvCat.PDNP),
    ("PNP", RsvCat.PDNP),
    ("PDBP", RsvCat.PDNP),
    ("COMP", RsvCat.DUC),
    ("DUC", RsvCat.DUC),
    ("PERM", RsvCat.PUD),
    ("PPERM", RsvCat.PROB),
    ("UND", RsvCat.LOC),
    ("SI", RsvCat.SHUT_IN),
    ("PUD", RsvCat.PUD),
    ("PROB", RsvCat.PROB),
    ("POSS", RsvCat.POSS),
    ("LOC", RsvCat.LOC),
    ("TA", RsvCat.TA),
    ("SWD", RsvCat.SWD),
    ("DATA", RsvCat.DATA),
    ("PVPD", RsvCat.PDP),
    ("PA", RsvCat.PA),
]


@pytest.mark.parametrize(
    ("stored", "rsv_cat"),
    [
        *_RSV_CAT_SPELLINGS,
        *[(f"{digit}{text}", rsv_cat) for digit, (text, rsv_cat) in enumerate(_RSV_CAT_SPELLINGS)],
        ("pdp", RsvCat.PDP),
        ("  ShutIn ", RsvCat.SHUT_IN),
        ("PDP_SI", RsvCat.SHUT_IN),
        ("PDNP_SI", RsvCat.SHUT_IN),
        ("NP", RsvCat.SHUT_IN),
        ("Inactive", RsvCat.SHUT_IN),
        ("Producing", RsvCat.PDP),
        ("Active Producer", RsvCat.PDP),
        ("Proved Developed Non-Producing", RsvCat.PDNP),
        ("BP-PROV", RsvCat.PDNP),
        ("Drilling", RsvCat.DUC),
        ("Completed - Not Active", RsvCat.DUC),
        ("Completed", RsvCat.PDP),
        ("Permitted", RsvCat.PUD),
        ("Expired Permit", RsvCat.LOC),
        ("T/A", RsvCat.TA),
        ("P & A", RsvCat.PA),
        ("P&A", RsvCat.PA),
        ("Plugged", RsvCat.PA),
        ("Abandoned", RsvCat.PA),
        ("WIP", RsvCat.PUD),
        ("Salt Water Disposal", RsvCat.SWD),
        ("Data", RsvCat.DATA),
        ("Blank", RsvCat.UNSPECIFIED),
        ("BAD", RsvCat.UNSPECIFIED),
        ("", RsvCat.UNSPECIFIED),
        (None, RsvCat.UNSPECIFIED),
        (7, RsvCat.UNSPECIFIED),
    ],
)
def test_a_reserve_category_reads_as_obsidian_reads_it(tmp_path, stored, rsv_cat):
    """Obsidian reads any text in Main.rsv_cat; so does the library, and one
    odd spelling never fails `wells()` for the whole table."""
    db_path = tmp_path / "spellings.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            ("insert into Main (prop_id, rsv_cat) values (?, ?)", ("P1", stored)),
            ("insert into Main (prop_id, rsv_cat) values (?, ?)", ("P2", "PDP")),
        ],
    )

    with Database.open(db_path) as db:
        first, second = db.wells()
        assert first.rsv_cat is rsv_cat
        assert second.rsv_cat is RsvCat.PDP
        well = db.well("P1")
        assert well is not None and well.rsv_cat is rsv_cat


def test_reading_an_odd_reserve_category_does_not_rewrite_it(tmp_path):
    db_path = tmp_path / "kept.obsdb"
    exec_sql(
        db_path,
        [MAIN_SCHEMA, ("insert into Main (prop_id, rsv_cat) values (?, ?)", ("P1", "1PDP_SI"))],
    )
    with Database.open(db_path, change_log=False) as db:
        assert db.wells()[0].rsv_cat is RsvCat.SHUT_IN
        db.set_well_header("P1", lease="SMITH")
    conn = sqlite3.connect(db_path)
    assert conn.execute("select rsv_cat, lease from Main").fetchall() == [("1PDP_SI", "SMITH")]
    conn.close()


def test_reader_accepts_every_rsv_cat_value(tmp_path):
    # Every category Obsidian can write to Main.rsv_cat must read back as
    # itself. A spelling missing from the enum would read as a neighbor or as
    # unspecified (before 1.3 it failed the whole wells() call; PDNP did).
    db_path = tmp_path / "all_cats.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            *[
                (
                    "insert into Main (prop_id, rsv_cat) values (?, ?)",
                    (f"P{index}", category.value),
                )
                for index, category in enumerate(RsvCat)
            ],
        ],
    )

    with Database.open(db_path) as db:
        wells = db.wells()

    assert {w.rsv_cat for w in wells} == set(RsvCat)
    # Spot-check the exact spellings Obsidian writes, which are not all the
    # member names.
    assert {"PDP", "PDNP", "ShutIn", "P&A", "Blank"} <= {c.value for c in RsvCat}


def test_reservoir_and_perf_readers_allow_null_depths(tmp_path):
    db_path = tmp_path / "null_depths.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table Reservoir (prop_id text, reservoir text, top_depth_ft real, "
                "gross_thickness_ft real)"
            ),
            (
                "create table Perfs (prop_id text, perf_start_md_ft real, perf_end_md_ft real, producing integer)"
            ),
            ("insert into Reservoir values (?, ?, ?, ?)", ("P1", "COTTON VALLEY", None, None)),
            ("insert into Perfs values (?, ?, ?, ?)", ("P1", None, None, None)),
        ],
    )

    with Database.open(db_path) as db:
        reservoir = db.reservoirs("P1")[0]
        perf = db.perfs("P1")[0]

    assert reservoir.top_depth_ft is None
    assert perf.perf_start_md_ft is None
    assert perf.producing is False


def test_attribute_readers_tolerate_missing_table(tmp_path):
    # Older databases may predate the WellAttributes table entirely.
    db_path = tmp_path / "no_attrs.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            ("insert into Main (prop_id, rsv_cat) values (?, ?)", ("P1", "PDP")),
        ],
    )

    with Database.open(db_path) as db:
        assert db.list_attribute_columns() == []
        assert db.well_attributes("P1") == {}
        assert db.all_well_attributes() == {}


def test_reader_wraps_non_numeric_values(tmp_path):
    db_path = tmp_path / "bad_float.obsdb"
    exec_sql(
        db_path,
        [
            MAIN_SCHEMA,
            (
                "insert into Main (prop_id, rsv_cat, surface_latitude) values (?, ?, ?)",
                ("P1", "PDP", "abc"),
            ),
        ],
    )

    with Database.open(db_path) as db:
        with pytest.raises(DataIntegrityError, match="surface_latitude"):
            db.wells()
