"""Per-well field references of the shared econ models: read, and kept true
by every write that touches a model, a well or an attribute column."""

from __future__ import annotations

import sqlite3
import warnings
from datetime import date

import pytest
from conftest import (
    create_field_refs_schema,
    create_scenario_schema,
    exec_sql,
    field_refs,
    seed_models,
)

from upstream_edge.obsidian_db import (
    AttributeType,
    Database,
    DiffModelSegment,
    DiffType,
    ExpenseModelKind,
    ExpenseModelSegment,
    ModelFieldRef,
    ModelFieldRefWarning,
    ModelKind,
    RsvCat,
    TaxModelKind,
    TaxModelSegment,
    ValidationError,
)


def make(tmp_path, *, refs_table=True):
    """Scenario MAIN, the seeded models (OPEX, TAX, DIFF, SY), and wells P1, P2."""
    path = tmp_path / "refs.obsdb"
    create_scenario_schema(path)
    seed_models(path)
    if refs_table:
        create_field_refs_schema(path)
    with Database.open(path, change_log=False) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well("P2", rsv_cat=RsvCat.PDP)
        db.add_well_attribute_column("LOE", AttributeType.NUMERIC)
        db.add_well_attribute_column("SevTax", AttributeType.NUMERIC)
    return path


def ref(path, model_type, model_name, segment_key, field_name, attr_column):
    exec_sql(
        path,
        [
            (
                "insert into EconModelFieldRefs values (?, ?, ?, ?, ?)",
                (model_type, model_name, segment_key, field_name, attr_column),
            )
        ],
    )


def expense(kind=ExpenseModelKind.SIMPLE, fixed=2500.0, **start):
    return ExpenseModelSegment(
        kind=kind,
        fixed_monthly=fixed,
        variable_oil=4.0,
        variable_gas=0.3,
        variable_water=1.0,
        **start,
    )


def dated(day, fixed=2500.0):
    return expense(ExpenseModelKind.DATE_BASED, fixed, effective_date=day)


def aged(months, fixed=2500.0):
    return expense(ExpenseModelKind.AGE_BASED, fixed, age_months=months)


def tax(kind=TaxModelKind.SIMPLE, **start):
    return TaxModelSegment(
        kind=kind,
        sev_tax_oil=0.046,
        sev_tax_gas=0.075,
        sev_tax_ngl=0.075,
        ad_valorum_tax=0.02,
        **start,
    )


def diff(day):
    return DiffModelSegment(
        start_date=day,
        oil_method=DiffType.DOLLAR,
        oil_diff=-2.0,
        gas_method=DiffType.FRACTION,
        gas_diff=0.9,
        ngl_method=DiffType.FRACTION,
        ngl_diff=0.4,
    )


