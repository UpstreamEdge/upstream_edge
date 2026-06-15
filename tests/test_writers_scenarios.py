from __future__ import annotations

import pytest
from conftest import create_scenario_schema, exec_sql, seed_models

from upstream_edge.obsidian_db import (
    Database,
    ModelNotFoundError,
    RsvCat,
    ValidationError,
)


def _seed_synthetic_expense_override(tmp_path_db, prop_id, scenario="MAIN"):
    """Seed a legacy per-well "<>" expense override directly.

    Obsidian historically wrote per-well overrides as shared-table rows named
    "{scenario}<>{prop_id}". The library no longer creates them, but still has
    to read around them, remap them on copy, and delete them — so tests insert
    them by hand the way a real database would already carry them.
    """
    synthetic = f"{scenario}<>{prop_id}"
    exec_sql(
        tmp_path_db,
        [
            ("insert into Main (prop_id, rsv_cat) values (?, 'PDP')", (prop_id,)),
            (
                "insert into WellModels (prop_id, scenario, exp_model_name, capex_model_name, "
                "diff_model_name, tax_model_name, shrink_yield_model_name, interest_model_name) "
                "values (?, ?, ?, '', '', '', '', '')",
                (prop_id, scenario, synthetic),
            ),
            (
                "insert into ExpenseModel (exp_model_name, model_type, fixed_monthly, "
                "variable_oil, variable_gas, variable_water) "
                "values (?, 'SIMPLE', 10.0, 1.0, 2.0, 3.0)",
                (synthetic,),
            ),
        ],
    )
    return synthetic


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


def test_legacy_synthetic_overrides_are_readable_and_filtered(tmp_path):
    db_path = tmp_path / "per_well_models.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)
    synthetic = _seed_synthetic_expense_override(db_path, "P1")

    with Database.open(db_path) as db:
        # Fetching a legacy override by its exact name still works.
        assert db.expense_models(synthetic)[0].fixed_monthly == 10.0
        assert db.well_models("P1", "MAIN")[0].exp_model == synthetic

        # Synthetic per-well override names stay out of the unfiltered listing.
        assert all("<>" not in model.name for model in db.expense_models())


def test_copy_well_remaps_synthetic_model_overrides(tmp_path):
    db_path = tmp_path / "copy_well_synthetic.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)
    _seed_synthetic_expense_override(db_path, "P1")

    with Database.open(db_path) as db:
        db.copy_well("P1", "P2")

        copied = db.well_models("P2", "MAIN")[0]
        assert copied.exp_model == "MAIN<>P2"
        assert db.expense_models("MAIN<>P2")[0].fixed_monthly == 10.0
        assert db.well_models("P1", "MAIN")[0].exp_model == "MAIN<>P1"


def test_delete_well_removes_synthetic_model_overrides(tmp_path):
    db_path = tmp_path / "delete_well_synthetic.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)
    _seed_synthetic_expense_override(db_path, "P1")

    with Database.open(db_path) as db:
        db.delete_well("P1", confirm=True)

        assert db.expense_models("MAIN<>P1") == []


def test_create_scenario_copy_remaps_synthetic_model_overrides(tmp_path):
    db_path = tmp_path / "copy_scenario_synthetic.obsdb"
    create_scenario_schema(db_path)
    seed_models(db_path)
    _seed_synthetic_expense_override(db_path, "P1")

    with Database.open(db_path) as db:
        db.create_scenario("UPSIDE", copy_from="MAIN")

        copied = db.well_models("P1", "UPSIDE")[0]
        assert copied.exp_model == "UPSIDE<>P1"
        assert db.expense_models("UPSIDE<>P1")[0].fixed_monthly == 10.0
