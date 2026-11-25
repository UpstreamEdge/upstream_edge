"""Regression tests for defects an independent review found before 1.3.0:
forecasts, per-well field references, and what the well writers accept."""

from __future__ import annotations

import math
import sqlite3
import struct
import warnings
from datetime import date, datetime

import pytest
from conftest import (
    create_bookkeeping_schema,
    create_field_refs_schema,
    create_scenario_schema,
    exec_sql,
    field_refs,
    seed_models,
)

from upstream_edge.obsidian_db import (
    AttributeType,
    CapexItem,
    CapexJobType,
    Database,
    DiffModelSegment,
    DiffType,
    ExpenseModelKind,
    ExpenseModelSegment,
    ForecastKind,
    ForecastSegment,
    InterestSegment,
    ModelFieldRefWarning,
    MonthlyVolumesWarning,
    Phase,
    RsvCat,
    ValidationError,
)

SEGMENT = ForecastSegment(
    start=date(2026, 1, 1), rate_init=500.0, decline_init=0.6, b_factor=1.1, decline_min=0.06
)


def blob(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def make(tmp_path):
    """Scenario MAIN, the seeded models (OPEX, TAX, DIFF, SY), wells P1 and P2,
    with the forecast index and the side tables a current file has."""
    path = tmp_path / "review.obsdb"
    create_scenario_schema(path)
    seed_models(path)
    create_bookkeeping_schema(path)
    create_field_refs_schema(path)
    with Database.open(path, change_log=False) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.add_well("P2", rsv_cat=RsvCat.PDP)
    return path


def store(path, prop_id, model, start, oil, gas, water):
    exec_sql(
        path,
        [
            (
                "INSERT INTO ForecastVolumes VALUES (?, ?, ?, ?, ?, ?, 'Simulation', 'r', '')",
                (prop_id, model, start, blob(*oil), blob(*gas), blob(*water)),
            )
        ],
    )


def sql(path, statement, *params):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(statement, params).fetchall()
    finally:
        conn.close()


# ── forecasts ────────────────────────────────────────────────────────────────


def test_a_tail_after_the_stored_months_is_written_without_a_warning(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [3.0, 2.0, 1.0], [0.0] * 3, [0.0] * 3)
    tail = ForecastSegment(date(2026, 6, 1), 90.0, 0.4, 1.0, 0.06)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.set_forecast("P1", "SIM", Phase.OIL, [tail])


@pytest.mark.parametrize("start", [date(2026, 5, 1), date(2026, 3, 1), date(2024, 1, 1)])
def test_a_segment_that_starts_before_the_stored_months_end_warns(tmp_path, start):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [3.0, 2.0, 1.0], [0.0] * 3, [0.0] * 3)
    segment = ForecastSegment(start, 90.0, 0.4, 1.0, 0.06)
    with Database.open(path) as db, pytest.warns(MonthlyVolumesWarning, match="before 2026-06"):
        db.set_forecast("P1", "SIM", Phase.OIL, [segment])


def test_a_phase_spelled_in_another_case_is_ordered_replaced_and_deleted(tmp_path):
    """Library 1.0 wrote "Oil"; a file can still hold such rows."""
    path = make(tmp_path)
    exec_sql(
        path,
        ["INSERT INTO Forecast VALUES ('P1', 'M', 'Oil', '2024-01-01', NULL, 1.0, 0.5, 1.0, 0.06)"],
    )
    later = ForecastSegment(date(2025, 1, 1), 90.0, 0.4, 1.0, 0.06)
    with Database.open(path, change_log=False) as db:
        exec_sql(
            path,
            [
                "INSERT INTO Forecast VALUES ('P1', 'M', 'OIL', '2025-01-01', NULL, 2.0, 0.5, 1.0, 0.06)"
            ],
        )
        assert [row.start for row in db.forecasts("P1", "M")] == [
            date(2024, 1, 1),
            date(2025, 1, 1),
        ]
        db.set_forecast("P1", "M", Phase.OIL, [later])  # replaces both spellings
        assert [(row.start, row.rate_init) for row in db.forecasts("P1", "M")] == [
            (date(2025, 1, 1), 90.0)
        ]
        exec_sql(
            path,
            [
                "INSERT INTO Forecast VALUES ('P1', 'M', 'oil', '2023-01-01', NULL, 3.0, 0.5, 1.0, 0.06)"
            ],
        )
        db.delete_forecast("P1", "M", Phase.OIL, confirm=True)
        assert db.forecasts("P1", "M") == []


