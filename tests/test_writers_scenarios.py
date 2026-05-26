from __future__ import annotations

from datetime import date

import pytest
from conftest import create_scenario_schema, seed_models

from upstream_edge.obsidian_db import (
    Database,
    DiffModelSegment,
    DiffType,
    ExpenseModelKind,
    ExpenseModelSegment,
    ModelNotFoundError,
    RsvCat,
    TaxModelKind,
    TaxModelSegment,
    ValidationError,
)


def test_create_scenario_copy_and_delete(tmp_path):
    db_path = tmp_path / "scenario_copy.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_well_models("P1", "MAIN", exp_model="OPEX")

        db.create_scenario("UPSIDE", copy_from="MAIN")

        copied = db.well_models("P1", "UPSIDE")[0]
        assert copied.exp_model == "OPEX"
        assert db.scenarios("UPSIDE")[0].price_model == "STRIP"

        with pytest.raises(ValidationError, match="confirm=True"):
            db.delete_scenario("UPSIDE", confirm=False)
        db.delete_scenario("UPSIDE", confirm=True)

        assert db.scenarios("UPSIDE") == []
        assert db.well_models("P1", "UPSIDE") == []


def test_delete_scenario_rejects_main(tmp_path):
    db_path = tmp_path / "scenario_main.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.raises(ValidationError, match="MAIN scenario cannot be deleted"):
            db.delete_scenario("MAIN", confirm=True)

        assert db.scenarios("MAIN") != []


def test_set_scenario_and_well_models_validation(tmp_path):
    db_path = tmp_path / "scenario_validation.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        db.set_scenario("MAIN", price_model="STRIP")
        assert db.scenarios("MAIN")[0].price_model == "STRIP"

        with pytest.raises(ModelNotFoundError):
            db.set_scenario("MAIN", price_model="MISSING")

        with pytest.raises(ValidationError, match="at least one"):
            db.set_well_models("P1", "MAIN")

        with pytest.raises(ModelNotFoundError) as exc_info:
            db.set_well_models("P1", "MAIN", exp_model="MISSING")
        assert exc_info.value.model_kind == "Expense"


def test_set_well_models_updates_named_models_and_warns_for_new_labels(tmp_path):
    db_path = tmp_path / "well_models.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        with pytest.warns(UserWarning, match="NEW_CAPEX"):
            db.set_well_models(
                "P1",
                "MAIN",
                exp_model="OPEX",
                diff_model="DIFF",
                tax_model="TAX",
                shrink_yield_model="SY",
                capex_model="NEW_CAPEX",
                interest_model="MAIN",
            )

        row = db.well_models("P1", "MAIN")[0]

    assert row.exp_model == "OPEX"
    assert row.diff_model == "DIFF"
    assert row.tax_model == "TAX"
    assert row.shrink_yield_model == "SY"
    assert row.capex_model == "NEW_CAPEX"
    assert row.interest_model == "MAIN"


def test_per_well_economic_model_writers_link_synthetic_models(tmp_path):
    db_path = tmp_path / "per_well_models.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)

        db.set_well_expense_model(
            "P1",
            "MAIN",
            [
                ExpenseModelSegment(
                    kind=ExpenseModelKind.SIMPLE,
                    fixed_monthly=10.0,
                    variable_oil=1.0,
                    variable_gas=2.0,
                    variable_water=3.0,
                )
            ],
        )
        db.set_well_tax_model(
            "P1",
            "MAIN",
            [
                TaxModelSegment(
                    kind=TaxModelKind.SIMPLE,
                    sev_tax_oil=0.1,
                    sev_tax_gas=0.1,
                    sev_tax_ngl=0.1,
                    ad_valorum_tax=0.1,
                )
            ],
        )
        db.set_well_diff_model(
            "P1",
            "MAIN",
            [
                DiffModelSegment(
                    start_date=date(2026, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-1.0,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.9,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.5,
                )
            ],
        )
        db.set_well_shrink_yield_model(
            "P1",
            "MAIN",
            gas_shrink_frac=0.1,
            ngl_yield_bbl_mmscf=40.0,
        )

        synthetic = "MAIN<>P1"
        row = db.well_models("P1", "MAIN")[0]
        assert row.exp_model == synthetic
        assert row.tax_model == synthetic
        assert row.diff_model == synthetic
        assert row.shrink_yield_model == synthetic
        assert db.expense_models(synthetic)[0].fixed_monthly == 10.0

        db.delete_well_expense_model("P1", "MAIN")
        assert db.expense_models(synthetic) == []
        assert db.well_models("P1", "MAIN")[0].exp_model == ""


def test_copy_well_remaps_synthetic_model_overrides(tmp_path):
    db_path = tmp_path / "copy_well_synthetic.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_well_expense_model(
            "P1",
            "MAIN",
            [
                ExpenseModelSegment(
                    kind=ExpenseModelKind.SIMPLE,
                    fixed_monthly=10.0,
                    variable_oil=1.0,
                    variable_gas=2.0,
                    variable_water=3.0,
                )
            ],
        )

        db.copy_well("P1", "P2")

        copied = db.well_models("P2", "MAIN")[0]
        assert copied.exp_model == "MAIN<>P2"
        assert db.expense_models("MAIN<>P2")[0].fixed_monthly == 10.0
        assert db.well_models("P1", "MAIN")[0].exp_model == "MAIN<>P1"


def test_delete_well_removes_synthetic_model_overrides(tmp_path):
    db_path = tmp_path / "delete_well_synthetic.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_well_expense_model(
            "P1",
            "MAIN",
            [
                ExpenseModelSegment(
                    kind=ExpenseModelKind.SIMPLE,
                    fixed_monthly=10.0,
                    variable_oil=1.0,
                    variable_gas=2.0,
                    variable_water=3.0,
                )
            ],
        )

        db.delete_well("P1", confirm=True)

        assert db.expense_models("MAIN<>P1") == []


def test_create_scenario_copy_remaps_synthetic_model_overrides(tmp_path):
    db_path = tmp_path / "copy_scenario_synthetic.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)

    with Database.open(db_path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_well_expense_model(
            "P1",
            "MAIN",
            [
                ExpenseModelSegment(
                    kind=ExpenseModelKind.SIMPLE,
                    fixed_monthly=10.0,
                    variable_oil=1.0,
                    variable_gas=2.0,
                    variable_water=3.0,
                )
            ],
        )

        db.create_scenario("UPSIDE", copy_from="MAIN")

        copied = db.well_models("P1", "UPSIDE")[0]
        assert copied.exp_model == "UPSIDE<>P1"
        assert db.expense_models("UPSIDE<>P1")[0].fixed_monthly == 10.0
