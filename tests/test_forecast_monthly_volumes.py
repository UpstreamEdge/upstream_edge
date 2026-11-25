"""A forecast that starts with stored monthly volumes: read as rows of
`forecasts()`, left alone and warned about by `set_forecast`, deleted with the
forecast."""

from __future__ import annotations

import sqlite3
import struct
import warnings
from datetime import date

import pytest
from conftest import change_log, create_bookkeeping_schema, create_core_schema, exec_sql

from upstream_edge.obsidian_db import (
    Database,
    Forecast,
    ForecastKind,
    ForecastSegment,
    MonthlyVolumesWarning,
    Phase,
    RsvCat,
)

SEGMENT = ForecastSegment(
    start=date(2026, 1, 1), rate_init=500.0, decline_init=0.6, b_factor=1.1, decline_min=0.06
)
TAIL = ForecastSegment(
    start=date(2026, 6, 1), rate_init=90.0, decline_init=0.4, b_factor=1.0, decline_min=0.06
)
CURVE = ForecastSegment(
    start=date(2027, 1, 1),
    rate_init=0.0,
    decline_init=0.0,
    b_factor=0.0,
    decline_min=0.0,
    type_curve="TC A",
)


def blob(*values: float) -> bytes:
    """One phase's months as Obsidian stores them: little-endian 32-bit floats."""
    return struct.pack(f"<{len(values)}f", *values)


def store(path, prop_id, model, start, oil, gas, water):
    """Stored monthly volumes, as a saved Spatial Forecasting run writes them."""
    exec_sql(
        path,
        [
            (
                "INSERT INTO ForecastVolumes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    prop_id,
                    model,
                    start,
                    blob(*oil),
                    blob(*gas),
                    blob(*water),
                    "Simulation",
                    "run-1",
                    "2026-10-02T15:04:05+00:00",
                ),
            )
        ],
    )


def stored_rows(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT prop_id, model FROM ForecastVolumes ORDER BY 1, 2").fetchall()
    finally:
        conn.close()


def keys(rows):
    return [(row.prop_id, row.model) for row in rows]


def make(tmp_path, *, bookkeeping=True):
    path = tmp_path / "volumes.obsdb"
    create_core_schema(path)
    if bookkeeping:
        create_bookkeeping_schema(path)
    with Database.open(path, change_log=False) as db:
        for well in ("P1", "P2"):
            db.add_well(well, rsv_cat=RsvCat.PDP)
    return path


# ── reading ──────────────────────────────────────────────────────────────────


def test_one_call_reads_the_stored_months_and_the_segments_after_them(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.OIL, [TAIL, CURVE])
        db.set_forecast("P1", "SIM", Phase.GAS, [TAIL])
    store(path, "P1", "SIM", "2026-03-01", [3000.0, 2500.0, 2000.0], [9.0, 8.0, 7.0], [0.0] * 3)

    with Database.open(path, read_only=True) as db:
        rows = db.forecasts("P1", "SIM")

    assert [(row.phase, row.start, row.kind) for row in rows] == [
        (Phase.GAS, date(2026, 3, 1), ForecastKind.MONTHLY_VOLUMES),
        (Phase.GAS, date(2026, 6, 1), ForecastKind.DECLINE),
        (Phase.OIL, date(2026, 3, 1), ForecastKind.MONTHLY_VOLUMES),
        (Phase.OIL, date(2026, 6, 1), ForecastKind.DECLINE),
        (Phase.OIL, date(2027, 1, 1), ForecastKind.TYPE_CURVE),
    ]
    assert rows[2] == Forecast(
        prop_id="P1",
        model="SIM",
        phase=Phase.OIL,
        start=date(2026, 3, 1),
        type_curve=None,
        rate_init=None,
        decline_init=None,
        b_factor=None,
        decline_min=None,
        monthly_volumes=(3000.0, 2500.0, 2000.0),
    )
    assert rows[0].monthly_volumes == (9.0, 8.0, 7.0), "gas, in Mcf"
    assert rows[3].monthly_volumes is None and rows[3].rate_init == 90.0


def test_a_phase_whose_stored_months_are_all_zero_has_none(tmp_path):
    """Obsidian reads an all-zero phase as having no stored volumes: its
    segments are its forecast."""
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [5.0, 0.0], [0.0, 0.0], [0.0, 1.0])
    with Database.open(path) as db:
        assert [(row.phase, row.monthly_volumes) for row in db.forecasts()] == [
            (Phase.OIL, (5.0, 0.0)),
            (Phase.WATER, (0.0, 1.0)),
        ]


