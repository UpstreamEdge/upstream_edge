"""Seven fields renamed in 1.3, and the names they replaced.

The names the fields had before keep working as keywords and attributes; a
script written against 1.2 runs unchanged unless it reads one of those
DataFrame columns by its old name.
"""

from __future__ import annotations

import dataclasses
import inspect
import sqlite3
from datetime import date

import pytest
from conftest import (
    change_log,
    create_bookkeeping_schema,
    create_scenario_schema,
    exec_sql,
    seed_models,
)

from upstream_edge.obsidian_db import (
    Database,
    MonthlyRow,
    RsvCat,
    TaxModel,
    TaxModelKind,
    TaxModelSegment,
    ValidationError,
    Well,
    WellModels,
)

# (the name, the name before 1.3 and the database's column, a value)
HEADER_NAMES = [
    ("spud_date", "spud", date(2025, 1, 2)),
    ("completion_date", "completion", date(2025, 3, 4)),
    ("first_production_date", "first_prod", date(2025, 5, 6)),
    ("bottom_hole_latitude", "bh_latitude", 31.5),
    ("bottom_hole_longitude", "bh_longitude", -102.5),
]


def make(tmp_path):
    path = tmp_path / "names.obsdb"
    create_scenario_schema(path)
    seed_models(path)
    create_bookkeeping_schema(path)
    return path


def field_names(row_type):
    return [field.name for field in dataclasses.fields(row_type)]


# ── well header ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "earlier", "value"), HEADER_NAMES)
def test_a_header_field_goes_in_and_comes_out_under_either_name(tmp_path, name, earlier, value):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("BY_NAME", rsv_cat=RsvCat.PDP, **{name: value})
        db.add_well("BY_EARLIER", rsv_cat=RsvCat.PDP, **{earlier: value})
        db.add_well("LATER", rsv_cat=RsvCat.PDP)
        db.set_well_header("LATER", **{earlier: value})
        for well in db.wells():
            assert getattr(well, name) == value, well.prop_id
            assert getattr(well, earlier) == value, well.prop_id
    # One column, whichever name it came in under; the ChangeLog names the column.
    conn = sqlite3.connect(path)
    assert conn.execute(f"select count(distinct {earlier}) from Main").fetchone() == (1,)
    conn.close()
    assert ("Main", "LATER", earlier, 3, None) in change_log(path)


@pytest.mark.parametrize(("name", "earlier", "value"), HEADER_NAMES)
def test_a_header_field_given_under_both_names_is_refused(tmp_path, name, earlier, value):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(ValidationError, match=f"'{name}' was given twice"):
            db.set_well_header("P1", **{name: value, earlier: value})
        with pytest.raises(ValidationError, match="given twice"):
            db.add_well("P2", rsv_cat=RsvCat.PDP, **{earlier: value, name: value})
        assert [well.prop_id for well in db.wells()] == ["P1"]


def test_a_header_field_clears_and_an_unknown_one_is_named_with_the_valid_ones(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP, first_production_date=date(2025, 5, 6))
        db.set_well_header("P1", first_prod=None)
        well = db.well("P1")
        assert well is not None and well.first_production_date is None
        with pytest.raises(ValidationError, match="unknown field 'first_production'") as error:
            db.set_well_header("P1", first_production=date(2025, 5, 6))
        assert "first_production_date" in str(error.value)
        assert "first_prod," not in str(error.value), "only the names of record are offered"


def test_the_well_fields_and_dataframe_columns_carry_the_names(tmp_path):
    names = field_names(Well)
    assert names[13:16] == ["spud_date", "completion_date", "first_production_date"]
    assert names[-2:] == ["bottom_hole_latitude", "bottom_hole_longitude"]
    assert not {"spud", "completion", "first_prod", "bh_latitude", "bh_longitude"} & set(names)
    pytest.importorskip("pandas")
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP, first_production_date=date(2025, 5, 6))
        assert list(db.wells_df().columns) == names


def test_a_well_is_built_and_read_under_the_earlier_names_too():
    values = dict.fromkeys(field_names(Well))
    values.update(prop_id="P1", rsv_cat=RsvCat.PDP)
    for name, earlier, value in HEADER_NAMES:
        del values[name]
        values[earlier] = value
    well = Well(**values)
    for name, earlier, value in HEADER_NAMES:
        assert getattr(well, name) == getattr(well, earlier) == value
    assert "first_production_date=datetime.date(2025, 5, 6)" in repr(well)
    # `dataclasses.replace` under either name, as a 1.2 script calls it.
    assert dataclasses.replace(well, first_production_date=None).first_prod is None
    moved = dataclasses.replace(well, first_prod=date(2030, 1, 1), spud=None)
    assert (moved.first_production_date, moved.spud_date) == (date(2030, 1, 1), None)
    assert moved.completion_date == date(2025, 3, 4), "the other fields are kept"
    with pytest.raises(dataclasses.FrozenInstanceError):
        well.first_production_date = None  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):  # read-only, like every field
        well.first_prod = None  # type: ignore[misc]


def test_the_earlier_names_are_not_in_a_signature():
    for row_type in (Well, WellModels, TaxModel, TaxModelSegment, MonthlyRow):
        parameters = set(inspect.signature(row_type).parameters)
        assert parameters == set(field_names(row_type)), row_type.__name__


# ── well models ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("keyword", ["expense_model", "exp_model"])
def test_the_expense_model_is_assigned_under_either_name(tmp_path, keyword):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_well_models("P1", "MAIN", **{keyword: "OPEX"})
        (assigned,) = db.well_models("P1")
        assert assigned.expense_model == "OPEX" and assigned.exp_model == "OPEX"


