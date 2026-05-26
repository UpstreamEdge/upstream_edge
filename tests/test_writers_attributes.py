from __future__ import annotations

from datetime import date

import pytest
from conftest import create_attribute_schema

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