def quiet(db_call):
    """Run a write that must not warn."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        db_call()


# ── reading ──────────────────────────────────────────────────────────────────


def test_a_file_without_the_table_has_no_references(tmp_path):
    path = make(tmp_path, refs_table=False)
    with Database.open(path) as db:
        assert db.model_field_refs() == []
        assert db.model_field_refs(ModelKind.EXPENSE, "OPEX") == []


def test_a_reference_names_its_model_segment_field_and_column(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    with Database.open(path) as db:
        assert db.model_field_refs() == [
            ModelFieldRef(
                kind=ModelKind.EXPENSE, name="OPEX", segment=0, field="fixed_monthly", column="LOE"
            )
        ]


def test_the_segment_is_the_place_in_the_models_own_reader(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_expense_model("STEPPED", [aged(24, 900.0), aged(0, 1500.0), aged(6, 1200.0)])
    ref(path, "Expense", "STEPPED", "AGEBASED(6)", "fixed_monthly", "LOE")
    ref(path, "Expense", "STEPPED", "AGEBASED(24)", "variable_oil", "LOE")
    with Database.open(path) as db:
        segments = db.expense_models("STEPPED")
        refs = db.model_field_refs(name="STEPPED")
        assert {segments[r.segment].age_months: r.field for r in refs} == {
            6: "fixed_monthly",
            24: "variable_oil",
        }


def test_a_reference_tied_to_no_segment_is_not_listed(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "AGEBASED(12)", "fixed_monthly", "LOE")  # OPEX has one segment
    ref(path, "Expense", "GONE", "SIMPLE", "fixed_monthly", "LOE")  # no such model
    with Database.open(path) as db:
        assert db.model_field_refs() == []


def test_one_segment_is_keyed_simple_and_a_simple_row_among_others_by_its_date(tmp_path):
    path = make(tmp_path)
    # One dated segment: Obsidian still keys the model's only segment SIMPLE.
    with Database.open(path, change_log=False) as db:
        db.set_expense_model("ONE", [dated(date(2026, 1, 1))])
    ref(path, "Expense", "ONE", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Expense", "ONE", "DATEBASED(2026-01-01)", "variable_oil", "LOE")
    # A SIMPLE row beside a dated one starts on 2000-01-01.
    exec_sql(
        path,
        [
            "insert into ExpenseModel values ('TWO', 'SIMPLE', 1.0, 1.0, 1.0, 1.0)",
            "insert into ExpenseModel values ('TWO', 'DATEBASED(2027-01-01)', 2.0, 1.0, 1.0, 1.0)",
        ],
    )
    ref(path, "Expense", "TWO", "DATEBASED(2000-01-01)", "fixed_monthly", "LOE")
    ref(path, "Expense", "TWO", "SIMPLE", "variable_gas", "LOE")
    with Database.open(path) as db:
        assert [(r.name, r.segment, r.field) for r in db.model_field_refs()] == [
            ("ONE", 0, "fixed_monthly"),
            ("TWO", 1, "fixed_monthly"),
        ]
        assert db.expense_models("TWO")[1].kind is ExpenseModelKind.SIMPLE


@pytest.mark.parametrize("stored", ["ad_valorum_tax", "ad_valorem_tax"])
def test_the_ad_valorem_rate_reads_under_the_correct_spelling(tmp_path, stored):
    path = make(tmp_path)
    ref(path, "Tax", "TAX", "SIMPLE", stored, "SevTax")
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")
    with Database.open(path) as db:
        assert [r.field for r in db.model_field_refs(ModelKind.TAX)] == [
            "ad_valorem_tax",
            "sev_tax_oil",
        ]


def test_a_differential_segment_is_keyed_by_its_month_and_shrink_yield_has_one(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_diff_model("BASIS", [diff(date(2026, 1, 15)), diff(date(2027, 6, 1))])
    ref(path, "Diff", "BASIS", "DATEBASED(2026-01-01)", "oil_diff", "LOE")
    ref(path, "Diff", "BASIS", "DATEBASED(2027-06-01)", "condensate_diff", "LOE")
    ref(path, "Diff", "DIFF", "DATEBASED(2026-01-01)", "gas_diff", "LOE")  # one segment, dated
    ref(path, "ShrinkYield", "SY", "SIMPLE", "condensate_yield_bbl_mmscf", "LOE")
    with Database.open(path) as db:
        assert [(r.kind, r.name, r.segment, r.field) for r in db.model_field_refs()] == [
            (ModelKind.DIFF, "BASIS", 0, "oil_diff"),
            (ModelKind.DIFF, "BASIS", 1, "condensate_diff"),
            (ModelKind.DIFF, "DIFF", 0, "gas_diff"),
            (ModelKind.SHRINK_YIELD, "SY", 0, "condensate_yield_bbl_mmscf"),
        ]


def test_rows_obsidian_would_skip_are_skipped(tmp_path):
    path = make(tmp_path)
    ref(path, "Royalty", "OPEX", "SIMPLE", "fixed_monthly", "LOE")  # a kind this era lacks
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "  ")  # no column named
    ref(path, "Expense", "OPEX", "SIMPLE", "variable_oil", "LOE")
    with Database.open(path) as db:
        assert [r.field for r in db.model_field_refs()] == ["variable_oil"]


def test_filters_and_a_wells_own_models(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["insert into ExpenseModel values ('MAIN<>P1', 'SIMPLE', 1.0, 1.0, 1.0, 1.0)"])
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Expense", "MAIN<>P1", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")
    with Database.open(path) as db:
        assert [r.name for r in db.model_field_refs()] == ["OPEX", "TAX"]
        assert [r.name for r in db.model_field_refs(ModelKind.TAX)] == ["TAX"]
        assert [r.name for r in db.model_field_refs(name="OPEX")] == ["OPEX"]
        assert [r.name for r in db.model_field_refs(name="MAIN<>P1")] == ["MAIN<>P1"]
        assert db.model_field_refs(ModelKind.PRICE) == []
        assert db.model_field_refs(ModelKind.TAX, "OPEX") == []


def test_references_as_a_dataframe(tmp_path):
    pytest.importorskip("pandas")
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    with Database.open(path) as db:
        frame = db.model_field_refs_df()
    assert list(frame.columns) == ["kind", "name", "segment", "field", "column"]
    assert frame.iloc[0].tolist() == ["expense", "OPEX", 0, "fixed_monthly", "LOE"]


# ── a model written over its references ──────────────────────────────────────


def test_writing_a_model_whose_field_is_per_well_warns_and_keeps_the_reference(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    before = field_refs(path)
    with Database.open(path) as db:
        with pytest.warns(ModelFieldRefWarning, match=r"fixed_monthly \(from 'LOE'\)") as caught:
            db.set_expense_model("OPEX", [expense(fixed=9000.0)])
        assert db.expense_models("OPEX")[0].fixed_monthly == 9000.0
    assert len(caught) == 1 and caught[0].filename == __file__, "blamed on the caller's line"
    assert field_refs(path) == before


def test_a_model_without_references_is_written_without_a_word(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OTHER", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Tax", "OPEX", "SIMPLE", "sev_tax_oil", "SevTax")  # another kind's, same name
    with Database.open(path) as db:
        quiet(lambda: db.set_expense_model("OPEX", [expense()]))
        quiet(lambda: db.set_expense_model("OPEX", [aged(0), aged(12)]))
        quiet(lambda: db.set_tax_model("TAX", [tax()]))
        quiet(lambda: db.set_diff_model("DIFF", [diff(date(2026, 1, 1)), diff(date(2028, 1, 1))]))
        quiet(lambda: db.set_shrink_yield_model("SY", gas_shrink_frac=0.2, ngl_yield_bbl_mmscf=1.0))


def test_references_move_with_their_segments_when_the_starts_move(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_expense_model("STEPPED", [aged(0), aged(12), aged(36)])
    ref(path, "Expense", "STEPPED", "AGEBASED(0)", "fixed_monthly", "LOE")
    ref(path, "Expense", "STEPPED", "AGEBASED(36)", "variable_oil", "LOE")
    with Database.open(path) as db, pytest.warns(ModelFieldRefWarning):
        # The same three steps, the later two a year on.
        db.set_expense_model("STEPPED", [aged(48), aged(0), aged(24)])
    assert field_refs(path) == [
        ("Expense", "STEPPED", "AGEBASED(0)", "fixed_monthly", "LOE"),
        ("Expense", "STEPPED", "AGEBASED(48)", "variable_oil", "LOE"),
    ]


def test_a_reference_follows_its_segment_from_simple_to_dated_and_back(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    with Database.open(path) as db:
        # Still one segment, so still keyed SIMPLE whatever its kind.
        with pytest.warns(ModelFieldRefWarning):
            db.set_expense_model("OPEX", [dated(date(2026, 6, 1))])
        assert field_refs(path) == [("Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")]
        assert [r.segment for r in db.model_field_refs(name="OPEX")] == [0]


def test_a_write_that_changes_the_number_of_segments_is_refused_whole(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    before = path.read_bytes()
    with Database.open(path) as db:
        with pytest.raises(ValidationError, match="per-well field references") as error:
            db.set_expense_model("OPEX", [aged(0), aged(12)])
        assert "fixed_monthly (column 'LOE')" in str(error.value)
        assert "1 segments (this call has 2)" in str(error.value)
        assert len(db.expense_models("OPEX")) == 1
    assert path.read_bytes() == before


def test_a_reference_tied_to_no_segment_never_blocks_a_write(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "AGEBASED(12)", "fixed_monthly", "LOE")  # OPEX is one segment
    ref(path, "Expense", "OPEX", "AGEBASED(60)", "variable_oil", "LOE")
    with Database.open(path) as db:
        # No reference is on a segment the model has, so the table is left
        # alone, as Obsidian's own save leaves it; any number of segments goes.
        # The write gives the model a month-12 segment, the first row's key,
        # and the warning says that field is now read per well.
        with pytest.warns(ModelFieldRefWarning, match="fixed_monthly") as caught:
            db.set_expense_model("OPEX", [aged(0), aged(12)])
        assert "variable_oil" not in str(caught[0].message)
        assert [(r.segment, r.field) for r in db.model_field_refs()] == [(1, "fixed_monthly")]
    assert field_refs(path) == [
        ("Expense", "OPEX", "AGEBASED(12)", "fixed_monthly", "LOE"),
        ("Expense", "OPEX", "AGEBASED(60)", "variable_oil", "LOE"),
    ]


def test_a_stray_reference_does_not_come_alive_on_a_moved_segment(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_expense_model("STEPPED", [aged(0), aged(12)])
    ref(path, "Expense", "STEPPED", "AGEBASED(12)", "fixed_monthly", "LOE")
    ref(path, "Expense", "STEPPED", "AGEBASED(24)", "variable_water", "LOE")  # tied to nothing
    with Database.open(path) as db, pytest.warns(ModelFieldRefWarning):
        db.set_expense_model("STEPPED", [aged(0), aged(24)])
    # The live reference moved to month 24; the stray one keyed there is gone
    # rather than starting to apply to a segment it was never set on.
    assert field_refs(path) == [("Expense", "STEPPED", "AGEBASED(24)", "fixed_monthly", "LOE")]


def test_tax_differential_and_shrink_yield_follow_the_same_rules(tmp_path):
    path = make(tmp_path)
    ref(path, "Tax", "TAX", "SIMPLE", "ad_valorum_tax", "SevTax")
    ref(path, "Diff", "DIFF", "DATEBASED(2026-01-01)", "oil_diff", "LOE")
    ref(path, "ShrinkYield", "SY", "SIMPLE", "gas_shrink_frac", "LOE")
    with Database.open(path) as db:
        with pytest.warns(ModelFieldRefWarning, match=r"ad_valorem_tax \(from 'SevTax'\)"):
            db.set_tax_model("TAX", [tax()])
        with pytest.raises(ValidationError, match="per-well field references"):
            db.set_tax_model(
                "TAX",
                [
                    tax(TaxModelKind.AGE_BASED, age_months=0),
                    tax(TaxModelKind.AGE_BASED, age_months=12),
                ],
            )
        with pytest.warns(ModelFieldRefWarning, match="oil_diff"):
            db.set_diff_model("DIFF", [diff(date(2027, 3, 20))])  # the one segment, moved
        with pytest.raises(ValidationError, match="per-well field references"):
            db.set_diff_model("DIFF", [diff(date(2027, 3, 1)), diff(date(2028, 3, 1))])
        with pytest.warns(ModelFieldRefWarning, match="gas_shrink_frac"):
            db.set_shrink_yield_model("SY", gas_shrink_frac=0.2, ngl_yield_bbl_mmscf=1.0)
    assert field_refs(path) == [
        ("Diff", "DIFF", "DATEBASED(2027-03-01)", "oil_diff", "LOE"),
        ("ShrinkYield", "SY", "SIMPLE", "gas_shrink_frac", "LOE"),
        ("Tax", "TAX", "SIMPLE", "ad_valorum_tax", "SevTax"),
    ]


# ── a model deleted ──────────────────────────────────────────────────────────


def test_a_deleted_model_takes_its_references_and_no_one_elses(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")
    ref(path, "Diff", "DIFF", "DATEBASED(2026-01-01)", "oil_diff", "LOE")
    ref(path, "ShrinkYield", "SY", "SIMPLE", "gas_shrink_frac", "LOE")
    ref(path, "Tax", "OPEX", "SIMPLE", "sev_tax_oil", "SevTax")  # another kind, same name
    with Database.open(path) as db:
        db.delete_expense_model("OPEX", confirm=True)
        assert ("Tax", "OPEX", "SIMPLE", "sev_tax_oil", "SevTax") in field_refs(path)
        assert len(field_refs(path)) == 4
        db.delete_tax_model("TAX", confirm=True)
        db.delete_diff_model("DIFF", confirm=True)
        db.delete_shrink_yield_model("SY", confirm=True)
    assert field_refs(path) == [("Tax", "OPEX", "SIMPLE", "sev_tax_oil", "SevTax")]


def test_a_model_made_again_under_a_deleted_name_starts_without_references(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    with Database.open(path) as db:
        db.delete_expense_model("OPEX", confirm=True)
        quiet(lambda: db.set_expense_model("OPEX", [expense()]))
        assert db.model_field_refs() == []


# ── a well's own models ──────────────────────────────────────────────────────


def own_expense_model(path, prop_id, scenario="MAIN"):
    """Give the well an individual expense model with a per-well field, as
    Obsidian stores one."""
    name = f"{scenario}<>{prop_id}"
    exec_sql(
        path,
        [
            (
                "insert into ExpenseModel values (?, 'SIMPLE', 10.0, 1.0, 2.0, 3.0)",
                (name,),
            ),
            (
                "update WellModels set exp_model_name = ? where prop_id = ? and scenario = ?",
                (name, prop_id, scenario),
            ),
        ],
    )
    ref(path, "Expense", name, "SIMPLE", "fixed_monthly", "LOE")
    return name


def test_a_copied_well_gets_its_own_models_references(tmp_path):
    path = make(tmp_path)
    own_expense_model(path, "P1")
    with Database.open(path) as db:
        db.copy_well("P1", "P9")
        assert [r.column for r in db.model_field_refs(name="MAIN<>P9")] == ["LOE"]
    assert field_refs(path) == [
        ("Expense", "MAIN<>P1", "SIMPLE", "fixed_monthly", "LOE"),
        ("Expense", "MAIN<>P9", "SIMPLE", "fixed_monthly", "LOE"),
    ]


def test_a_deleted_well_takes_its_own_models_references(tmp_path):
    path = make(tmp_path)
    own_expense_model(path, "P1")
    own_expense_model(path, "P2")
    with Database.open(path) as db:
        db.delete_well("P1", confirm=True)
    assert field_refs(path) == [("Expense", "MAIN<>P2", "SIMPLE", "fixed_monthly", "LOE")]


def test_a_copied_scenario_gets_them_and_a_deleted_scenario_takes_them(tmp_path):
    path = make(tmp_path)
    own_expense_model(path, "P1")
    with Database.open(path) as db:
        db.create_scenario("HIGH", copy_from="MAIN")
        assert field_refs(path) == [
            ("Expense", "HIGH<>P1", "SIMPLE", "fixed_monthly", "LOE"),
            ("Expense", "MAIN<>P1", "SIMPLE", "fixed_monthly", "LOE"),
        ]
        db.delete_scenario("HIGH", confirm=True)
    assert field_refs(path) == [("Expense", "MAIN<>P1", "SIMPLE", "fixed_monthly", "LOE")]


# ── an attribute column renamed or deleted ───────────────────────────────────


def test_references_follow_a_renamed_attribute_column(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "loe")  # stored in another case
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")
    with Database.open(path) as db:
        db.rename_well_attribute_column("LOE", "LeaseOpex")
        assert [r.column for r in db.model_field_refs()] == ["LeaseOpex", "SevTax"]


def test_a_column_a_model_reads_is_not_deleted_from_under_it(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "LOE")
    before = path.read_bytes()
    with Database.open(path) as db:
        with pytest.raises(ValidationError, match="read per well") as error:
            db.delete_well_attribute_column("LOE", confirm=True)
        assert "Expense 'OPEX', Tax 'TAX'" in str(error.value)
        assert "detach_model_refs=True" in str(error.value)
        assert [column.name for column in db.list_attribute_columns()] == ["LOE", "SevTax"]
    assert path.read_bytes() == before


def test_detach_removes_the_references_with_the_column(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "fixed_monthly", "LOE")
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")
    with Database.open(path) as db:
        db.delete_well_attribute_column("LOE", confirm=True, detach_model_refs=True)
        assert [column.name for column in db.list_attribute_columns()] == ["SevTax"]
        # The model keeps the number it stored.
        assert db.expense_models("OPEX")[0].fixed_monthly == 1.0
    assert field_refs(path) == [("Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")]


def test_a_column_no_model_reads_is_deleted_as_before(tmp_path):
    path = make(tmp_path)
    ref(path, "Tax", "TAX", "SIMPLE", "sev_tax_oil", "SevTax")
    with Database.open(path) as db:
        db.delete_well_attribute_column("LOE", confirm=True)
        assert [column.name for column in db.list_attribute_columns()] == ["SevTax"]


# ── a file from before the table ─────────────────────────────────────────────


def test_every_writer_works_on_a_file_without_the_table(tmp_path):
    path = make(tmp_path, refs_table=False)
    exec_sql(path, ["insert into ExpenseModel values ('MAIN<>P1', 'SIMPLE', 1.0, 1.0, 1.0, 1.0)"])
    exec_sql(path, ["update WellModels set exp_model_name = 'MAIN<>P1' where prop_id = 'P1'"])
    with Database.open(path) as db:

        def writes():
            db.set_expense_model("OPEX", [aged(0), aged(12)])
            db.set_tax_model("TAX", [tax()])
            db.set_diff_model("DIFF", [diff(date(2027, 1, 1))])
            db.set_shrink_yield_model("SY", gas_shrink_frac=0.2, ngl_yield_bbl_mmscf=1.0)
            db.copy_well("P1", "P9")
            db.create_scenario("HIGH", copy_from="MAIN")
            db.delete_scenario("HIGH", confirm=True)
            db.delete_well("P9", confirm=True)
            db.rename_well_attribute_column("LOE", "LeaseOpex")
            db.delete_well_attribute_column("LeaseOpex", confirm=True)
            db.delete_expense_model("OPEX", confirm=True)
            db.delete_tax_model("TAX", confirm=True)
            db.delete_diff_model("DIFF", confirm=True)
            db.delete_shrink_yield_model("SY", confirm=True)

        quiet(writes)
    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("select name from sqlite_master where type = 'table'")}
    conn.close()
    assert "EconModelFieldRefs" not in tables, "never created by the library"
