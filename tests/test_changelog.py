"""The ChangeLog rows the library writes: Obsidian's own rules."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime

import pytest
from conftest import (
    change_log,
    create_bookkeeping_schema,
    create_core_schema,
    create_scenario_schema,
    exec_sql,
    last_modified,
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
    ForecastSegment,
    InterestSegment,
    MonthlyRow,
    Phase,
    PriceModelSegment,
    RsvCat,
    SurveyPointInput,
    TaxModelKind,
    TaxModelSegment,
)

SEGMENT = ForecastSegment(
    start=date(2026, 1, 1), rate_init=500.0, decline_init=0.6, b_factor=1.1, decline_min=0.06
)
EXTERNAL = 3
LOE = ExpenseModelSegment(
    kind=ExpenseModelKind.SIMPLE,
    fixed_monthly=1000.0,
    variable_oil=2.0,
    variable_gas=0.3,
    variable_water=1.0,
)


def make(tmp_path, *, wells=("P1", "P2"), models=False):
    """A file with the core tables, the bookkeeping tables and a few wells."""
    path = tmp_path / "log.obsdb"
    create_scenario_schema(path)
    if models:
        seed_models(path)
    create_bookkeeping_schema(path)
    with Database.open(path, change_log=False) as db:
        for well in wells:
            db.add_well(well, rsv_cat=RsvCat.PDP)
    exec_sql(path, ["DELETE FROM Metadata"])
    return path


def keys(path):
    return [(table, well, detail) for table, well, detail, _source, _user in change_log(path)]


# ── the forecast: one row per well and model ─────────────────────────────────


def test_a_forecast_save_is_one_row_with_the_external_source(tmp_path):
    path = make(tmp_path)
    before = int(time.time())
    with Database.open(path, user="engineer@example.com") as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert change_log(path) == [("Forecast", "P1", "MAIN", EXTERNAL, "engineer@example.com")]
    conn = sqlite3.connect(path)
    ts = conn.execute("SELECT ts FROM ChangeLog").fetchone()[0]
    conn.close()
    assert before <= ts <= int(time.time())


def test_three_phases_in_one_transaction_are_one_row(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db, db.transaction():
        for phase in (Phase.OIL, Phase.GAS, Phase.WATER):
            db.set_forecast("P1", "MAIN", phase, [SEGMENT])

    assert keys(path) == [("Forecast", "P1", "MAIN")]


def test_a_repeat_within_thirty_seconds_adds_no_row(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        for phase in (Phase.OIL, Phase.GAS, Phase.WATER):
            db.set_forecast("P1", "MAIN", phase, [SEGMENT])

    assert keys(path) == [("Forecast", "P1", "MAIN")]


def test_a_repeat_after_thirty_seconds_adds_a_row(tmp_path, monkeypatch):
    path = make(tmp_path)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        clock[0] += 29
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        assert len(keys(path)) == 1
        clock[0] += 2  # 31 s after the row that was written, not after the last attempt
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert keys(path) == [("Forecast", "P1", "MAIN"), ("Forecast", "P1", "MAIN")]


def test_a_clock_that_went_backwards_writes_a_row(tmp_path, monkeypatch):
    path = make(tmp_path)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        clock[0] -= 3600
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert len(keys(path)) == 2


def test_other_wells_and_other_models_are_their_own_rows(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P1", "ALT", Phase.OIL, [SEGMENT])

    assert keys(path) == [
        ("Forecast", "P1", "MAIN"),
        ("Forecast", "P2", "MAIN"),
        ("Forecast", "P1", "ALT"),
    ]


def test_a_new_connection_logs_again(tmp_path):
    """The thirty seconds are this connection's memory, as they are the app's."""
    path = make(tmp_path)
    for _ in range(2):
        with Database.open(path) as db:
            db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert len(keys(path)) == 2


def test_a_forecast_delete_logs_only_what_had_rows(tmp_path):
    path = make(tmp_path, wells=("P1", "P2", "P3"))
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P1", "ALT", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.GAS, [SEGMENT])

    with Database.open(path) as db:
        db.delete_forecast("P3", confirm=True)  # nothing there
        assert keys(path) == []
        db.delete_forecast(model="MAIN", confirm=True)

    assert keys(path) == [("Forecast", "P1", "MAIN"), ("Forecast", "P2", "MAIN")]
    with Database.open(path) as db:
        assert [row.model for row in db.forecasts()] == ["ALT"]


# ── who ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("user", [None, "", "   "])
def test_no_user_writes_no_name(tmp_path, user):
    path = make(tmp_path)
    with Database.open(path, user=user) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert change_log(path) == [("Forecast", "P1", "MAIN", EXTERNAL, None)]


def test_the_user_is_trimmed(tmp_path):
    path = make(tmp_path)
    with Database.open(path, user="  nightly load  ") as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert change_log(path)[0][4] == "nightly load"


# ── the switch ───────────────────────────────────────────────────────────────


def test_change_log_off_writes_no_row_and_still_stamps_the_file(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        assert len(db.forecasts("P1")) == 1

    assert change_log(path) == []
    assert len(last_modified(path)) == 1


# ── commit and rollback ──────────────────────────────────────────────────────


def test_a_rollback_leaves_no_row_and_no_stamp(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        with pytest.raises(ValueError, match="boom"), db.transaction():
            db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
            raise ValueError("boom")
        assert db.forecasts("P1") == []
        assert change_log(path) == []
        assert last_modified(path) == []

        # The attempt that rolled back is not remembered as a row on disk.
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert keys(path) == [("Forecast", "P1", "MAIN")]


class _CommitFails:
    """A connection whose next COMMIT fails the way a full disk fails it."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
        if sql == "COMMIT":
            self._conn.execute("ROLLBACK")
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def test_a_commit_that_fails_is_not_remembered_as_a_row_on_disk(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        real = db._conn  # pyright: ignore[reportPrivateUsage]
        assert real is not None
        db._conn = _CommitFails(real)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db._conn = real  # pyright: ignore[reportPrivateUsage]
        assert change_log(path) == []
        assert db.forecasts("P1") == []

        # Inside the thirty seconds: the row the failed commit took with it
        # must not fold this one away.
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])

    assert keys(path) == [("Forecast", "P1", "MAIN")]