def test_a_date_with_a_time_reads_as_obsidian_reads_it(tmp_path):
    path = make(tmp_path)
    exec_sql(
        path,
        [
            "INSERT INTO Forecast VALUES ('P1', 'M', 'OIL', '2024-01-01 00:00:00', NULL, 1.0, 0.5, 1.0, 0.06)"
        ],
    )
    with Database.open(path) as db:
        assert [row.start for row in db.forecasts()] == [date(2024, 1, 1)]


def test_a_row_of_stored_months_cannot_be_written_back_as_a_segment(tmp_path):
    """A script that copies forecasts() rows into segments must not write a
    segment with no numbers where the stored months were."""
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [3.0], [0.0], [0.0])
    before = path.read_bytes()
    with Database.open(path) as db:
        (stored,) = db.forecasts("P1", "SIM")
        assert stored.kind is ForecastKind.MONTHLY_VOLUMES
        copied = ForecastSegment(
            start=stored.start,
            rate_init=stored.rate_init,  # type: ignore[arg-type]
            decline_init=stored.decline_init,  # type: ignore[arg-type]
            b_factor=stored.b_factor,  # type: ignore[arg-type]
            decline_min=stored.decline_min,  # type: ignore[arg-type]
            type_curve=stored.type_curve,
        )
        with pytest.raises(ValidationError, match="MONTHLY_VOLUMES"):
            db.set_forecast("P1", "COPY", Phase.OIL, [copied])
        with pytest.raises(ValidationError, match="rate_init=nan"):
            db.set_forecast(
                "P1",
                "COPY",
                Phase.OIL,
                [ForecastSegment(date(2026, 1, 1), math.nan, 0.5, 1.0, 0.06)],
            )
    assert path.read_bytes() == before


def test_a_type_curve_segment_needs_no_numbers_of_its_own(tmp_path):
    path = make(tmp_path)
    curve = ForecastSegment(date(2026, 1, 1), None, None, None, None, type_curve="TC")  # type: ignore[arg-type]
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [curve])
        (row,) = db.forecasts("P1")
        assert (row.kind, row.type_curve, row.rate_init) == (ForecastKind.TYPE_CURVE, "TC", None)


def test_stored_months_with_no_model_or_start_are_no_ones_forecast(tmp_path):
    path = make(tmp_path)
    store(path, "P1", " ", "2026-03-01", [3.0], [0.0], [0.0])
    store(path, "P1", "SIM", "March", [3.0], [0.0], [0.0])
    exec_sql(
        path,
        [
            (
                "INSERT INTO ForecastVolumes VALUES ('P2', NULL, '2026-03-01', ?, ?, ?, '', '', '')",
                (blob(1.0),) * 3,
            )
        ],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            assert db.forecasts() == []
            db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])  # nothing to warn about


# ── per-well field references ────────────────────────────────────────────────


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


def diff(day):
    return DiffModelSegment(
        day, DiffType.DOLLAR, -2.0, DiffType.FRACTION, 0.9, DiffType.FRACTION, 0.4
    )


def expense(kind=ExpenseModelKind.SIMPLE, **start):
    return ExpenseModelSegment(kind, 2500.0, 4.0, 0.3, 1.0, **start)