def test_the_expense_model_given_under_both_names_is_refused(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(ValidationError, match="given twice"):
            db.set_well_models("P1", "MAIN", exp_model="OPEX", expense_model="OPEX")
        assert db.well_models("P1")[0].expense_model == "MAIN"


def test_an_expense_model_under_the_earlier_name_must_exist_too(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        with pytest.raises(Exception, match="Expense model 'NOPE' does not exist"):
            db.set_well_models("P1", "MAIN", exp_model="NOPE")


def test_well_models_fields_and_columns(tmp_path):
    names = field_names(WellModels)
    assert names[2] == "expense_model" and "exp_model" not in names
    assert WellModels("P1", "MAIN", "A", "B", "C", "D", "E", "F").exp_model == "A"
    assert WellModels("P1", "MAIN", exp_model="A", capex_model="B", diff_model="C",
                      tax_model="D", shrink_yield_model="E", interest_model="F").expense_model == "A"  # fmt: skip
    pytest.importorskip("pandas")
    path = make(tmp_path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        assert list(db.well_models_df().columns) == names


# ── tax ──────────────────────────────────────────────────────────────────────


def test_a_tax_segment_takes_the_rate_under_either_spelling():
    by_name = TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, ad_valorem_tax=0.02)
    by_earlier = TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, ad_valorum_tax=0.02)
    by_place = TaxModelSegment(TaxModelKind.AGE_BASED, 0.046, 0.075, 0.075, 0.02, 12)
    assert by_name == by_earlier
    assert by_earlier.ad_valorem_tax == by_earlier.ad_valorum_tax == 0.02
    assert (by_place.ad_valorem_tax, by_place.age_months, by_place.effective_date) == (
        0.02,
        12,
        None,
    )
    assert hash(by_name) == hash(by_earlier)
    assert "ad_valorem_tax=0.02" in repr(by_earlier) and "valorum" not in repr(by_earlier)
    assert dataclasses.replace(by_earlier, ad_valorem_tax=0.05).ad_valorum_tax == 0.05
    assert "ad_valorum_tax" not in field_names(TaxModelSegment)


def test_a_tax_segment_needs_the_rate():
    with pytest.raises(TypeError, match="ad_valorem_tax"):
        TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="multiple values"):
        TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, 0.02, ad_valorum_tax=0.02)
    with pytest.raises(dataclasses.FrozenInstanceError):
        TaxModelSegment(  # type: ignore[misc]
            TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, 0.02
        ).ad_valorem_tax = 0.5
    segment = TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, ad_valorem_tax=0.02)
    assert dataclasses.replace(segment, ad_valorum_tax=0.05).ad_valorem_tax == 0.05
    assert dataclasses.replace(segment, ad_valorem_tax=0.03).ad_valorum_tax == 0.03


def test_a_tax_model_written_under_the_earlier_spelling_reads_under_both(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.set_tax_model(
            "STATE",
            [TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, ad_valorum_tax=0.025)],
        )
        (model,) = db.tax_models("STATE")
        assert isinstance(model, TaxModel)
        assert model.ad_valorem_tax == model.ad_valorum_tax == 0.025
        with pytest.raises(ValidationError, match="ad_valorem_tax"):
            db.set_tax_model(
                "STATE",
                [TaxModelSegment(TaxModelKind.SIMPLE, 0.046, 0.075, 0.075, ad_valorem_tax=25.0)],
            )
        pytest.importorskip("pandas")
        assert list(db.tax_models_df().columns)[5] == "ad_valorem_tax"


# ── monthly production ───────────────────────────────────────────────────────


def test_a_monthly_row_keeps_its_names_and_takes_the_other_spelling_of_gas():
    month = date(2026, 1, 1)
    by_name = MonthlyRow(prop_id="P1", month=month, oil_bbl=900.0, gas_mscf=1500.0, water_bbl=40.0)
    by_other = MonthlyRow(prop_id="P1", month=month, oil_bbl=900.0, gas_mcf=1500.0, water_bbl=40.0)
    by_place = MonthlyRow("P1", month, 900.0, 1500.0, 40.0)
    assert by_name == by_other == by_place
    assert by_other.gas_mscf == by_other.gas_mcf == 1500.0
    assert MonthlyRow("P1", month, None, gas_mcf=None, water_bbl=None).gas_mscf is None
    assert field_names(MonthlyRow) == ["prop_id", "month", "oil_bbl", "gas_mscf", "water_bbl"]
    assert dataclasses.replace(by_other, oil_bbl=1.0).gas_mcf == 1500.0


def test_a_monthly_row_needs_each_value():
    month = date(2026, 1, 1)
    with pytest.raises(TypeError, match="multiple values"):
        MonthlyRow("P1", month, 1.0, 2.0, 3.0, gas_mcf=2.0)
    with pytest.raises(TypeError, match="gas_mscf"):
        MonthlyRow("P1", month, 1.0, water_bbl=3.0)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="water_bbl"):
        MonthlyRow("P1", month, 1.0, gas_mcf=2.0)  # type: ignore[call-arg]
    row = MonthlyRow("P1", month, 1.0, 2.0, 3.0)
    assert dataclasses.replace(row, gas_mcf=9.0).gas_mscf == 9.0


def test_monthly_production_written_under_the_other_spelling_round_trips(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["create unique index idx_Monthly on Monthly (prop_id, month)"])
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_monthly_prod(
            [MonthlyRow("P1", date(2026, 1, 1), oil_bbl=900.0, gas_mcf=1500.0, water_bbl=40.0)]
        )
        (row,) = db.production_monthly("P1")
        assert row.gas_mscf == row.gas_mcf == 1500.0
        pytest.importorskip("pandas")
        assert list(db.production_monthly_df().columns)[3] == "gas_mscf"