def test_a_failed_writer_logs_nothing_of_the_writers_before_it(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        with pytest.raises(Exception, match="well not found"), db.transaction():
            db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
            db.set_forecast("NOPE", "MAIN", Phase.OIL, [SEGMENT])
        # The rows of the rolled-back transaction do not ride along with the next one.
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])

    assert keys(path) == [("Forecast", "P2", "MAIN")]


def test_a_transaction_that_only_reads_changes_nothing(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db, db.transaction():
        assert len(db.wells()) == 2

    assert change_log(path) == []
    assert last_modified(path) == []


def test_reading_changes_nothing(tmp_path):
    path = make(tmp_path)
    before = path.read_bytes()
    with Database.open(path) as db:
        db.wells()
        db.forecasts()

    assert path.read_bytes() == before


# ── the header: the fields that changed, by column name ──────────────────────


def test_a_header_edit_logs_each_changed_field_by_its_column(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.set_well_header("P1", lease="SMITH", tvd_ft=9800.0, lateral_length_ft=10000.0)

    assert keys(path) == [
        ("Main", "P1", "lease"),
        ("Main", "P1", "tvd"),
        ("Main", "P1", "lateral_length"),
    ]


def test_a_header_value_that_does_not_change_logs_nothing(tmp_path):
    path = make(tmp_path)
    with Database.open(path, change_log=False) as db:
        db.set_well_header("P1", lease="SMITH", tvd_ft=9800.0, spud=date(2024, 3, 1))

    with Database.open(path) as db:
        db.set_well_header("P1", lease="SMITH", tvd_ft=9800, spud=date(2024, 3, 1), field=None)
        assert keys(path) == []
        db.set_well_header("P1", lease="SMITH", operator="ACME", rsv_cat=RsvCat.PDNP)

    assert keys(path) == [("Main", "P1", "operator"), ("Main", "P1", "rsv_cat")]


def test_clearing_an_empty_text_field_logs_nothing(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["UPDATE Main SET lease = '' WHERE prop_id = 'P1'"])
    with Database.open(path) as db:
        db.set_well_header("P1", lease=None)

    assert keys(path) == []


# ── the named models: the model's name and no well ───────────────────────────


def test_named_models_log_their_name_without_a_well(tmp_path):
    path = make(tmp_path, models=True)
    with Database.open(path) as db:
        db.set_price_model(
            "DECK", [PriceModelSegment(start_date=date(2026, 1, 1), oil=70.0, gas=3.0, ngl=20.0)]
        )
        db.set_expense_model("LOE", [LOE])
        db.set_tax_model(
            "TX",
            [
                TaxModelSegment(
                    kind=TaxModelKind.SIMPLE,
                    sev_tax_oil=0.046,
                    sev_tax_gas=0.075,
                    sev_tax_ngl=0.075,
                    ad_valorum_tax=0.02,
                )
            ],
        )
        db.set_diff_model(
            "BASIS",
            [
                DiffModelSegment(
                    start_date=date(2026, 1, 1),
                    oil_method=DiffType.DOLLAR,
                    oil_diff=-2.0,
                    gas_method=DiffType.FRACTION,
                    gas_diff=0.9,
                    ngl_method=DiffType.FRACTION,
                    ngl_diff=0.3,
                )
            ],
        )
        db.set_shrink_yield_model("SHRINK", gas_shrink_frac=0.1, ngl_yield_bbl_mmscf=40.0)

    assert keys(path) == [
        ("PriceModel", None, "DECK"),
        ("ExpenseModel", None, "LOE"),
        ("TaxModel", None, "TX"),
        ("DiffModel", None, "BASIS"),
        ("ShrinkYieldModel", None, "SHRINK"),
    ]


def test_deleting_a_model_logs_it_and_deleting_nothing_logs_nothing(tmp_path):
    path = make(tmp_path, models=True)
    with Database.open(path) as db:
        db.delete_price_model("NO SUCH DECK", confirm=True)
        db.delete_expense_model("NO SUCH", confirm=True)
        db.delete_tax_model("NO SUCH", confirm=True)
        db.delete_diff_model("NO SUCH", confirm=True)
        db.delete_shrink_yield_model("NO SUCH", confirm=True)
        assert keys(path) == []
        db.delete_price_model("STRIP", confirm=True)
        db.delete_expense_model("OPEX", confirm=True)
        db.delete_tax_model("TAX", confirm=True)
        db.delete_diff_model("DIFF", confirm=True)
        db.delete_shrink_yield_model("SY", confirm=True)

    assert keys(path) == [
        ("PriceModel", None, "STRIP"),
        ("ExpenseModel", None, "OPEX"),
        ("TaxModel", None, "TAX"),
        ("DiffModel", None, "DIFF"),
        ("ShrinkYieldModel", None, "SY"),
    ]


# ── assignments, interests, capital ──────────────────────────────────────────


def test_assignments_log_the_well_and_the_scenario(tmp_path):
    path = make(tmp_path, models=True)
    with Database.open(path) as db:
        db.set_well_models(["P1", "P2"], "MAIN", exp_model="OPEX", tax_model="TAX")

    assert keys(path) == [("WellModels", "P1", "MAIN"), ("WellModels", "P2", "MAIN")]


def test_an_interest_save_logs_each_well_and_the_wells_a_new_model_fills(tmp_path):
    path = make(tmp_path, wells=("P1", "P2", "P3"))
    segments = [InterestSegment(start=date(2026, 1, 1), wi_pct=50.0, nri_pct=40.0)]
    with Database.open(path) as db:
        db.set_interest("P1", "MAIN", segments)  # the model exists: one well
        assert keys(path) == [("Interest", "P1", "MAIN")]
        db.set_interest("P2", "FARMOUT", segments)  # a new model: every well gets a row

    assert keys(path)[1:] == [
        ("Interest", "P2", "FARMOUT"),
        ("Interest", "P1", "FARMOUT"),
        ("Interest", "P3", "FARMOUT"),
    ]


def test_an_interest_delete_logs_what_it_removed(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.delete_interest("P1", "NO SUCH MODEL", confirm=True)
        assert keys(path) == []
        db.delete_interest(["P1", "P2"], "MAIN", confirm=True)

    assert keys(path) == [("Interest", "P1", "MAIN"), ("Interest", "P2", "MAIN")]


def test_capital_and_abandonment_log_under_the_capital_model(tmp_path):
    path = make(tmp_path)
    item = CapexItem(date=date(2026, 6, 1), job_type=CapexJobType.DRILLING, cost_gross=4_000_000.0)
    with Database.open(path) as db, db.transaction():
        db.set_capex("P1", "MAIN", [item])
        db.set_abandonment("P1", "MAIN", 150_000.0)
        db.set_abandonment("P2", "MAIN", 90_000.0)

    assert keys(path) == [("Capex", "P1", "MAIN"), ("Capex", "P2", "MAIN")]


def test_a_capex_delete_logs_what_it_removed(tmp_path):
    path = make(tmp_path)
    item = CapexItem(date=date(2026, 6, 1), job_type=CapexJobType.DRILLING, cost_gross=4_000_000.0)
    with Database.open(path, change_log=False) as db:
        db.set_capex("P1", "MAIN", [item])

    with Database.open(path) as db:
        db.delete_capex("P2", confirm=True)
        assert keys(path) == []
        db.delete_capex(confirm=True)

    assert keys(path) == [("Capex", "P1", "MAIN")]


# ── what Obsidian does not log ───────────────────────────────────────────────


def test_production_surveys_and_attributes_log_nothing_but_stamp_the_file(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["CREATE UNIQUE INDEX idx_Monthly ON Monthly (prop_id, month)"])
    with Database.open(path) as db:
        db.set_monthly_prod(
            [
                MonthlyRow(
                    prop_id="P1", month=date(2026, 1, 1), oil_bbl=1.0, gas_mscf=2.0, water_bbl=0.0
                )
            ]
        )
        db.set_surveys(
            "P1",
            [
                SurveyPointInput(
                    point_md=0.0,
                    point_tvd=0.0,
                    azimuth_angle=0.0,
                    inclination_angle=0.0,
                    deviation_ns=0.0,
                    deviation_ew=0.0,
                )
            ],
        )
        db.add_well_attribute_column("Basin", AttributeType.TEXT)
        db.set_well_attribute("P1", "Basin", "Permian")

    assert change_log(path) == []
    assert len(last_modified(path)) == 1


def test_scenarios_log_nothing(tmp_path):
    path = make(tmp_path, models=True)
    with Database.open(path) as db:
        db.create_scenario("HIGH", copy_from="MAIN")
        db.set_scenario("HIGH", price_model="STRIP")
        db.delete_scenario("HIGH", confirm=True)

    assert change_log(path) == []


# ── wells added, copied, deleted ─────────────────────────────────────────────


def test_a_new_well_logs_nothing_as_obsidian_logs_nothing_for_its_defaults(tmp_path):
    """Obsidian logs a new well only for the forecast it writes one. The
    interests, capital and assignments a well starts with are not edits."""
    path = make(tmp_path, wells=())
    with Database.open(path) as db:
        db.add_well("NEW", rsv_cat=RsvCat.PUD)

    assert keys(path) == []
    assert len(last_modified(path)) == 1


def test_a_copied_well_logs_the_forecasts_it_brought(tmp_path):
    path = make(tmp_path, wells=("P1",))
    with Database.open(path, change_log=False) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P1", "ALT", Phase.GAS, [SEGMENT])
    exec_sql(
        path,
        [
            "INSERT INTO ForecastVolumes VALUES ('P1', 'SIM', '2026-01-01', x'0000803f', x'0000803f', x'0000803f', '', '', '')"
        ],
    )

    with Database.open(path) as db:
        db.copy_well("P1", "P1 COPY")

    assert keys(path) == [
        ("Forecast", "P1 COPY", "ALT"),
        ("Forecast", "P1 COPY", "MAIN"),
        ("Forecast", "P1 COPY", "SIM"),
    ]


def test_logging_starts_once_the_file_has_the_table(tmp_path):
    """A file from before the log gets its table the first time Obsidian opens
    it. A script already holding the file open logs from then on."""
    path = tmp_path / "old.obsdb"
    create_core_schema(path)
    with Database.open(path) as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])  # no table: written, not logged
        create_bookkeeping_schema(path)  # Obsidian opens the file
        db.set_forecast("P1", "MAIN", Phase.GAS, [SEGMENT])

    assert keys(path) == [("Forecast", "P1", "MAIN")]


def test_a_repeat_is_not_folded_into_a_row_someone_else_has_since_followed(tmp_path):
    """Bob writes, Obsidian's user saves the same forecast, Bob writes again
    inside thirty seconds. Bob's second write is the newest change and must
    show as such."""
    path = make(tmp_path)
    with Database.open(path, user="bob") as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        exec_sql(
            path,
            [
                "INSERT INTO ChangeLog (ts, table_name, prop_id, detail, source, user) "
                "VALUES (strftime('%s', 'now'), 'Forecast', 'P1', 'MAIN', 0, 'alice')"
            ],
        )
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P1", "MAIN", Phase.GAS, [SEGMENT])  # folds into Bob's own newest row

    assert [row[4] for row in change_log(path)] == ["bob", "alice", "bob"]


def test_deleting_a_well_removes_its_rows_and_no_one_elses(tmp_path):
    path = make(tmp_path, models=True)
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])
        db.set_expense_model("LOE", [LOE])
        db.delete_well("P1", confirm=True)

    assert keys(path) == [("Forecast", "P2", "MAIN"), ("ExpenseModel", None, "LOE")]


