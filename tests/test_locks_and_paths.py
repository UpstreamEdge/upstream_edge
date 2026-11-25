"""A commit another process holds off, and the paths a database is opened by:
network shares, long-path prefixes, and a working directory that changes."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from conftest import create_bookkeeping_schema, create_core_schema

from upstream_edge.obsidian_db import Database, DatabaseLockedError, RsvCat
from upstream_edge.obsidian_db.database import (
    _read_only_uri,  # pyright: ignore[reportPrivateUsage]
)


def make(folder: Path, name: str = "Field.obsdb", *, lease: str = "HOME") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    create_core_schema(path)
    create_bookkeeping_schema(path)
    with Database.open(path, change_log=False) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP, lease=lease)
    return path


def impatient(path: Path) -> Database:
    """A database whose connection gives up on a lock at once."""
    conn = sqlite3.connect(path, isolation_level=None, timeout=0.1)
    conn.row_factory = sqlite3.Row
    return Database(path, conn)


# ── a commit that cannot finish ──────────────────────────────────────────────


def test_a_commit_held_off_by_a_reader_is_rolled_back_and_the_connection_stays_usable(tmp_path):
    """In SQLite's default journal mode a reader elsewhere blocks a commit.
    SQLite then leaves the transaction open; the library must end it."""
    path = make(tmp_path)
    db = impatient(path)
    conn = db._ensure_open()  # pyright: ignore[reportPrivateUsage]
    reader = sqlite3.connect(path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM Main").fetchone()  # holds a read lock
        with pytest.raises(DatabaseLockedError):
            db.set_well_header("P1", lease="BLOCKED")
        assert conn.in_transaction is False, "the half-done transaction was ended"
        reader.execute("COMMIT")

        # Nothing is locked: another connection writes, then this one does.
        other = sqlite3.connect(path, isolation_level=None, timeout=0.1)
        other.execute("UPDATE Main SET operator = 'OTHER'")
        other.close()
        db.set_well_header("P1", lease="AFTER")
        (well,) = db.wells()
        assert (well.lease, well.operator) == ("AFTER", "OTHER")
    finally:
        reader.close()
        db.close()


def test_a_blocked_commit_inside_a_transaction_block_writes_nothing(tmp_path):
    path = make(tmp_path)
    db = impatient(path)
    reader = sqlite3.connect(path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM Main").fetchone()
        with pytest.raises(DatabaseLockedError), db.transaction():
            db.set_well_header("P1", lease="ONE")
            db.add_well("P2", rsv_cat=RsvCat.PUD)
        reader.execute("COMMIT")
        assert [(well.prop_id, well.lease) for well in db.wells()] == [("P1", "HOME")]
        with db.transaction():  # a new transaction starts cleanly
            db.add_well("P3", rsv_cat=RsvCat.PUD)
        assert len(db.wells()) == 2
    finally:
        reader.close()
        db.close()


# ── the read-only URI ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "uri"),
    [
        (r"C:\data\Field.obsdb", "file:///C:/data/Field.obsdb?mode=ro"),
        (r"C:\my data\a#b%20?.obsdb", "file:///C:/my%20data/a%23b%2520%3F.obsdb?mode=ro"),
        # A share: an empty host, then the server as the start of the path.
        (r"\\server\share\dir\Field.obsdb", "file:////server/share/dir/Field.obsdb?mode=ro"),
        (r"\\server\c$\x y\Field.obsdb", "file:////server/c%24/x%20y/Field.obsdb?mode=ro"),
        # The long-path prefixes name the same files.
        ("\\\\?\\C:\\data\\Field.obsdb", "file:///C:/data/Field.obsdb?mode=ro"),
        ("\\\\?\\UNC\\server\\share\\Field.obsdb", "file:////server/share/Field.obsdb?mode=ro"),
    ],
)
def test_the_read_only_uri_of_a_windows_path(path, uri):
    assert _read_only_uri(path, windows=True) == uri


@pytest.mark.parametrize(
    ("path", "uri"),
    [
        ("/home/me/Field.obsdb", "file:///home/me/Field.obsdb?mode=ro"),
        ("/home/my data/a#b?.obsdb", "file:///home/my%20data/a%23b%3F.obsdb?mode=ro"),
        # A backslash is an ordinary character in a POSIX file name.
        ("/home/me/odd\\name.obsdb", "file:///home/me/odd%5Cname.obsdb?mode=ro"),
    ],
)
def test_the_read_only_uri_of_a_posix_path(path, uri):
    assert _read_only_uri(path, windows=False) == uri


@pytest.mark.skipif(os.name != "nt", reason="a Windows administrative share")
def test_a_read_only_open_over_a_network_share(tmp_path):
    path = make(tmp_path)
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    share = Path(f"\\\\localhost\\{drive[0]}$" + rest)
    if not share.exists():
        pytest.skip("the administrative share is not reachable from this account")
    with Database.open(share, read_only=True) as db:
        assert [well.prop_id for well in db.wells()] == ["P1"]
    with Database.open("\\\\?\\" + os.path.abspath(path), read_only=True) as db:
        assert len(db.wells()) == 1


# ── a working directory that changes ─────────────────────────────────────────


def test_a_snapshot_goes_beside_the_file_that_was_opened(tmp_path, monkeypatch):
    here = make(tmp_path / "A", lease="FROM A")
    there = make(tmp_path / "B", lease="FROM B")
    monkeypatch.chdir(here.parent)
    with Database.open("Field.obsdb") as db:
        monkeypatch.chdir(there.parent)  # another Field.obsdb lives here
        copy = db.snapshot()
    assert copy.parent == (tmp_path / "A" / "_Archive").resolve() or copy.parent == Path(
        os.path.abspath(tmp_path / "A" / "_Archive")
    )
    assert not (tmp_path / "B" / "_Archive").exists()
    with Database.open(copy, read_only=True) as version:
        assert version.wells()[0].lease == "FROM A"


def test_a_relative_read_only_open_keeps_its_file_too(tmp_path, monkeypatch):
    here = make(tmp_path / "A", lease="FROM A")
    make(tmp_path / "B", lease="FROM B")
    monkeypatch.chdir(here.parent)
    with Database.open("Field.obsdb", read_only=True) as db:
        monkeypatch.chdir(tmp_path / "B")
        assert db.wells()[0].lease == "FROM A"
        assert db.path == Path("Field.obsdb"), "the path is reported as it was given"


# ── a version another process wrote ──────────────────────────────────────────


def test_a_name_taken_since_it_was_chosen_is_left_alone(tmp_path, monkeypatch):
    """Obsidian, or another script, can write a version under this minute's
    name between the moment it is chosen and the copy. Theirs is not touched."""
    from upstream_edge.obsidian_db import database as database_module

    path = make(tmp_path)
    theirs = tmp_path / "_Archive" / "Field_20261124_1530.obsdb"
    theirs.parent.mkdir()
    theirs.write_bytes(b"another process's version, half written")
    monkeypatch.setattr(database_module, "_next_version_stamp", lambda *_: "20261124_1530")
    with Database.open(path) as db:
        with pytest.raises(FileExistsError, match="already exists"):
            db.snapshot()
    assert theirs.read_bytes() == b"another process's version, half written"
    conn = sqlite3.connect(path)
    parent = conn.execute("SELECT value FROM Metadata WHERE type = 'Parent'").fetchall()
    conn.close()
    assert parent == [], "the database does not claim the other process's version"


class _CopyFails:
    """A connection whose copy to a snapshot fails as a damaged file's does."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        if sql.startswith("VACUUM INTO"):
            raise sqlite3.DatabaseError("database disk image is malformed")
        return self._conn.execute(sql, parameters)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def test_a_copy_that_fails_leaves_no_empty_version_behind(tmp_path):
    path = make(tmp_path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    db = Database(path, _CopyFails(conn))  # type: ignore[arg-type]
    try:
        with pytest.raises(sqlite3.DatabaseError, match="malformed"):
            db.snapshot()
        assert list((tmp_path / "_Archive").iterdir()) == []
    finally:
        conn.close()
