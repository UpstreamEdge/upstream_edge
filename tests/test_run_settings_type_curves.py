"""Run settings and saved type curves."""

from __future__ import annotations

import warnings
from datetime import UTC, date, datetime

import pytest
from conftest import (
    change_log,
    create_bookkeeping_schema,
    create_obsidian_2026_02_blank_schema,
    create_settings_schema,
    exec_sql,
    last_modified,
)

from upstream_edge.obsidian_db import (
    CutoffMethod,
    Database,
    IdcMethod,
    MissingTableError,
    ModelNotFoundError,
    Phase,
    ReadOnlyDatabaseError,
    RunSettings,
    TypeCurve,
    TypeCurveFitKind,
    ValidationError,
)

SPLITS = "DRILLING:30,COMPLETIONS:30,FACILITIES:100,SURFACEWORK:100,OTHER:50"


def make(tmp_path):
    """The run settings a current Obsidian leaves in a new file."""
    path = tmp_path / "settings.obsdb"
    create_settings_schema(path)
    create_bookkeeping_schema(path)
    exec_sql(
        path,
        [
            (
                "insert into RunSettings values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "STANDARD",
                    "2026-03-01",
                    "EconLimit",
                    "MonthDelay(6)",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            ),
            (
                "insert into RunSettings values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("LOSSOK", "2026-03-01", "LossOK", "NoDelay", 0, 0.0, 0, "", SPLITS),
            ),
            (
                "insert into RunSettings values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "STANDARD-AFIT",
                    "2026-03-01",
                    "EconLimit",
                    "MonthDelay(6)",
                    1,
                    21.0,
                    7,
                    "ExpenseImmediately",
                    SPLITS,
                ),
            ),
        ],
    )
    return path