def test_stored_months_sort_ahead_of_a_segment_that_starts_with_them(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])
    store(path, "P1", "SIM", "2026-01-01", [10.0], [10.0], [10.0])
    with Database.open(path) as db:
        oil = [row for row in db.forecasts() if row.phase is Phase.OIL]
    assert [row.kind for row in oil] == [ForecastKind.MONTHLY_VOLUMES, ForecastKind.DECLINE]


def test_the_filters_cover_stored_months_too(tmp_path):
    path = make(tmp_path)
    for prop_id, model in (("P2", "SIM"), ("P1", "SIM"), ("P1", "ALT")):
        store(path, prop_id, model, "2026-01-01", [1.0], [0.0], [0.0])
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])
        assert keys(db.forecasts()) == [("P1", "ALT"), ("P1", "SIM"), ("P2", "MAIN"), ("P2", "SIM")]
        assert keys(db.forecasts("P1")) == [("P1", "ALT"), ("P1", "SIM")]
        assert keys(db.forecasts(model="SIM")) == [("P1", "SIM"), ("P2", "SIM")]
        assert keys(db.forecasts("P2", "SIM")) == [("P2", "SIM")]
        assert db.forecasts("P3") == []


def test_a_start_inside_a_month_is_that_month(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-17", [1.0], [1.0], [1.0])
    with Database.open(path) as db:
        assert {row.start for row in db.forecasts()} == {date(2026, 3, 1)}


def test_a_forecast_without_stored_months_reads_as_it_always_did(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [TAIL, SEGMENT])
        rows = db.forecasts()
    assert [row.start for row in rows] == [date(2026, 1, 1), date(2026, 6, 1)]
    assert all(row.monthly_volumes is None and row.kind is ForecastKind.DECLINE for row in rows)
    # Built the way a 1.2 script builds one, by place, with no volumes argument.
    assert rows[0] == Forecast(
        "P1", "MAIN", Phase.OIL, date(2026, 1, 1), None, 500.0, 0.6, 1.1, 0.06
    )


@pytest.mark.parametrize("bookkeeping", [False, True], ids=["no table", "an empty table"])
def test_a_file_with_no_stored_months(tmp_path, bookkeeping):
    path = make(tmp_path, bookkeeping=bookkeeping)
    with Database.open(path) as db:
        assert db.forecasts() == []
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        assert len(db.forecasts()) == 1


def bad_row(path, prop_id, oil, gas=None, water=None, start="2026-03-01"):
    one = blob(1.0)
    exec_sql(
        path,
        [
            (
                "INSERT INTO ForecastVolumes VALUES (?, 'SIM', ?, ?, ?, ?, 'x', 'r', '')",
                (prop_id, start, oil, one if gas is None else gas, one if water is None else water),
            )
        ],
    )


@pytest.mark.parametrize(
    ("oil", "gas", "why"),
    [
        (blob(1.0, 2.0), blob(1.0), "different numbers of months"),
        (blob(), blob(), "no months"),
        (blob(1.0, -2.0), blob(1.0, 2.0), "negative or not a number"),
        (blob(float("nan")), blob(1.0), "negative or not a number"),
        (blob(float("inf")), blob(1.0), "negative or not a number"),
        (b"\x00\x00\x00", blob(1.0), "oil_bbl is not a series"),
        (b"\x00" * 4 * 1201, blob(1.0), "oil_bbl is not a series"),
        ("text", blob(1.0), "oil_bbl must be stored as a blob"),
        (12, blob(1.0), "oil_bbl must be stored as a blob"),
    ],
)
def test_a_row_obsidian_skips_is_skipped_and_fails_no_other_well(tmp_path, caplog, oil, gas, why):
    """Obsidian drops such a row when it loads the file. Here it is logged and
    left out: that well's segments are its forecast, and every other well reads."""
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])
    water = blob(1.0, 2.0) if why == "negative or not a number" and len(oil) == 8 else None
    bad_row(path, "P1", oil, gas, water)
    store(path, "P2", "SIM", "2026-03-01", [4.0], [0.0], [0.0])

    with caplog.at_level("WARNING", logger="upstream_edge.obsidian_db"):
        with Database.open(path) as db:
            rows = db.forecasts()
    assert [(row.prop_id, row.kind) for row in rows] == [
        ("P1", ForecastKind.DECLINE),
        ("P2", ForecastKind.MONTHLY_VOLUMES),
    ]
    (record,) = caplog.records
    assert "'P1' on 'SIM' skipped" in record.getMessage() and why in record.getMessage()


