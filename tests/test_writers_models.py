from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from conftest import create_model_schema, exec_sql

from upstream_edge.obsidian_db import (
    Database,
    DiffModelSegment,
    DiffType,
    ExpenseModelKind,
    ExpenseModelSegment,
    PriceModelSegment,
    TaxModelKind,
    TaxModelSegment,
    ValidationError,
)


def test_price_model_round_trip(tmp_path):
    db_path = tmp_path / "price_model.obsdb"
    create_model_schema(db_path)

    with Database.open(db_path) as db:
        db.set_price_model(
            "STRIP",
            [
                PriceModelSegment(date(2027, 1, 1), oil=68.0, gas=3.0, ngl=20.0),
                PriceModelSegment(date(2026, 1, 1), oil=70.0, gas=3.2, ngl=21.0),
            ],
        )

        prices = db.price_models("STRIP")

    assert [price.start_date for price in prices] == [date(2026, 1, 1), date(2027, 1, 1)]


def test_shared_model_writers_round_trip(tmp_path):
    db_path = tmp_path / "shared_models.obsdb"
    create_model_schema(db_path)

    with Database.open(db_path) as db:
        db.set_expense_model(
            "OPEX",
            [
                ExpenseModelSegment(
                    kind=ExpenseModelKind.SIMPLE,
                    fixed_monthly=2500.0,
                    variable_oil=4.0,
                    variable_gas=0.3,
                    variable_water=1.0,
                )
            ],
        )
        db.set_tax_model(
            "TAX",
            [
                TaxModelSegment(
                    kind=TaxModelKind.DATE_BASED,
                    effective_date=date(2026, 1, 1),
                    sev_tax_oil=0.04,
                    sev_tax_gas=0.05,
                    sev_tax_ngl=0.03,
                    ad_valorum_tax=0.02,
                )
            ],
        )
        db.set_diff_model(
            "DIFF",
            [
                DiffModelSegment(
                    start_date=date(2026, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-2.0,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.9,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.5,
                )
            ],
        )
        db.set_shrink_yield_model("SY", gas_shrink_frac=0.12, ngl_yield_bbl_mmscf=45.0)

        assert db.expense_models("OPEX")[0].fixed_monthly == 2500.0
        assert db.tax_models("TAX")[0].effective_date == date(2026, 1, 1)
        assert db.diff_models("DIFF")[0].oil_method is DiffType.DOLLAR
        assert db.shrink_yield_models("SY")[0].gas_shrink_frac == 0.12

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_expense_model("OPEX")
        db.delete_expense_model("OPEX", confirm=True)
        assert db.expense_models("OPEX") == []


def test_shared_model_validation(tmp_path):
    db_path = tmp_path / "model_validation.obsdb"
    create_model_schema(db_path)

    with Database.open(db_path) as db:
        with pytest.raises(ValidationError, match="segments cannot be empty"):
            db.set_price_model("STRIP", [])

        with pytest.raises(ValidationError, match="AGE_BASED segments require age_months"):
            db.set_expense_model(
                "OPEX",
                [
                    ExpenseModelSegment(
                        kind=ExpenseModelKind.AGE_BASED,
                        fixed_monthly=1.0,
                        variable_oil=1.0,
                        variable_gas=1.0,
                        variable_water=1.0,
                    )
                ],
            )


def test_condensate_columns_survive_a_rewrite(tmp_path):
    # Obsidian's DiffModel and ShrinkYieldModel carry condensate values this
    # library does not expose. Both writers delete and reinsert, so hardcoding
    # the condensate columns wiped whatever Obsidian had stored. They must be
    # carried across instead, per start_date for the diff model.
    db_path = tmp_path / "condensate.obsdb"
    create_model_schema(db_path)

    with Database.open(db_path) as db:
        db.set_diff_model(
            "DIFF",
            [
                DiffModelSegment(
                    start_date=date(2026, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-2.0,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.9,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.5,
                ),
                DiffModelSegment(
                    start_date=date(2027, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-2.5,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.85,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.45,
                ),
            ],
        )
        db.set_shrink_yield_model("SY", gas_shrink_frac=0.12, ngl_yield_bbl_mmscf=45.0)

    # Stand in for Obsidian setting the condensate side. The 2027 segment is
    # left on its defaults so both the carried and the untouched case are
    # covered.
    exec_sql(
        db_path,
        [
            (
                "UPDATE DiffModel SET condensate_diff_method = ?, condensate_diff = ? "
                "WHERE diff_model_name = ? AND start_date = ?",
                ("DOLLAR", -3.25, "DIFF", "2026-01-01"),
            ),
            (
                "UPDATE ShrinkYieldModel SET condensate_yield_bbl_mmscf = ? "
                "WHERE shrink_yield_model_name = ?",
                (12.5, "SY"),
            ),
        ],
    )

    with Database.open(db_path) as db:
        # Rewrite both models, changing only what the library exposes. The new
        # 2028 segment has no prior row and must land on Obsidian's defaults.
        db.set_diff_model(
            "DIFF",
            [
                DiffModelSegment(
                    start_date=date(2026, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-1.0,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.95,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.55,
                ),
                DiffModelSegment(
                    start_date=date(2028, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-4.0,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.8,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.4,
                ),
            ],
        )
        db.set_shrink_yield_model("SY", gas_shrink_frac=0.2, ngl_yield_bbl_mmscf=50.0)

        # The exposed edit landed.
        assert db.diff_models("DIFF")[0].oil_diff == -1.0
        assert db.shrink_yield_models("SY")[0].gas_shrink_frac == 0.2

    conn = sqlite3.connect(db_path)
    try:
        diffs = {
            str(row[0]): (str(row[1]), row[2])
            for row in conn.execute(
                "SELECT start_date, condensate_diff_method, condensate_diff FROM DiffModel "
                "WHERE diff_model_name = ?",
                ("DIFF",),
            ).fetchall()
        }
        shrink = conn.execute(
            "SELECT condensate_yield_bbl_mmscf FROM ShrinkYieldModel "
            "WHERE shrink_yield_model_name = ?",
            ("SY",),
        ).fetchone()
    finally:
        conn.close()

    assert diffs["2026-01-01"] == ("DOLLAR", -3.25)  # carried, not wiped
    assert diffs["2028-01-01"] == ("FRACTION", 0.0)  # new row, Obsidian's default
    assert "2027-01-01" not in diffs  # dropped segment really is gone
    assert shrink[0] == 12.5
