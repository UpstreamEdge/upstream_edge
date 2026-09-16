from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from conftest import create_attribute_schema, exec_sql

from upstream_edge.obsidian_db import (
    AttributeType,
    Database,
    RsvCat,
    ValidationError,
    WellAttributeUpdate,
)


def test_attribute_column_add_set_bulk_and_defaults(tmp_path):
    db_path = tmp_path / "attrs_write.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well("P2", rsv_cat=RsvCat.PDP)

        db.add_well_attribute_column("Basin", AttributeType.TEXT, default="Midland")
        db.add_well_attribute_column("Working Interest", AttributeType.NUMERIC, default=0.875)
        db.add_well_attribute_column("LeaseExpiry", AttributeType.DATE)
        db.set_well_attribute("P1", "Basin", "Delaware")
        db.set_well_attributes_bulk(
            [
                WellAttributeUpdate("P1", "Working Interest", 0.8),
                WellAttributeUpdate("P2", "LeaseExpiry", date(2030, 1, 1)),
            ]
        )

        p1 = db.well_attributes("P1")
        p2 = db.well_attributes("P2")

    assert p1["Basin"] == "Delaware"
    assert p1["Working Interest"] == 0.8
    assert p1["LeaseExpiry"] == date(2000, 1, 1)
    assert p2["Basin"] == "Midland"
    assert p2["LeaseExpiry"] == date(2030, 1, 1)


def test_attribute_rename_and_delete(tmp_path):
    db_path = tmp_path / "attrs_rename.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well_attribute_column("WellOpex", AttributeType.NUMERIC, default=2500.0)

        db.rename_well_attribute_column("WellOpex", "Lease Opex")

        assert [column.name for column in db.list_attribute_columns()] == ["Lease Opex"]

        db.delete_well_attribute_column("Lease Opex", confirm=True)

        assert [column.name for column in db.list_attribute_columns()] == []


def test_attribute_validation(tmp_path):
    db_path = tmp_path / "attrs_validation.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        for bad_name in ("", "_reserved", "prop_id", "Bad/Name"):
            with pytest.raises(ValidationError):
                db.add_well_attribute_column(bad_name, AttributeType.TEXT)

        db.add_well_attribute_column("NumericAttr", AttributeType.NUMERIC)
        with pytest.raises(ValidationError, match="must be float"):
            db.set_well_attribute("P1", "NumericAttr", "1.0")

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_well_attribute_column("NumericAttr", confirm=False)

        # inf/nan would be inlined into the ADD COLUMN DEFAULT clause, where
        # they are not valid SQL, and SQLite stores them as NULL in a cell.
        for bad_number in (float("inf"), float("nan")):
            with pytest.raises(ValidationError, match="finite"):
                db.set_well_attribute("P1", "NumericAttr", bad_number)
            with pytest.raises(ValidationError, match="finite"):
                db.add_well_attribute_column("Bad", AttributeType.NUMERIC, default=bad_number)


def test_attribute_column_ddl_matches_obsidian(tmp_path):
    # Obsidian emits `{type} NOT NULL DEFAULT {literal}` for its own attribute
    # columns. Matching that DDL keeps a column added from Python identical to
    # one added in the application, so a WellAttributes row Obsidian inserts
    # later carries the caller's default instead of NULL.
    db_path = tmp_path / "attrs_ddl.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        # The text default embeds a quote, which has to survive being inlined
        # into the DDL rather than bound as a parameter.
        db.add_well_attribute_column("Basin", AttributeType.TEXT, default="O'Brien")
        db.add_well_attribute_column("Opex", AttributeType.NUMERIC, default=2500.0)
        db.add_well_attribute_column("Expiry", AttributeType.DATE)

        # The DDL default backfills rows that already existed.
        assert db.well_attributes("P1") == {
            "Basin": "O'Brien",
            "Opex": 2500.0,
            "Expiry": date(2000, 1, 1),
        }

    conn = sqlite3.connect(db_path)
    try:
        schema = {
            str(row[1]): (str(row[2]), bool(row[3]), row[4])
            for row in conn.execute("PRAGMA table_info(WellAttributes)").fetchall()
        }
    finally:
        conn.close()

    assert schema["Basin"] == ("TEXT", True, "'O''Brien'")
    assert schema["Opex"] == ("REAL", True, "2500.0")
    assert schema["Expiry"] == ("DATE", True, "'2000-01-01'")


def test_attribute_column_default_lands_on_rows_written_outside_python(tmp_path):
    # Obsidian inserts WellAttributes rows naming only the columns it knows
    # about. Without a schema default those cells would be NULL; with one they
    # carry the default the Python caller chose.
    db_path = tmp_path / "attrs_default.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well_attribute_column("Basin", AttributeType.TEXT, default="Midland")
        db.add_well_attribute_column("Opex", AttributeType.NUMERIC, default=2500.0)

    exec_sql(db_path, [("INSERT INTO WellAttributes (prop_id) VALUES (?)", ("P2",))])

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            'SELECT "Basin", "Opex" FROM WellAttributes WHERE prop_id = ?', ("P2",)
        ).fetchone()
    finally:
        conn.close()

    assert row == ("Midland", 2500.0)