def test_a_row_with_an_unreadable_start_is_skipped_too(tmp_path, caplog):
    path = make(tmp_path)
    bad_row(path, "P1", blob(1.0), start="March")
    with caplog.at_level("WARNING", logger="upstream_edge.obsidian_db"):
        with Database.open(path) as db:
            assert db.forecasts() == []
    assert "ForecastVolumes.start" in caplog.text


def test_forecasts_as_a_frame_keep_one_row_per_piece(tmp_path):
    pytest.importorskip("pandas")
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.OIL, [TAIL])
    store(path, "P1", "SIM", "2026-03-01", [3.0, 2.0], [0.0, 0.0], [0.0, 0.0])
    with Database.open(path) as db:
        frame = db.forecasts_df("P1")
    assert list(frame.columns)[-2:] == ["decline_min", "monthly_volumes"]
    assert frame["monthly_volumes"].tolist() == [(3.0, 2.0), None]
    assert frame["phase"].tolist() == ["OIL", "OIL"]


# ── writing segments ─────────────────────────────────────────────────────────


def test_writing_segments_under_stored_months_warns_and_still_writes(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [3.0, 2.0, 1.0], [9.0, 8.0, 7.0], [0.0] * 3)
    with Database.open(path) as db:
        with pytest.warns(
            MonthlyVolumesWarning, match="3 stored monthly volumes, 2026-03 up to 2026-06"
        ) as w:
            db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])
        assert len(w) == 1 and w[0].filename == __file__, "blamed on the caller's line"
        kinds = [row.kind for row in db.forecasts("P1", "SIM") if row.phase is Phase.OIL]
        assert kinds == [ForecastKind.DECLINE, ForecastKind.MONTHLY_VOLUMES]
    assert stored_rows(path) == [("P1", "SIM")]


def test_no_warning_where_the_phase_has_no_stored_months(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [3.0], [9.0], [0.0])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.set_forecast("P1", "SIM", Phase.WATER, [SEGMENT])  # the water months are all zero
            db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])  # another model
            db.set_forecast("P2", "SIM", Phase.OIL, [SEGMENT])  # another well


def test_no_warning_in_a_file_without_the_table(tmp_path):
    path = make(tmp_path, bookkeeping=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])


def test_a_refused_write_does_not_warn(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [1.0], [1.0], [1.0])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db, pytest.raises(Exception, match="cannot be empty"):
            db.set_forecast("P1", "SIM", Phase.OIL, [])


def test_a_row_obsidian_skips_neither_warns_nor_fails_a_write(tmp_path):
    path = make(tmp_path)
    bad_row(path, "P1", "x", "x", "x")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])
            db.delete_forecast("P1", "SIM", Phase.OIL, confirm=True)
            assert stored_rows(path) == [("P1", "SIM")]
            db.delete_forecast("P1", "SIM", confirm=True)  # the row goes with the forecast
    assert stored_rows(path) == []


# ── deleting ─────────────────────────────────────────────────────────────────


def test_deleting_a_forecast_whole_takes_its_stored_months(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "SIM", Phase.OIL, [SEGMENT])
    for prop_id, model in (("P1", "SIM"), ("P1", "ALT"), ("P2", "SIM")):
        store(path, prop_id, model, "2026-03-01", [1.0], [0.0], [0.0])

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path, user="dana") as db:
            db.delete_forecast("P1", "SIM", confirm=True)
            assert stored_rows(path) == [("P1", "ALT"), ("P2", "SIM")]
            assert db.forecasts("P1", "SIM") == []
    # A forecast that was only stored months is still a logged change.
    assert change_log(path) == [("Forecast", "P1", "SIM", 3, "dana")]

    with Database.open(path, change_log=False) as db:
        db.delete_forecast(model="SIM", confirm=True)
        assert stored_rows(path) == [("P1", "ALT")]
        assert keys(db.forecasts()) == [("P1", "ALT"), ("P1", "MAIN")]
        db.delete_forecast(confirm=True)
        assert db.forecasts() == [] and stored_rows(path) == []