def settings_row(path, *values):
    exec_sql(path, [("insert into RunSettings values (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)])


# ── run settings, read ───────────────────────────────────────────────────────


def test_the_presets_read_as_obsidian_writes_them(tmp_path):
    path = make(tmp_path)
    with Database.open(path, read_only=True) as db:
        assert db.run_settings() == [
            RunSettings(
                name="LOSSOK",
                effective_date=date(2026, 3, 1),
                cutoff_method=CutoffMethod.LOSS_OK,
                cutoff_delay_months=0,
                abandonment_delay_months=0,
                after_tax=False,
                federal_tax_rate_pct=None,
                tangible_recovery_years=None,
                idc_method=None,
            ),
            RunSettings(
                name="STANDARD",
                effective_date=date(2026, 3, 1),
                cutoff_method=CutoffMethod.ECON_LIMIT,
                cutoff_delay_months=0,
                abandonment_delay_months=6,
                after_tax=False,
                federal_tax_rate_pct=None,
                tangible_recovery_years=None,
                idc_method=None,
            ),
            RunSettings(
                name="STANDARD-AFIT",
                effective_date=date(2026, 3, 1),
                cutoff_method=CutoffMethod.ECON_LIMIT,
                cutoff_delay_months=0,
                abandonment_delay_months=6,
                after_tax=True,
                federal_tax_rate_pct=21.0,
                tangible_recovery_years=7,
                idc_method=IdcMethod.EXPENSE_IMMEDIATELY,
            ),
        ]
        assert [s.name for s in db.run_settings("STANDARD")] == ["STANDARD"]
        assert db.run_settings("NOPE") == []


@pytest.mark.parametrize(
    ("stored", "method", "delay"),
    [
        ("EconLimit", CutoffMethod.ECON_LIMIT, 0),
        ("econlimit", CutoffMethod.ECON_LIMIT, 0),
        ("LastPositive", CutoffMethod.LAST_POSITIVE, 0),
        ("LossOK", CutoffMethod.LOSS_OK, 0),
        ("LOSSOK", CutoffMethod.LOSS_OK, 0),
        ("EconLimitDelayMo(18)", CutoffMethod.ECON_LIMIT_DELAY, 18),
        ("EconLimitDelayMo()", CutoffMethod.ECON_LIMIT_DELAY, 0),
        # What Obsidian falls back to for text it does not know.
        ("SomethingNewer", CutoffMethod.ECON_LIMIT, 0),
        ("", CutoffMethod.ECON_LIMIT, 0),
        (None, CutoffMethod.ECON_LIMIT, 0),
    ],
)
def test_a_cutoff_reads_as_obsidian_reads_it(tmp_path, stored, method, delay):
    path = make(tmp_path)
    settings_row(path, "CUSTOM", "2026-03-01", stored, "NoDelay", 0, 0.0, 0, "", "")
    with Database.open(path) as db:
        (custom,) = db.run_settings("CUSTOM")
        assert (custom.cutoff_method, custom.cutoff_delay_months) == (method, delay)


@pytest.mark.parametrize(
    ("stored", "months"),
    [
        ("NoDelay", 0),
        ("MonthDelay(6)", 6),
        ("MonthDelay(0)", 0),
        ("YearDelay(2)", 24),
        ("Unknown", 0),
        ("", 0),
        (None, 0),
    ],
)
def test_an_abandonment_delay_reads_in_months(tmp_path, stored, months):
    path = make(tmp_path)
    settings_row(path, "CUSTOM", "2026-03-01", "EconLimit", stored, 0, 0.0, 0, "", "")
    with Database.open(path) as db:
        assert db.run_settings("CUSTOM")[0].abandonment_delay_months == months


@pytest.mark.parametrize(
    ("rate", "years", "idc", "expected"),
    [
        (35.0, 5, "Amortize60Months", (35.0, 5, IdcMethod.AMORTIZE_60_MONTHS)),
        (21.0, 7, "amortize60months", (21.0, 7, IdcMethod.AMORTIZE_60_MONTHS)),
        # Obsidian's fallbacks: a 21% rate, seven years, intangibles expensed.
        (140.0, None, "Newer", (21.0, 7, IdcMethod.EXPENSE_IMMEDIATELY)),
        (None, "", None, (21.0, 7, IdcMethod.EXPENSE_IMMEDIATELY)),
    ],
)
def test_the_after_tax_choices(tmp_path, rate, years, idc, expected):
    path = make(tmp_path)
    settings_row(path, "TAXED", "2026-03-01", "EconLimit", "NoDelay", 1, rate, years, idc, "")
    with Database.open(path) as db:
        (taxed,) = db.run_settings("TAXED")
        assert taxed.after_tax is True
        assert (
            taxed.federal_tax_rate_pct,
            taxed.tangible_recovery_years,
            taxed.idc_method,
        ) == expected


def test_run_settings_in_a_file_from_before_the_after_tax_columns(tmp_path):
    path = tmp_path / "old.obsdb"
    exec_sql(
        path,
        [
            "create table RunSettings (settings_name text, effective_date date, "
            "cutoff_method text, abandon_timing text, revenue_timing text)",
            "insert into RunSettings values ('STANDARD', '2024-07-15', 'EconLimit', "
            "'MonthDelay(6)', 'x')",
        ],
    )
    with Database.open(path) as db:
        (standard,) = db.run_settings()
        assert standard.effective_date == date(2024, 7, 1), "the month's first day"
        assert standard.after_tax is False and standard.idc_method is None
        assert standard.abandonment_delay_months == 6


def test_run_settings_need_their_table(tmp_path):
    path = tmp_path / "bare.obsdb"
    exec_sql(path, ["create table Main (prop_id text)"])
    with Database.open(path) as db:
        with pytest.raises(MissingTableError) as error:
            db.run_settings()
        assert error.value.table == "RunSettings"
        with pytest.raises(MissingTableError):
            db.type_curves()


def test_run_settings_as_a_dataframe(tmp_path):
    pytest.importorskip("pandas")
    path = make(tmp_path)
    with Database.open(path) as db:
        frame = db.run_settings_df("STANDARD-AFIT")
    assert list(frame.columns) == [
        "name",
        "effective_date",
        "cutoff_method",
        "cutoff_delay_months",
        "abandonment_delay_months",
        "after_tax",
        "federal_tax_rate_pct",
        "tangible_recovery_years",
        "idc_method",
    ]
    assert frame.iloc[0]["cutoff_method"] == "EconLimit"
    assert frame.iloc[0]["idc_method"] == "ExpenseImmediately"


# ── run settings, written ────────────────────────────────────────────────────


def test_the_effective_date_moves_and_nothing_else_does(tmp_path):
    path = make(tmp_path)
    with Database.open(path, user="dana") as db:
        before = {s.name: s for s in db.run_settings()}
        db.set_run_settings("STANDARD-AFIT", effective_date=date(2026, 7, 1))
        after = {s.name: s for s in db.run_settings()}
    assert after["STANDARD-AFIT"].effective_date == date(2026, 7, 1)
    assert after["STANDARD"] == before["STANDARD"] and after["LOSSOK"] == before["LOSSOK"]
    # Every other column of the row is as Obsidian wrote it.
    import sqlite3

    conn = sqlite3.connect(path)
    row = conn.execute("select * from RunSettings where settings_name = 'STANDARD-AFIT'").fetchone()
    conn.close()
    assert row == (
        "STANDARD-AFIT",
        "2026-07-01",
        "EconLimit",
        "MonthDelay(6)",
        1,
        21.0,
        7,
        "ExpenseImmediately",
        SPLITS,
    )
    assert change_log(path) == [("RunSettings", None, "STANDARD-AFIT", 3, "dana")]
    assert len(last_modified(path)) == 1


def test_a_date_that_does_not_move_logs_nothing(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.set_run_settings("STANDARD", effective_date=date(2026, 3, 1))
    assert change_log(path) == []


def test_set_run_settings_refusals(tmp_path):
    path = make(tmp_path)
    before = path.read_bytes()
    with Database.open(path) as db:
        with pytest.raises(ModelNotFoundError, match="run settings not found") as error:
            db.set_run_settings("NOPE", effective_date=date(2026, 7, 1))
        assert (error.value.model_kind, error.value.name) == ("RunSettings", "NOPE")
        with pytest.raises(ValidationError, match="first day of the month"):
            db.set_run_settings("STANDARD", effective_date=date(2026, 7, 15))
        with pytest.raises(ValidationError, match="at least one field"):
            db.set_run_settings("STANDARD")
    with Database.open(path, read_only=True) as db, pytest.raises(ReadOnlyDatabaseError):
        db.set_run_settings("STANDARD", effective_date=date(2026, 7, 1))
    assert path.read_bytes() == before


# ── type curves ──────────────────────────────────────────────────────────────


def curve_row(path, *values):
    exec_sql(
        path,
        [("insert into TypeCurve values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)],
    )


def test_a_saved_type_curve_reads_under_the_api_names(tmp_path):
    path = make(tmp_path)
    curve_row(
        path,
        "ACREAGE P50",
        "OIL",
        "LateralLength(10000)",
        120.0,
        1,
        900.0,
        2,
        0.5,
        0.7,
        1.1,
        0.06,
        "4212300001, 4212300002,",
        "P50|AUTO|MAIN",
        "dana@example.com",
        1790000000,
    )
    curve_row(
        path, "ACREAGE P50", "GAS", "LateralLength(10000)", 0.0, 0, 2500.0, 0, 0.0, 0.6, 0.9,
        0.05, "", "P50|CUSTOM|", None, None,
    )  # fmt: skip
    with Database.open(path, read_only=True) as db:
        gas, oil = db.type_curves("ACREAGE P50")
        assert oil == TypeCurve(
            name="ACREAGE P50",
            phase=Phase.OIL,
            normalization_lateral_length_ft=10000.0,
            rate_start=120.0,
            build_up_months=1,
            rate_init=900.0,
            bridge_months=2,
            decline_bridge=0.5,
            decline_init=0.7,
            b_factor=1.1,
            decline_min=0.06,
            analog_apis=("4212300001", "4212300002"),
            fit_pxx=50,
            fit_kind=TypeCurveFitKind.AUTO,
            fit_forecasts_from="MAIN",
            saved_by="dana@example.com",
            saved_at=datetime.fromtimestamp(1790000000, tz=UTC),
        )
        assert gas.phase is Phase.GAS and gas.analog_apis == ()
        assert (gas.fit_pxx, gas.fit_kind, gas.fit_forecasts_from) == (
            50,
            TypeCurveFitKind.CUSTOM,
            None,
        ), "fitted to production alone"
        assert gas.saved_by is None and gas.saved_at is None
        assert db.type_curves("NOPE") == []
        assert len(db.type_curves()) == 2


@pytest.mark.parametrize(
    ("stored", "length"),
    [
        ("None", None),
        ("", None),
        (None, None),
        ("LateralLength(7500)", 7500.0),
        ("LateralLength(7500.5)", 7500.5),
        ("laterallength(5000)", 5000.0),
        ("LateralLength(x)", None),
        ("SomethingNewer(3)", None),
    ],
)
def test_a_normalization(tmp_path, stored, length):
    path = make(tmp_path)
    curve_row(path, "TC", "OIL", stored, 1.0, 0, 1.0, 0, 0.0, 0.5, 1.0, 0.06, "", None, None, None)
    with Database.open(path) as db:
        assert db.type_curves()[0].normalization_lateral_length_ft == length


@pytest.mark.parametrize(
    ("stored", "fit"),
    [
        ("P65|AUTO|MAIN", (65, TypeCurveFitKind.AUTO, "MAIN")),
        ("p65|custom|A|B", (65, TypeCurveFitKind.CUSTOM, "A|B")),
        ("P0|AUTO", (0, TypeCurveFitKind.AUTO, None)),
        ("P100|AUTO|  ", (100, TypeCurveFitKind.AUTO, None)),
        # Anything Obsidian did not write is "not recorded", never an error.
        ("P101|AUTO|MAIN", (None, None, None)),
        ("65|AUTO|MAIN", (None, None, None)),
        ("P65|NEWER|MAIN", (None, None, None)),
        ("P65", (None, None, None)),
        ("", (None, None, None)),
        (None, (None, None, None)),
        (17, (None, None, None)),
    ],
)
def test_how_a_curve_was_fitted(tmp_path, stored, fit):
    path = make(tmp_path)
    curve_row(path, "TC", "OIL", "None", 1.0, 0, 1.0, 0, 0.0, 0.5, 1.0, 0.06, "", stored, "", "x")
    with Database.open(path) as db:
        (curve,) = db.type_curves()
        assert (curve.fit_pxx, curve.fit_kind, curve.fit_forecasts_from) == fit
        assert curve.saved_by is None and curve.saved_at is None


def test_odd_numbers_in_a_curve_read_as_obsidian_reads_them(tmp_path):
    """Month counts rounded half away from zero and held to 0 through 100;
    declines and b held to the panel's range; text and NULL as zero."""
    path = make(tmp_path)
    curve_row(
        path, "ODD", "OIL", "None", 5, 2.5, 7.0, -3, None, "0.7", 9.0, -0.2, None, None, None, None
    )
    curve_row(path, "ODD", "GAS", "None", 1.0, 0.5, 1.0, 150, 1.5, -2.0, 7, 0.4, "", None, " ", -5)
    curve_row(
        path, "ODD", "WATER", "None", 1.0, "12", 1.0, b"x", 0.5, 0.5, 1.0, 0.06, "", "", "", 0
    )
    with Database.open(path) as db:
        gas, oil, water = db.type_curves()
    assert (oil.rate_start, oil.rate_init) == (5.0, 7.0)
    assert (oil.build_up_months, oil.bridge_months) == (3, 0), "2.5 rounds up; never negative"
    # A missing decline is zero; one out of range is held to the range.
    assert (oil.decline_bridge, oil.decline_init, oil.b_factor, oil.decline_min) == (
        0.0,
        0.7,
        5.0,
        0.0,
    )
    assert (gas.build_up_months, gas.bridge_months) == (1, 100), "0.5 rounds up; capped at 100"
    assert (gas.decline_bridge, gas.decline_init, gas.b_factor) == (0.999, -0.999, 5.0)
    assert gas.saved_by is None and gas.saved_at is None, "blank text and a negative time"
    assert (water.build_up_months, water.bridge_months) == (12, 0), "numeric text; a blob"


@pytest.mark.parametrize(
    ("phase", "start_rate", "initial_rate"),
    [
        ("oil", 1.0, 1.0),
        ("Gas ", 1.0, 1.0),
        ("NGL", 1.0, 1.0),
        ("OIL", None, 1.0),
        ("OIL", 1.0, "abc"),
    ],
)
def test_a_curve_row_obsidian_does_not_load_is_left_out(
    tmp_path, caplog, phase, start_rate, initial_rate
):
    """Obsidian matches the phase exactly and needs both rates to be numbers;
    it skips any other row. One such row must not fail or pollute the rest."""
    path = make(tmp_path)
    curve_row(
        path,
        "BAD",
        phase,
        "None",
        start_rate,
        0,
        initial_rate,
        0,
        0.0,
        0.5,
        1.0,
        0.06,
        "",
        None,
        None,
        None,
    )
    curve_row(
        path, "GOOD", "WATER", "None", 1.0, 0, 2.0, 0, 0.0, 0.5, 1.0, 0.06, "", None, None, None
    )
    with caplog.at_level("WARNING", logger="upstream_edge.obsidian_db"):
        with Database.open(path) as db:
            assert [curve.name for curve in db.type_curves()] == ["GOOD"]
    assert "skipped" in caplog.text


@pytest.mark.parametrize(
    ("stored", "length"),
    [
        ("LateralLength( 10000 )", None),
        ("LateralLength(10_000)", None),
        ("LateralLength(inf)", None),
    ],
)
def test_a_normalization_obsidian_cannot_read_is_not_one(tmp_path, stored, length):
    path = make(tmp_path)
    curve_row(path, "TC", "OIL", stored, 1.0, 0, 1.0, 0, 0.0, 0.5, 1.0, 0.06, "", None, None, None)
    with Database.open(path) as db:
        assert db.type_curves()[0].normalization_lateral_length_ft == length


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("2026-07-01 00:00:00", date(2026, 7, 1)),
        ("2026-07-15", date(2026, 7, 1)),
        # Obsidian's fallback for a date it cannot read; the row still loads.
        ("July 2026", date(2024, 7, 1)),
        ("", date(2024, 7, 1)),
        (None, date(2024, 7, 1)),
    ],
)
def test_an_effective_date_reads_as_obsidian_reads_it(tmp_path, stored, expected):
    path = make(tmp_path)
    settings_row(path, "CUSTOM", stored, "EconLimit", "NoDelay", 0, 0.0, 0, "", "")
    with Database.open(path) as db:
        assert db.run_settings("CUSTOM")[0].effective_date == expected
        assert len(db.run_settings()) == 4


def test_the_presets_read_as_obsidian_holds_them_whatever_the_rows_say(tmp_path):
    path = make(tmp_path)
    exec_sql(
        path,
        [
            "UPDATE RunSettings SET cutoff_method = 'LossOK', abandon_timing = 'NoDelay' "
            "WHERE settings_name = 'STANDARD'",
            "UPDATE RunSettings SET cutoff_method = 'EconLimitDelayMo(9)', "
            "abandon_timing = 'YearDelay(2)' WHERE settings_name = 'LOSSOK'",
            "UPDATE RunSettings SET afit_enabled = 0, fed_corp_tax_rate = 35.0, "
            "tangible_recovery_period_yrs = 5, idc_method = 'Amortize60Months', "
            "cutoff_method = 'LastPositive' WHERE settings_name = 'STANDARD-AFIT'",
        ],
    )
    with Database.open(path) as db:
        by_name = {s.name: s for s in db.run_settings()}
    standard, lossok, afit = by_name["STANDARD"], by_name["LOSSOK"], by_name["STANDARD-AFIT"]
    assert (standard.cutoff_method, standard.abandonment_delay_months) == (
        CutoffMethod.ECON_LIMIT,
        6,
    )
    assert (lossok.cutoff_method, lossok.cutoff_delay_months, lossok.abandonment_delay_months) == (
        CutoffMethod.LOSS_OK,
        0,
        0,
    )
    assert (afit.cutoff_method, afit.after_tax, afit.federal_tax_rate_pct) == (
        CutoffMethod.ECON_LIMIT,
        True,
        21.0,
    )
    assert (afit.tangible_recovery_years, afit.idc_method) == (7, IdcMethod.EXPENSE_IMMEDIATELY)


@pytest.mark.parametrize(
    ("flag", "years", "cutoff", "expected"),
    [
        ("true", 5, "EconLimit", (False, None, 0)),  # Obsidian reads the flag as an integer
        (1, 7.5, "EconLimit", (True, 7, 0)),  # and the years as a whole number
        (1, -3, "EconLimit", (True, 7, 0)),
        (1, 300, "EconLimit", (True, 7, 0)),
        (1, 12, "EconLimitDelayMo(²)", (True, 12, 0)),  # a superscript two is no digit
        (1, 12, "EconLimitDelayMo(99999999999)", (True, 12, 0)),  # past a 32-bit count
    ],
)
def test_run_settings_values_obsidian_does_not_take(tmp_path, flag, years, cutoff, expected):
    path = make(tmp_path)
    settings_row(path, "CUSTOM", "2026-03-01", cutoff, "MonthDelay(³)", flag, 30.0, years, "", "")
    with Database.open(path) as db:
        (custom,) = db.run_settings("CUSTOM")
    assert (
        custom.after_tax,
        custom.tangible_recovery_years,
        custom.cutoff_delay_months,
    ) == expected
    assert custom.abandonment_delay_months == 0


def test_type_curves_in_a_file_from_before_the_fit_columns(tmp_path):
    path = tmp_path / "blank_2026_02.obsdb"
    create_obsidian_2026_02_blank_schema(path)
    exec_sql(
        path,
        [
            (
                "insert into TypeCurve values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("TC", "WATER", "None", 10.0, 0, 300.0, 0, 0.0, 0.4, 0.8, 0.05, "4212300001"),
            )
        ],
    )
    with Database.open(path) as db:
        (curve,) = db.type_curves()
        assert curve.phase is Phase.WATER and curve.analog_apis == ("4212300001",)
        assert (curve.fit_pxx, curve.saved_by, curve.saved_at) == (None, None, None)
        assert db.run_settings() == []


def test_type_curves_as_a_dataframe(tmp_path):
    pytest.importorskip("pandas")
    path = make(tmp_path)
    curve_row(
        path, "TC", "OIL", "None", 1.0, 0, 1.0, 0, 0.0, 0.5, 1.0, 0.06, "", "P50|AUTO|MAIN", None,
        None,
    )  # fmt: skip
    with Database.open(path) as db:
        frame = db.type_curves_df()
    assert list(frame.columns)[:4] == [
        "name",
        "phase",
        "normalization_lateral_length_ft",
        "rate_start",
    ]
    assert frame.iloc[0]["phase"] == "OIL" and frame.iloc[0]["fit_kind"] == "auto"


def test_reading_settings_and_curves_never_warns_or_writes(tmp_path):
    path = make(tmp_path)
    before = path.read_bytes()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Database.open(path) as db:
            db.run_settings()
            db.type_curves()
            db.model_field_refs()
    assert path.read_bytes() == before
