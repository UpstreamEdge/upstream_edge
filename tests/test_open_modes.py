"""Opening for reading only, and files from other Obsidian versions."""

from __future__ import annotations

import sqlite3
import warnings
from datetime import date

import pytest
from conftest import create_bookkeeping_schema, create_core_schema, exec_sql

from upstream_edge.obsidian_db import (
    Database,
    ForecastSegment,
    MonthlyRow,
    ObsidianDbError,
    Phase,
    ReadOnlyDatabaseError,
    RsvCat,
)

SEGMENT = ForecastSegment(
    start=date(2026, 1, 1), rate_init=500.0, decline_init=0.6, b_factor=1.1, decline_min=0.06
)


def make(tmp_path, name="field.obsdb", *, version=None):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    create_core_schema(path)
    create_bookkeeping_schema(path)
    with Database.open(path, change_log=False) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
    if version is not None:
        exec_sql(path, [f"PRAGMA user_version = {version}"])
    return path


# Every kind of writer: one that logs, one that does not, a delete, a schema change.
WRITES = [
    lambda db: db.set_forecast("P1", "MAIN", Phase.GAS, [SEGMENT]),
    lambda db: db.set_well_header("P1", lease="SMITH"),
    lambda db: db.add_well("P2", rsv_cat=RsvCat.PUD),
    lambda db: db.delete_well("P1", confirm=True),
    lambda db: db.delete_forecast(confirm=True),
    lambda db: db.set_monthly_prod(
        [MonthlyRow(prop_id="P1", month=date(2026, 1, 1), oil_bbl=1.0, gas_mscf=1.0, water_bbl=1.0)]
    ),
    lambda db: db.delete_monthly_prod(confirm=True),
    lambda db: db.set_abandonment("P1", "MAIN", 1.0),
]


# ── read-only ────────────────────────────────────────────────────────────────


def test_a_read_only_database_reads(tmp_path):
    path = make(tmp_path)
    with Database.open(path, read_only=True) as db:
        assert db.read_only is True
        assert [well.prop_id for well in db.wells()] == ["P1"]
        assert len(db.forecasts("P1")) == 1


@pytest.mark.parametrize("write", WRITES)
def test_a_read_only_database_refuses_every_write_and_the_file_is_untouched(tmp_path, write):
    path = make(tmp_path)
    before = path.read_bytes()
    with Database.open(path, read_only=True) as db:
        with pytest.raises(ReadOnlyDatabaseError, match="read_only=True"):
            write(db)
        assert [well.prop_id for well in db.wells()] == ["P1"]

    assert path.read_bytes() == before


def test_a_read_only_database_refuses_a_transaction(tmp_path):
    path = make(tmp_path)
    with Database.open(path, read_only=True) as db:
        with pytest.raises(ReadOnlyDatabaseError), db.transaction():
            pytest.fail("the block must not run")


def test_the_refusal_is_one_of_the_librarys_errors(tmp_path):
    path = make(tmp_path)
    with Database.open(path, read_only=True) as db, pytest.raises(ObsidianDbError):
        db.set_well_header("P1", lease="SMITH")


def test_a_read_only_connection_cannot_write_even_past_the_library(tmp_path):
    """The connection itself is read-only: SQLite refuses, not only the library."""
    path = make(tmp_path)
    with Database.open(path, read_only=True) as db:
        conn = db._ensure_open()  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE Main SET lease = 'X'")


def test_a_read_only_database_reads_while_another_connection_holds_the_write_lock(tmp_path):
    path = make(tmp_path)
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE Main SET lease = 'UNCOMMITTED'")
        with Database.open(path, read_only=True) as db:
            (well,) = db.wells()
            assert well.lease is None, "the uncommitted write is not seen"
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_a_read_only_database_never_blocks_a_writer(tmp_path):
    path = make(tmp_path)
    with Database.open(path, read_only=True) as reader:
        reader.wells()
        with Database.open(path) as writer:
            writer.set_well_header("P1", lease="SMITH")
        (well,) = reader.wells()
        assert well.lease == "SMITH"


@pytest.mark.parametrize(
    "name", ["with space.obsdb", "pound#sign.obsdb", "percent%20.obsdb", "sub dir/a&b=c.obsdb"]
)
def test_a_read_only_open_takes_any_file_name(tmp_path, name):
    path = make(tmp_path, name)
    with Database.open(path, read_only=True) as db:
        assert len(db.wells()) == 1
        assert db.path == path


def test_a_read_only_open_takes_a_string_and_a_relative_path(tmp_path, monkeypatch):
    make(tmp_path, "relative.obsdb")
    monkeypatch.chdir(tmp_path)
    with Database.open("relative.obsdb", read_only=True) as db:
        assert len(db.wells()) == 1


def test_a_read_only_open_of_a_missing_file_does_not_create_it(tmp_path):
    with pytest.raises(FileNotFoundError):
        Database.open(tmp_path / "missing.obsdb", read_only=True)
    assert not (tmp_path / "missing.obsdb").exists()


def test_an_in_memory_database_opened_read_only_refuses_writes():
    with Database.open(":memory:", read_only=True) as db:
        assert db.read_only is True
        with pytest.raises(ReadOnlyDatabaseError), db.transaction():
            pytest.fail("the block must not run")


def test_a_database_is_writable_by_default(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        assert db.read_only is False
        db.set_well_header("P1", lease="SMITH")
        assert db.wells()[0].lease == "SMITH"


# ── a file from another Obsidian version ─────────────────────────────────────


@pytest.mark.parametrize("version", [None, 0, 1, 2, 3, 99])
def test_the_files_schema_stamp_changes_nothing(tmp_path, version):
    """The library and Obsidian are not version-locked: a file stamped by an
    older or a newer Obsidian opens without a word, reads and writes."""
    path = make(tmp_path, version=version)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            assert db.read_only is False
            db.set_forecast("P1", "MAIN", Phase.GAS, [SEGMENT])
            db.set_well_header("P1", lease="SMITH")
            assert len(db.forecasts("P1")) == 2
            assert db.wells()[0].lease == "SMITH"

    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == (version or 0), "never restamped"
    conn.close()


def test_a_column_the_library_does_not_know_is_left_alone(tmp_path):
    """What a newer Obsidian adds survives on every row the library does not
    replace, and goes along when a well is copied."""
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.add_well("P2", rsv_cat=RsvCat.PDP)
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])
    exec_sql(
        path,
        [
            "ALTER TABLE Main ADD COLUMN from_the_future TEXT",
            "ALTER TABLE Forecast ADD COLUMN from_the_future TEXT",
            "UPDATE Main SET from_the_future = 'kept'",
            "UPDATE Forecast SET from_the_future = 'kept'",
        ],
    )

    with Database.open(path) as db:
        db.set_well_header("P1", lease="SMITH")  # an update: the row keeps the column
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])  # a replace: new rows for P1 oil
        db.copy_well("P2", "P2 COPY")
        assert len(db.wells()) == 3 and len(db.forecasts()) == 3

    conn = sqlite3.connect(path)
    main = dict(conn.execute("SELECT prop_id, from_the_future FROM Main").fetchall())
    forecast = dict(conn.execute("SELECT prop_id, from_the_future FROM Forecast").fetchall())
    conn.close()
    assert main == {"P1": "kept", "P2": "kept", "P2 COPY": "kept"}
    # The rows the library replaced are new rows; everyone else's are untouched.
    assert forecast == {"P1": None, "P2": "kept", "P2 COPY": "kept"}