def test_two_differential_segments_in_one_month_are_refused_when_a_reference_is_at_stake(tmp_path):
    """Obsidian keys a differential segment by its month, so the two would
    share one reference and the other would be lost."""
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_diff_model("D2", [diff(date(2026, 1, 1)), diff(date(2026, 6, 1))])
    ref(path, "Diff", "D2", "DATEBASED(2026-01-01)", "oil_diff", "A")
    ref(path, "Diff", "D2", "DATEBASED(2026-06-01)", "oil_diff", "B")
    before = field_refs(path)
    with Database.open(path) as db:
        with pytest.raises(ValidationError, match="start together"):
            db.set_diff_model("D2", [diff(date(2026, 1, 1)), diff(date(2026, 1, 15))])
        assert len(db.diff_models("D2")) == 2
    assert field_refs(path) == before


def test_a_simple_segment_beside_one_dated_2000_is_refused_the_same_way(tmp_path):
    path = make(tmp_path)
    dated = [
        expense(ExpenseModelKind.DATE_BASED, effective_date=date(2025, 1, 1)),
        expense(ExpenseModelKind.DATE_BASED, effective_date=date(2026, 1, 1)),
    ]
    with Database.open(path, change_log=False) as db:
        db.set_expense_model("E2", dated)
    ref(path, "Expense", "E2", "DATEBASED(2025-01-01)", "fixed_monthly", "A")
    ref(path, "Expense", "E2", "DATEBASED(2026-01-01)", "fixed_monthly", "B")
    before = field_refs(path)
    clash = [expense(), expense(ExpenseModelKind.DATE_BASED, effective_date=date(2000, 1, 1))]
    with Database.open(path) as db, pytest.raises(ValidationError, match="start together"):
        db.set_expense_model("E2", clash)
    assert field_refs(path) == before


