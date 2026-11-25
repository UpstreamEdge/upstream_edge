"""`Database.snapshot()`: a restore point where Obsidian keeps its versions."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime

import pytest
from conftest import create_bookkeeping_schema, create_core_schema, exec_sql, last_modified

from upstream_edge.obsidian_db import (
    Database,
    DatabaseLockedError,
    ForecastSegment,
    Phase,
    ReadOnlyDatabaseError,
    RsvCat,
    ValidationError,
)
from upstream_edge.obsidian_db import database as database_module

SEGMENT = ForecastSegment(
    start=date(2026, 1, 1), rate_init=500.0, decline_init=0.6, b_factor=1.1, decline_min=0.06
)


def make(tmp_path, name="Field.obsdb"):
    path = tmp_path / name
    create_core_schema(path)
    create_bookkeeping_schema(path)
    exec_sql(path, ["insert into Metadata values ('Branch', 'MAIN')"])
    with Database.open(path, change_log=False) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
    return path


def metadata(path):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("select type, value from Metadata").fetchall())
    finally:
        conn.close()


def at(monkeypatch, stamp):
    """Hold the clock, as a snapshot reads it, to one minute."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.strptime(stamp, "%Y%m%d_%H%M")

    monkeypatch.setattr(database_module, "datetime", Clock)


def test_a_snapshot_is_a_version_obsidian_lists(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        copy = db.snapshot()
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])  # after the snapshot

    assert copy.parent == tmp_path / "_Archive"
    assert re.fullmatch(r"Field_\d{8}_\d{4}\.obsdb", copy.name), copy.name
    with Database.open(copy, read_only=True) as version:
        assert [well.prop_id for well in version.wells()] == ["P1"]
        assert version.forecasts() == [], "the state when it was taken"
    conn = sqlite3.connect(copy)
    assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    conn.close()


def test_the_database_records_the_snapshot_as_its_parent(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["insert into Metadata values ('Parent', 'ORIGINAL DATABASE')"])
    edited = last_modified(path)
    with Database.open(path) as db:
        first = db.snapshot()
    assert metadata(path) == {"Branch": "MAIN", "Parent": first.stem, "LastModified": edited[0]}
    # The copy was taken before the stamp, so it still names its own parent.
    assert metadata(first)["Parent"] == "ORIGINAL DATABASE"
    assert last_modified(path) == edited, "a snapshot is not an edit of the data"


def test_a_file_with_no_parent_row_gets_one(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        copy = db.snapshot()
    assert metadata(path)["Parent"] == copy.stem
    assert "Parent" not in metadata(copy)


def test_snapshots_in_one_minute_get_obsidians_letters(tmp_path, monkeypatch):
    path = make(tmp_path)
    at(monkeypatch, "20261124_1530")
    with Database.open(path) as db:
        names = [db.snapshot().name for _ in range(3)]
    assert names == [
        "Field_20261124_1530.obsdb",
        "Field_20261124_1530B.obsdb",
        "Field_20261124_1530C.obsdb",
    ]
    assert metadata(path)["Parent"] == "Field_20261124_1530C"


def test_a_version_in_the_newer_folder_takes_the_name_too(tmp_path, monkeypatch):
    """A later Obsidian keeps versions in `_ObsidianVersions/<name>/`; no two
    versions share a name across the two folders."""
    path = make(tmp_path)
    newer = tmp_path / "_ObsidianVersions" / "Field"
    newer.mkdir(parents=True)
    (newer / "Field_20261124_1530.obsdb").write_bytes(b"")
    (newer / "Field_20261124_1530B.OBSDB").write_bytes(b"")
    (newer / "Field_20261124_1530C.txt").write_bytes(b"")  # not a database
    at(monkeypatch, "20261124_1530")
    with Database.open(path) as db:
        assert db.snapshot() == tmp_path / "_Archive" / "Field_20261124_1530C.obsdb"


def test_the_tenth_snapshot_of_a_minute_is_x_and_the_next_is_refused(tmp_path, monkeypatch):
    path = make(tmp_path)
    at(monkeypatch, "20261124_1530")
    with Database.open(path) as db:
        names = [db.snapshot().stem[-1] for _ in range(11)]
        assert names == ["0", "B", "C", "D", "E", "F", "G", "H", "I", "J", "X"]
        parent = metadata(path)["Parent"]
        tenth = (tmp_path / "_Archive" / "Field_20261124_1530X.obsdb").read_bytes()
        with pytest.raises(FileExistsError, match="1530X"):
            db.snapshot()
        assert metadata(path)["Parent"] == parent
        assert len(list((tmp_path / "_Archive").iterdir())) == 11
        assert (tmp_path / "_Archive" / "Field_20261124_1530X.obsdb").read_bytes() == tenth


def test_a_snapshot_that_cannot_record_itself_leaves_no_copy(tmp_path):
    """Another writer holds the lock: the Parent stamp cannot be written, so
    the copy is removed rather than left as a version nothing names."""
    path = make(tmp_path)
    before = metadata(path)
    other = sqlite3.connect(path, isolation_level=None)
    try:
        conn = sqlite3.connect(path, isolation_level=None, timeout=0.2)
        conn.row_factory = sqlite3.Row
        db = Database(path, conn)
        other.execute("BEGIN")
        other.execute("UPDATE Main SET lease = 'UNCOMMITTED'")
        with pytest.raises(DatabaseLockedError):
            db.snapshot()
        db.close()
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert list((tmp_path / "_Archive").iterdir()) == []
    assert metadata(path) == before


@pytest.mark.parametrize("name", ["with space.obsdb", "it's.obsdb", "pound#sign.obsdb", "old.db"])
def test_a_snapshot_of_any_file_name(tmp_path, name):
    path = make(tmp_path, name)
    with Database.open(path) as db:
        copy = db.snapshot()
    assert copy.name.startswith(path.stem + "_") and copy.suffix == ".obsdb"
    assert copy.is_file()


def test_a_snapshot_from_a_relative_path(tmp_path, monkeypatch):
    make(tmp_path)
    monkeypatch.chdir(tmp_path)
    with Database.open("Field.obsdb") as db:
        copy = db.snapshot()
    assert copy.is_absolute() and copy.parent == tmp_path.resolve() / "_Archive"


def test_what_a_snapshot_refuses(tmp_path):
    path = make(tmp_path)
    with Database.open(path, read_only=True) as db:
        with pytest.raises(ReadOnlyDatabaseError, match="read_only=True"):
            db.snapshot()
    with Database.open(path) as db:
        with db.transaction():
            with pytest.raises(RuntimeError, match="inside a transaction"):
                db.snapshot()
    with Database.open(":memory:") as db, pytest.raises(ValidationError, match="in-memory"):
        db.snapshot()
    closed = Database.open(path)
    closed.close()
    with pytest.raises(RuntimeError, match="closed"):
        closed.snapshot()
    assert not (tmp_path / "_Archive").exists(), "a refused snapshot leaves nothing behind"


def test_a_file_without_metadata_is_copied_and_left_alone(tmp_path):
    path = tmp_path / "bare.obsdb"
    create_core_schema(path)
    before = path.read_bytes()
    with Database.open(path) as db:
        copy = db.snapshot()
    assert copy.is_file()
    assert path.read_bytes() == before
