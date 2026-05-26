from __future__ import annotations

from datetime import date

import pytest
from conftest import exec_sql

from upstream_edge.obsidian_db import AttributeType, Database, DataIntegrityError, WellNotFoundError


def test_attribute_columns_and_values(tmp_path):
    db_path = tmp_path / "attrs.obsdb"
    exec_sql(
        db_path,
        [
            (
                "create table WellAttributes (prop_id text primary key, Basin text, "
                '"Working Interest" real, LeaseExpiry date)'
            ),
            (
                "insert into WellAttributes values (?, ?, ?, ?)",
                ("P1", "Midland", 0.875, "2030-12-31"),
            ),
        ],
    )

    with Database.open(db_path) as db:
        columns = db.list_attribute_columns()
        attrs = db.well_attributes("P1")
        all_attrs = db.all_well_attributes()

    assert [(column.name, column.attr_type) for column in columns] == [
        ("Basin", AttributeType.TEXT),
        ("Working Interest", AttributeType.NUMERIC),
        ("LeaseExpiry", AttributeType.DATE),
    ]
    assert attrs == {
        "Basin": "Midland",
        "Working Interest": 0.875,
        "LeaseExpiry": date(2030, 12, 31),
    }
    assert all_attrs["P1"]["Basin"] == "Midland"


def test_attribute_reader_rejects_null_and_missing_row(tmp_path):
    db_path = tmp_path / "bad_attrs.obsdb"
    exec_sql(
        db_path,
        [
            "create table WellAttributes (prop_id text primary key, Basin text)",
            ("insert into WellAttributes values (?, ?)", ("P1", None)),
        ],
    )

    with Database.open(db_path) as db:
        with pytest.raises(DataIntegrityError, match="NULL"):
            db.well_attributes("P1")

        with pytest.raises(WellNotFoundError) as exc_info:
            db.well_attributes("P2")

    assert exc_info.value.prop_id == "P2"