def test_a_forecast_with_segments_and_stored_months_logs_one_change(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.OIL, [TAIL])
    store(path, "P1", "SIM", "2026-03-01", [1.0], [1.0], [1.0])
    with Database.open(path) as db:
        db.delete_forecast("P1", confirm=True)
    assert change_log(path) == [("Forecast", "P1", "SIM", 3, None)]


def test_deleting_one_phase_clears_its_stored_months_as_obsidian_does(tmp_path):
    """Obsidian's own delete of a phase zeroes that phase's stored months and
    keeps the others; left in place they would go on being its forecast."""
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.OIL, [TAIL])
        db.set_forecast("P1", "SIM", Phase.GAS, [TAIL])
    store(path, "P1", "SIM", "2026-03-01", [3.0, 2.0], [9.0, 8.0], [0.0, 0.0])
    store(path, "P2", "SIM", "2026-03-01", [3.0], [9.0], [0.0])

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path, user="dana") as db:
            db.delete_forecast("P1", "SIM", Phase.OIL, confirm=True)
            rows = db.forecasts("P1", "SIM")
            assert [(row.phase, row.kind) for row in rows] == [
                (Phase.GAS, ForecastKind.MONTHLY_VOLUMES),
                (Phase.GAS, ForecastKind.DECLINE),
            ]
            assert rows[0].monthly_volumes == (9.0, 8.0), "the other phase is untouched"
            assert {row.prop_id for row in db.forecasts()} == {"P1", "P2"}
    assert change_log(path) == [("Forecast", "P1", "SIM", 3, "dana")]
    conn = sqlite3.connect(path)
    oil = conn.execute("SELECT oil_bbl FROM ForecastVolumes WHERE prop_id = 'P1'").fetchone()[0]
    conn.close()
    assert oil == blob(0.0, 0.0), "zeroed in place, the same number of months"

    with Database.open(path, change_log=False) as db:
        db.delete_forecast("P1", "SIM", Phase.GAS, confirm=True)  # the last phase with months
    assert stored_rows(path) == [("P2", "SIM")], "a row with no phase left is removed"


def test_deleting_a_phase_with_no_stored_months_is_quiet(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "SIM", Phase.WATER, [TAIL])
    store(path, "P1", "SIM", "2026-03-01", [3.0], [9.0], [0.0])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.delete_forecast("P1", "SIM", Phase.WATER, confirm=True)
            db.delete_forecast("P2", confirm=True)
    assert stored_rows(path) == [("P1", "SIM")]


def test_deleting_forecasts_in_a_file_without_the_table(tmp_path):
    path = make(tmp_path, bookkeeping=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.set_forecast("P1", "SIM", Phase.OIL, [SEGMENT])
            db.delete_forecast("P1", "SIM", Phase.OIL, confirm=True)
            db.delete_forecast(confirm=True)
            assert db.forecasts() == []


# ── the well's own tables ────────────────────────────────────────────────────


def test_deleting_a_well_deletes_everything_stored_for_it(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [1.0], [1.0], [1.0])
    store(path, "P2", "SIM", "2026-03-01", [2.0], [2.0], [2.0])
    exec_sql(
        path,
        [
            "INSERT INTO DailyPressure VALUES ('P1', '2026-01-01', 900.0, 2500.0, 32.0)",
            "INSERT INTO DailyPressure VALUES ('P2', '2026-01-01', 800.0, 2400.0, 28.0)",
        ],
    )

    with Database.open(path) as db:
        db.delete_well("P1", confirm=True)
        assert {row.prop_id for row in db.forecasts()} == {"P2"}
        conn = db._ensure_open()  # pyright: ignore[reportPrivateUsage]
        assert [row[0] for row in conn.execute("SELECT prop_id FROM DailyPressure")] == ["P2"]


def test_copying_a_well_copies_its_stored_months(tmp_path):
    path = make(tmp_path)
    store(path, "P1", "SIM", "2026-03-01", [3.0, 2.0], [9.0, 8.0], [0.0, 0.0])

    with Database.open(path) as db:
        db.copy_well("P1", "P1 COPY")
        original = db.forecasts("P1")
        copy = db.forecasts("P1 COPY")

    assert [row.monthly_volumes for row in copy] == [(9.0, 8.0), (3.0, 2.0)]
    assert [(row.model, row.start, row.monthly_volumes) for row in copy] == [
        (row.model, row.start, row.monthly_volumes) for row in original
    ]