def test_a_model_with_no_references_may_still_hold_two_segments_in_a_month(tmp_path):
    path = make(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.set_diff_model("D3", [diff(date(2026, 1, 1)), diff(date(2026, 1, 20))])


def test_a_reference_shared_by_two_segments_of_one_month_is_listed_on_both(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_diff_model("D3", [diff(date(2026, 1, 1)), diff(date(2026, 1, 20))])
    ref(path, "Diff", "D3", "DATEBASED(2026-01-01)", "oil_diff", "A")
    with Database.open(path) as db:
        assert [(r.segment, r.field, r.column) for r in db.model_field_refs(name="D3")] == [
            (0, "oil_diff", "A"),
            (1, "oil_diff", "A"),
        ]


def test_only_a_field_of_the_models_own_kind_is_a_reference(tmp_path):
    path = make(tmp_path)
    ref(path, "Expense", "OPEX", "SIMPLE", "sev_tax_oil", "A")  # a tax field on an expense model
    ref(path, "Expense", "OPEX", "SIMPLE", "variable_oil", "B")
    ref(path, "Tax", "TAX", "SIMPLE", "fixed_monthly", "C")
    with Database.open(path) as db:
        assert [(r.name, r.field) for r in db.model_field_refs()] == [("OPEX", "variable_oil")]
        with pytest.warns(ModelFieldRefWarning, match="variable_oil") as caught:
            db.set_expense_model("OPEX", [expense()])
        assert "sev_tax_oil" not in str(caught[0].message)


def test_both_spellings_of_the_ad_valorem_key_are_one_reference(tmp_path):
    path = make(tmp_path)
    ref(path, "Tax", "TAX", "SIMPLE", "ad_valorem_tax", "NEWER")
    ref(path, "Tax", "TAX", "SIMPLE", "ad_valorum_tax", "KEPT")
    with Database.open(path) as db:
        # Obsidian keeps the row under the spelling it writes.
        assert [(r.field, r.column) for r in db.model_field_refs()] == [("ad_valorem_tax", "KEPT")]


# ── what the writers accept ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [math.nan, math.inf, None, "75", True])
def test_a_rate_or_cost_that_is_not_a_number_is_refused(tmp_path, bad):
    path = make(tmp_path)
    before = path.read_bytes()
    day = date(2026, 1, 1)
    with Database.open(path) as db:
        with pytest.raises(ValidationError, match="wi_pct"):
            db.set_interest("P1", "MAIN", [InterestSegment(day, bad, 75.0)])
        with pytest.raises(ValidationError, match="nri_pct"):
            db.set_interest("P1", "MAIN", [InterestSegment(day, 100.0, bad)])
        with pytest.raises(ValidationError, match="cost_gross"):
            db.set_abandonment("P1", "MAIN", bad)
        with pytest.raises(ValidationError, match="cost_gross"):
            db.set_capex("P1", "MAIN", [CapexItem(day, CapexJobType.DRILLING, bad)])
        assert len(db.interest("P1")) == 1
    assert path.read_bytes() == before


def test_an_empty_rate_or_cost_in_the_file_reads_as_zero_and_fails_no_other_well(tmp_path):
    path = make(tmp_path)
    exec_sql(
        path,
        [
            "UPDATE Interest SET wi_pct = NULL WHERE prop_id = 'P1'",
            "UPDATE Abandonment SET cost_gross = NULL WHERE prop_id = 'P1'",
            "INSERT INTO Capex VALUES ('P1', 'MAIN', '2026-01-01', 'Drilling', NULL, '')",
        ],
    )
    with Database.open(path) as db:
        assert [(row.prop_id, row.wi_pct) for row in db.interest()] == [("P1", 0.0), ("P2", 100.0)]
        assert [row.cost_gross for row in db.abandonment()] == [0.0, 100000.0]
        assert [row.cost_gross for row in db.capex()] == [0.0]


def test_a_datetime_is_written_as_its_date(tmp_path):
    """A datetime, or a pandas Timestamp, is a date too. Written with its time
    it would be a value neither Obsidian nor this library reads back."""
    path = make(tmp_path)
    moment = datetime(2021, 3, 1, 14, 30)
    with Database.open(path, change_log=False) as db:
        db.set_well_header("P1", spud_date=moment)
        db.set_interest("P1", "MAIN", [InterestSegment(moment, 100.0, 75.0)])
        db.set_capex("P1", "MAIN", [CapexItem(moment, CapexJobType.DRILLING, 1.0)])
        db.set_forecast("P1", "MAIN", Phase.OIL, [ForecastSegment(moment, 1.0, 0.5, 1.0, 0.06)])
        db.set_diff_model("DD", [diff(moment)])
        assert db.wells()[0].spud_date == date(2021, 3, 1)
        assert db.interest("P1")[0].start == db.capex("P1")[0].date == date(2021, 3, 1)
        assert db.forecasts("P1")[0].start == db.diff_models("DD")[0].start_date == date(2021, 3, 1)
    stored = sql(path, "SELECT spud FROM Main WHERE prop_id = 'P1'") + sql(
        path, "SELECT start FROM Interest WHERE prop_id = 'P1'"
    )
    assert stored == [("2021-03-01",), ("2021-03-01",)]


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("spud_date", "01/15/2020"),
        ("first_prod", "garbage"),
        ("first_production_date", True),
        ("completion_date", 20200101),
        ("tvd_ft", "abc"),
        ("lateral_length_ft", math.nan),
        ("bh_latitude", "31.5"),
    ],
)
def test_a_header_value_the_readers_could_not_read_back_is_refused(tmp_path, field, bad):
    path = make(tmp_path)
    before = path.read_bytes()
    with Database.open(path) as db:
        with pytest.raises(ValidationError):
            db.set_well_header("P1", **{field: bad})
        with pytest.raises(ValidationError):
            db.add_well("P9", rsv_cat=RsvCat.PDP, **{field: bad})
        assert len(db.wells()) == 2
    assert path.read_bytes() == before


