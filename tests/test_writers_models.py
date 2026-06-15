from __future__ import annotations

from datetime import date

import pytest
from conftest import create_model_schema

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