def test_a_well_changed_and_deleted_in_one_transaction_leaves_no_row(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db, db.transaction():
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])
        db.delete_well("P1", confirm=True)

    assert keys(path) == [("Forecast", "P2", "MAIN")]


def test_a_well_deleted_and_added_again_logs_afresh(tmp_path):
    path = make(tmp_path)
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.delete_well("P1", confirm=True)
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])  # inside the thirty seconds

    assert ("Forecast", "P1", "MAIN") in keys(path)


# ── the file's last-modified stamp ───────────────────────────────────────────


def test_the_stamp_is_one_row_updated_in_place(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["INSERT INTO Metadata VALUES ('LastModified', '2020-01-01T00:00:00-06:00')"])
    exec_sql(path, ["INSERT INTO Metadata VALUES ('Branch', 'MAIN')"])
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])

    (stamp,) = last_modified(path)
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None, "Obsidian reads an RFC 3339 time with its offset"
    assert abs(parsed.timestamp() - time.time()) < 60
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM Metadata WHERE type = 'Branch'").fetchall() == [
        ("MAIN",)
    ]
    conn.close()


def test_the_stamp_is_added_to_a_metadata_table_without_one(tmp_path):
    path = make(tmp_path)
    exec_sql(path, ["DROP INDEX idx_Metadata"])  # an older file: no unique index
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])

    assert len(last_modified(path)) == 1


# ── a failed log never fails the write ───────────────────────────────────────


def test_a_file_without_the_tables_is_written_as_before(tmp_path):
    path = tmp_path / "old.obsdb"
    create_core_schema(path)
    with Database.open(path, user="someone") as db:
        db.add_well("P1", rsv_cat=RsvCat.PDP)
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P1", "MAIN", Phase.GAS, [SEGMENT])
        assert len(db.forecasts("P1")) == 2

    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "ChangeLog" not in tables and "Metadata" not in tables, "the library creates neither"


def test_a_log_the_library_cannot_write_to_does_not_fail_the_save(tmp_path):
    path = make(tmp_path)
    # A leftover NOT NULL column from another build rejects every insert.
    exec_sql(path, ["DROP TABLE ChangeLog"])
    exec_sql(
        path,
        [
            "CREATE TABLE ChangeLog (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, "
            "table_name TEXT NOT NULL, op TEXT NOT NULL)"
        ],
    )
    with Database.open(path) as db:
        db.set_forecast("P1", "MAIN", Phase.OIL, [SEGMENT])
        db.set_forecast("P2", "MAIN", Phase.OIL, [SEGMENT])
        assert len(db.forecasts()) == 2

    assert len(last_modified(path)) == 1