def test_a_header_date_given_as_iso_text_is_taken(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_well_header("P1", spud_date="2020-01-15", tvd_ft=9000, well_number=7)
        well = db.well("P1")
    assert well is not None
    assert (well.spud_date, well.tvd_ft, well.well_number) == (date(2020, 1, 15), 9000.0, "7")


@pytest.mark.parametrize(
    "bad",
    ["", " ", " P9", "P9 ", "P\n9", "A<>B", "SEVENTEEN_BYTES_X", "ÉÉÉÉÉÉÉÉÉ", None, 17],
)
def test_an_id_obsidian_could_not_load_is_refused_for_a_new_well(tmp_path, bad):
    """Obsidian keeps a PropID in 16 bytes and skips a longer one at load."""
    path = make(tmp_path)
    with Database.open(path) as db:
        with pytest.raises(ValidationError, match="prop_id"):
            db.add_well(bad, rsv_cat=RsvCat.PDP)
        with pytest.raises(ValidationError, match="to_prop_id"):
            db.copy_well("P1", bad)
        assert len(db.wells()) == 2


def test_sixteen_bytes_is_an_id(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.add_well("SIXTEEN_BYTES_OK", rsv_cat=RsvCat.PDP)
        db.copy_well("P1", "ÉÉÉÉÉÉÉÉ")  # eight characters, sixteen bytes
        assert len(db.wells()) == 4


@pytest.mark.parametrize("api", ["１２３４５６７８９０", "12345 6789", "123456789"])
def test_an_api_number_is_ten_ascii_digits(tmp_path, api):
    path = make(tmp_path)
    with Database.open(path) as db, pytest.raises(ValidationError, match="api_10"):
        db.add_well("P9", rsv_cat=RsvCat.PDP, api_10=api)


def test_a_new_well_does_not_inherit_what_an_older_delete_left_behind(tmp_path):
    """`delete_well` before 1.3 left a well's stored forecast months and
    pressures in the file."""
    path = make(tmp_path)
    store(path, "GONE", "SIM", "2026-03-01", [3.0], [0.0], [0.0])
    exec_sql(path, ["INSERT INTO DailyPressure VALUES ('GONE', '2026-01-01', 900.0, 2500.0, 32.0)"])
    with Database.open(path, change_log=False) as db:
        db.add_well("GONE", rsv_cat=RsvCat.PUD)
        assert db.forecasts("GONE") == []
    assert sql(path, "SELECT count(*) FROM DailyPressure") == [(0,)]


def test_a_writer_that_fails_inside_a_transaction_takes_back_its_own_half(tmp_path):
    """The caller catches the error and carries on; what commits must be whole
    writes only."""
    path = make(tmp_path)
    exec_sql(path, ["create unique index idx_Interest on Interest (prop_id, model, start)"])
    bad = [InterestSegment("2026-01-01", 100.0, 75.0)]  # type: ignore[arg-type]
    with Database.open(path, change_log=False) as db:
        with db.transaction():
            db.set_well_header("P1", lease="KEPT")
            with pytest.raises(AttributeError):
                db.set_interest(["P1", "P2"], "MAIN", bad)  # deletes, then fails on its insert
            db.set_well_header("P2", lease="ALSO KEPT")
        assert [(row.prop_id, row.wi_pct) for row in db.interest()] == [
            ("P1", 100.0),
            ("P2", 100.0),
        ]
        assert [well.lease for well in db.wells()] == ["KEPT", "ALSO KEPT"]


def test_attribute_columns_still_work_inside_a_transaction(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db, db.transaction():
        db.add_well_attribute_column("Basin", AttributeType.TEXT)
        db.set_well_attribute("P1", "Basin", "Permian")
        db.rename_well_attribute_column("Basin", "Play")
        assert db.well_attributes("P1") == {"Play": "Permian"}
