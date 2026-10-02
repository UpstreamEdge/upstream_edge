"""Databases whose schema is older than the library's: files last saved by an
older Obsidian, or created by Obsidian and written from Python before Obsidian
loaded them. Obsidian adds missing tables on load; until then the library has to
cope on its own."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from conftest import (
    create_attribute_schema,
    create_core_schema,
    create_obsidian_2026_02_blank_schema,
    exec_sql,
)

from upstream_edge.obsidian_db import (
    AttributeType,
    Database,
    DataIntegrityError,
    MissingTableError,
    MonthlyRow,
    RsvCat,
    ValidationError,
    WellAttributeUpdate,
)


def _rows(path, sql: str) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    try:
        return [tuple(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _well_attribute_prop_ids(path) -> list[str]:
    return [
        str(row[0]) for row in _rows(path, "SELECT prop_id FROM WellAttributes ORDER BY prop_id")
    ]


def _table_names(path) -> set[str]:
    return {
        str(row[0]) for row in _rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _prop_id_tables(path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]
        return [
            table
            for table in tables
            if any(
                str(column[1]) == "prop_id"
                for column in conn.execute(f'PRAGMA table_info("{table}")')
            )
        ]
    finally:
        conn.close()


def _count(path, table: str, prop_id: str) -> int:
    conn = sqlite3.connect(path)
    try:
        sql = f'SELECT COUNT(*) FROM "{table}" WHERE prop_id = ?'
        return int(conn.execute(sql, (prop_id,)).fetchone()[0])
    finally:
        conn.close()


def _create_main_and_monthly_only(path) -> None:
    # Older than WellModels and every other per-well table but Monthly.
    exec_sql(
        path,
        [
            "CREATE TABLE Main (prop_id TEXT PRIMARY KEY, rsv_cat TEXT)",
            "CREATE TABLE Monthly (prop_id TEXT, month TEXT, oil_monthly_bbl REAL)",
            ("INSERT INTO Main VALUES (?, ?)", ("P1", "PDP")),
            ("INSERT INTO Main VALUES (?, ?)", ("P2", "PDP")),
            ("INSERT INTO Monthly VALUES (?, ?, ?)", ("P1", "2026-01-01", 100.0)),
            ("INSERT INTO Monthly VALUES (?, ?, ?)", ("P2", "2026-01-01", 200.0)),
        ],
    )


def test_well_lifecycle_on_obsidian_2026_02_blank_database(tmp_path):
    # The customer report: the first add_well on a new Blank Database failed
    # with "UNIQUE constraint failed: WellAttributes.prop_id".
    db_path = tmp_path / "blank.obsdb"
    create_obsidian_2026_02_blank_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP, lease="MITCHELL")
        db.add_well("P2", rsv_cat=RsvCat.PUD, lease="MITCHELL")
        db.add_well_attribute_column("Basin", AttributeType.TEXT, default="Midland")
        db.set_well_attribute("P1", "Basin", "Delaware")
        db.copy_well("P1", "P3")
        db.delete_well("P2", confirm=True)

        prop_ids = [well.prop_id for well in db.wells()]
        attributes = db.all_well_attributes()

    assert prop_ids == ["P1", "P3"]
    assert attributes == {"P1": {"Basin": "Delaware"}, "P3": {"Basin": "Delaware"}}
    assert _well_attribute_prop_ids(db_path) == ["P1", "P3"]

    # Created the way Obsidian creates it, unique index included.
    conn = sqlite3.connect(db_path)
    try:
        indexes = {
            str(row[1]): bool(row[2])
            for row in conn.execute("PRAGMA index_list(WellAttributes)").fetchall()
        }
    finally:
        conn.close()
    assert indexes == {"idx_WellAttributes": True}


def test_delete_and_copy_well_cover_every_prop_id_table_in_the_blank_schema(tmp_path):
    db_path = tmp_path / "every_table.obsdb"
    create_obsidian_2026_02_blank_schema(db_path)
    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well("P2", rsv_cat=RsvCat.PDP)
    tables = _prop_id_tables(db_path)
    # Give both wells a row in every table add_well left empty (Casing, Perfs,
    # PredictorAssignment, production and the rest).
    exec_sql(
        db_path,
        [
            (f'INSERT INTO "{table}" (prop_id) VALUES (?)', (prop_id,))
            for table in tables
            for prop_id in ("P1", "P2")
            if _count(db_path, table, prop_id) == 0
        ],
    )

    with Database.open(db_path) as db:
        db.copy_well("P1", "P3")
        db.delete_well("P2", confirm=True)

    assert "Casing" in tables
    assert "PredictorAssignment" in tables
    for table in tables:
        assert _count(db_path, table, "P1") > 0, table
        assert _count(db_path, table, "P2") == 0, table
        assert _count(db_path, table, "P3") == _count(db_path, table, "P1"), table


def test_add_well_creates_well_attributes_with_one_row_per_well(tmp_path):
    db_path = tmp_path / "existing_wells.obsdb"
    create_obsidian_2026_02_blank_schema(db_path)
    exec_sql(db_path, [("INSERT INTO Main (prop_id, rsv_cat) VALUES (?, ?)", ("P1", "PDP"))])

    with Database.open(db_path) as db:
        db.add_well("P2", rsv_cat=RsvCat.PDP)

        assert db.all_well_attributes() == {"P1": {}, "P2": {}}

    # The well already in Main is backfilled; the new well gets one row, not two.
    assert _well_attribute_prop_ids(db_path) == ["P1", "P2"]


def test_add_well_replaces_a_row_left_by_a_deleted_well(tmp_path):
    db_path = tmp_path / "leftover_row.obsdb"
    create_attribute_schema(db_path)
    with Database.open(db_path) as db:
        db.add_well_attribute_column("Opex", AttributeType.NUMERIC)
    # A row whose well was removed from Main directly, outside the library.
    exec_sql(
        db_path, [('INSERT INTO WellAttributes (prop_id, "Opex") VALUES (?, ?)', ("P9", 99.0))]
    )

    with Database.open(db_path) as db:
        db.add_well("P9", rsv_cat=RsvCat.PUD)

        assert db.well_attributes("P9") == {"Opex": 0.0}

    assert _well_attribute_prop_ids(db_path) == ["P9"]


def test_add_well_on_a_database_with_no_well_attributes_rolls_back_on_failure(tmp_path):
    # The table add_well creates is part of its transaction: a failed add
    # leaves the file as it was.
    db_path = tmp_path / "rollback.obsdb"
    create_obsidian_2026_02_blank_schema(db_path)
    exec_sql(db_path, ["DROP TABLE Abandonment"])

    with Database.open(db_path) as db, pytest.raises(MissingTableError):
        db.add_well("P1", rsv_cat=RsvCat.PDP)

    assert "WellAttributes" not in _table_names(db_path)
    assert _rows(db_path, "SELECT COUNT(*) FROM Main") == [(0,)]


def test_delete_well_skips_tables_the_file_does_not_have(tmp_path):
    # No WellModels either, so there are no individual models to look up.
    db_path = tmp_path / "minimal_delete.obsdb"
    _create_main_and_monthly_only(db_path)

    with Database.open(db_path) as db:
        db.delete_well("P1", confirm=True)

    assert _rows(db_path, "SELECT prop_id FROM Main") == [("P2",)]
    assert _rows(db_path, "SELECT prop_id FROM Monthly") == [("P2",)]


def test_copy_well_skips_tables_the_file_does_not_have(tmp_path):
    db_path = tmp_path / "minimal_copy.obsdb"
    _create_main_and_monthly_only(db_path)

    with Database.open(db_path) as db:
        db.copy_well("P1", "P3")

    assert _rows(db_path, "SELECT prop_id, rsv_cat FROM Main WHERE prop_id = 'P3'") == [
        ("P3", "PDP")
    ]
    assert _rows(db_path, "SELECT * FROM Monthly WHERE prop_id = 'P3'") == [
        ("P3", "2026-01-01", 100.0)
    ]
    assert _table_names(db_path) == {"Main", "Monthly"}


def test_set_well_attribute_adds_the_row_a_well_is_missing(tmp_path):
    db_path = tmp_path / "missing_row.obsdb"
    create_attribute_schema(db_path)
    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well_attribute_column("Opex", AttributeType.NUMERIC)
        db.add_well_attribute_column("Expiry", AttributeType.DATE)
        db.add_well_attribute_column("Basin", AttributeType.TEXT)
    # Wells added to Main by another tool, with no WellAttributes row.
    exec_sql(
        db_path,
        [
            ("INSERT INTO Main (prop_id, rsv_cat) VALUES (?, ?)", ("P2", "PDP")),
            ("INSERT INTO Main (prop_id, rsv_cat) VALUES (?, ?)", ("P3", "PDP")),
        ],
    )

    with Database.open(db_path) as db:
        db.set_well_attribute("P2", "Opex", 12.5)
        # Two updates for one well: the first adds the row, the second finds it.
        db.set_well_attributes_bulk(
            [
                WellAttributeUpdate("P3", "Opex", 7.0),
                WellAttributeUpdate("P3", "Expiry", date(2031, 6, 1)),
            ]
        )

        p2 = db.well_attributes("P2")
        p3 = db.well_attributes("P3")

    assert p2 == {"Opex": 12.5, "Expiry": date(2000, 1, 1), "Basin": ""}
    assert p3 == {"Opex": 7.0, "Expiry": date(2031, 6, 1), "Basin": ""}
    assert _well_attribute_prop_ids(db_path) == ["P1", "P2", "P3"]


def test_new_attribute_rows_take_each_column_s_declared_default(tmp_path):
    db_path = tmp_path / "column_defaults.obsdb"
    create_attribute_schema(db_path)
    with Database.open(db_path) as db:
        db.add_well_attribute_column("Basin", AttributeType.TEXT, default="Midland")
        db.add_well_attribute_column("Opex", AttributeType.NUMERIC, default=1500.0)
        db.add_well_attribute_column("Expiry", AttributeType.DATE)
    exec_sql(
        db_path,
        [
            # A column with no declared default, as another tool might add one.
            'ALTER TABLE WellAttributes ADD COLUMN "Zone" TEXT',
            ("INSERT INTO Main (prop_id, rsv_cat) VALUES (?, ?)", ("P2", "PDP")),
        ],
    )

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_well_attribute("P2", "Opex", 12.5)

        p1 = db.well_attributes("P1")
        p2 = db.well_attributes("P2")

    assert p1 == {"Basin": "Midland", "Opex": 1500.0, "Expiry": date(2000, 1, 1), "Zone": ""}
    assert p2 == {"Basin": "Midland", "Opex": 12.5, "Expiry": date(2000, 1, 1), "Zone": ""}


def test_set_well_attribute_without_a_well_attributes_table_writes_nothing(tmp_path):
    # No table means no attribute columns, which opening the file in Obsidian
    # would not change, so this is an unknown column rather than a missing table.
    db_path = tmp_path / "no_attributes.obsdb"
    create_obsidian_2026_02_blank_schema(db_path)
    exec_sql(db_path, [("INSERT INTO Main (prop_id, rsv_cat) VALUES (?, ?)", ("P1", "PDP"))])

    with Database.open(db_path) as db:
        with pytest.raises(ValidationError) as caught:
            db.set_well_attribute("P1", "Basin", "Midland")
        with pytest.raises(ValidationError, match="has no WellAttributes table yet"):
            db.set_well_attributes_bulk([WellAttributeUpdate("P1", "Basin", "Midland")])

    assert str(caught.value) == (
        "set_well_attribute: unknown WellAttributes column 'Basin'. The database has no "
        "WellAttributes table yet, so it has no attribute columns; "
        "add_well_attribute_column creates the table and adds the column"
    )
    assert "WellAttributes" not in _table_names(db_path)

    # Following the message works.
    with Database.open(db_path) as db:
        db.add_well_attribute_column("Basin", AttributeType.TEXT)
        db.set_well_attribute("P1", "Basin", "Midland")

        assert db.well_attributes("P1") == {"Basin": "Midland"}


def test_set_well_attribute_with_no_attribute_columns_says_to_add_one(tmp_path):
    db_path = tmp_path / "no_columns.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(ValidationError) as caught:
            db.set_well_attribute("P1", "Basin", "Midland")

    assert str(caught.value) == (
        "set_well_attribute: unknown WellAttributes column 'Basin'. The database has no "
        "attribute columns yet; add one with add_well_attribute_column"
    )


def test_write_to_a_missing_table_raises_missing_table_error(tmp_path):
    db_path = tmp_path / "no_monthly.obsdb"
    create_attribute_schema(db_path)
    row = MonthlyRow("P1", date(2026, 1, 1), oil_bbl=100.0, gas_mscf=200.0, water_bbl=10.0)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(MissingTableError) as caught:
            db.set_monthly_prod([row])

        # The well from the earlier, separate write is kept.
        assert [well.prop_id for well in db.wells()] == ["P1"]

    assert caught.value.table == "Monthly"
    assert isinstance(caught.value, DataIntegrityError)
    assert "'Monthly' is missing" in str(caught.value)
    assert "Open it once in Obsidian" in str(caught.value)
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)


def test_missing_table_inside_a_transaction_rolls_back_the_transaction(tmp_path):
    db_path = tmp_path / "no_monthly_txn.obsdb"
    create_attribute_schema(db_path)
    row = MonthlyRow("P1", date(2026, 1, 1), oil_bbl=100.0, gas_mscf=200.0, water_bbl=10.0)

    with Database.open(db_path) as db:
        with pytest.raises(MissingTableError, match="'Monthly'"), db.transaction():
            db.add_well("P1", rsv_cat=RsvCat.PDP)
            db.set_monthly_prod([row])

        assert db.wells() == []


def test_read_of_a_missing_table_raises_missing_table_error(tmp_path):
    db_path = tmp_path / "no_monthly_read.obsdb"
    create_attribute_schema(db_path)

    with Database.open(db_path) as db, pytest.raises(MissingTableError) as caught:
        db.production_monthly()

    assert caught.value.table == "Monthly"
    assert isinstance(caught.value, DataIntegrityError)


def test_missing_table_reached_through_a_trigger_names_the_table(tmp_path):
    # SQLite reports a trigger's missing table as "main.<name>".
    db_path = tmp_path / "trigger.obsdb"
    create_core_schema(db_path)
    exec_sql(
        db_path,
        [
            "CREATE TABLE MonthlyAudit (prop_id TEXT)",
            "CREATE TRIGGER audit_monthly AFTER INSERT ON Monthly "
            "BEGIN INSERT INTO MonthlyAudit (prop_id) VALUES (NEW.prop_id); END",
            "DROP TABLE MonthlyAudit",
        ],
    )
    row = MonthlyRow("P1", date(2026, 1, 1), oil_bbl=100.0, gas_mscf=200.0, water_bbl=10.0)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(MissingTableError) as caught:
            db.set_monthly_prod([row])

    assert caught.value.table == "MonthlyAudit"
    assert _rows(db_path, "SELECT COUNT(*) FROM Monthly") == [(0,)]


def test_other_sqlite_errors_are_not_reported_as_missing_tables(tmp_path):
    # A table that exists but lacks a column is a different problem, and keeps
    # the error it raised before.
    db_path = tmp_path / "short_monthly.obsdb"
    create_attribute_schema(db_path)
    exec_sql(db_path, ["CREATE TABLE Monthly (prop_id TEXT, month TEXT)"])
    row = MonthlyRow("P1", date(2026, 1, 1), oil_bbl=100.0, gas_mscf=200.0, water_bbl=10.0)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(sqlite3.OperationalError, match="no column named"):
            db.set_monthly_prod([row])
        with pytest.raises(DataIntegrityError, match="no such column") as read_error:
            db.production_monthly()

    assert not isinstance(read_error.value, MissingTableError)
