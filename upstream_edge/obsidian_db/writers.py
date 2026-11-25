"""High-level write operations that preserve Obsidian bookkeeping invariants."""

from __future__ import annotations

import logging
import math
import sqlite3
import struct
import warnings
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum

from . import _field_refs
from ._changelog import (
    CAPEX,
    DIFF_MODEL,
    EXPENSE_MODEL,
    FORECAST,
    INTEREST,
    MAIN,
    PRICE_MODEL,
    RUN_SETTINGS,
    SHRINK_YIELD_MODEL,
    TAX_MODEL,
    WELL_MODELS,
    Change,
    table_change,
    well_change,
)
from ._sql import (
    parse_date,
    quote_identifier,
    stored_monthly_volumes,
    tax_ad_valorem_column,
)
from ._validation import (
    reject_reserved_name,
    require_number,
    validate_api10,
    validate_month,
    validate_new_prop_id,
    validate_percent_0_100,
    validate_percentage,
)
from .enums import (
    AttributeType,
    CapexJobType,
    DiffType,
    ExpenseModelKind,
    ModelKind,
    Phase,
    RsvCat,
    TaxModelKind,
)
from .exceptions import (
    DataIntegrityError,
    DuplicateError,
    ModelFieldRefWarning,
    ModelNotFoundError,
    MonthlyVolumesWarning,
    ValidationError,
    WellNotFoundError,
)
from .models import (
    CapexItem,
    DailyRow,
    DiffModelSegment,
    ExpenseModelSegment,
    ForecastSegment,
    InterestSegment,
    MonthlyRow,
    PerfsInput,
    PriceModelSegment,
    SurveyPointInput,
    TaxModelSegment,
    WellAttributeUpdate,
)
from .types import AttributeValue

MAIN_COLUMNS: tuple[str, ...] = (
    "prop_id",
    "api_10",
    "rsv_cat",
    "lease",
    "well_number",
    "field",
    "operator",
    "category",
    "group",
    "reservoir",
    "tvd",
    "md",
    "lateral_length",
    "spud",
    "completion",
    "first_prod",
    "state",
    "county",
    "surface_latitude",
    "surface_longitude",
    "bh_latitude",
    "bh_longitude",
)

LOGGER = logging.getLogger("upstream_edge.obsidian_db")

_SYNTHETIC_MODEL_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("Expense", "ExpenseModel", "exp_model_name", "exp_model_name"),
    ("Tax", "TaxModel", "tax_model_name", "tax_model_name"),
    ("Diff", "DiffModel", "diff_model_name", "diff_model_name"),
    (
        "ShrinkYield",
        "ShrinkYieldModel",
        "shrink_yield_model_name",
        "shrink_yield_model_name",
    ),
)

# The per-well tables, besides Main, that `delete_well` clears and `copy_well`
# copies. One a file does not have is skipped.
_WELL_TABLES: tuple[str, ...] = (
    "Monthly",
    "Daily",
    "Forecast",
    "WellModels",
    "Interest",
    "Capex",
    "Abandonment",
    "Survey",
    "Reservoir",
    "Completion",
    "Casing",
    "Perfs",
    "PredictorAssignment",
    "WellAttributes",
    "DailyPressure",
    "ForecastVolumes",
)

HEADER_FIELD_TO_SQL: dict[str, str] = {
    "api_10": "api_10",
    "rsv_cat": "rsv_cat",
    "lease": "lease",
    "well_number": "well_number",
    "field": "field",
    "operator": "operator",
    "category": "category",
    "group": "group",
    "reservoir": "reservoir",
    "tvd_ft": "tvd",
    "md_ft": "md",
    "lateral_length_ft": "lateral_length",
    "spud_date": "spud",
    "completion_date": "completion",
    "first_production_date": "first_prod",
    "state": "state",
    "county": "county",
    "surface_latitude": "surface_latitude",
    "surface_longitude": "surface_longitude",
    "bottom_hole_latitude": "bh_latitude",
    "bottom_hole_longitude": "bh_longitude",
}

# The names five header fields had before 1.3 (the database's own column
# names). Still taken wherever header fields are given by keyword.
HEADER_FIELD_EARLIER_NAMES: dict[str, str] = {
    "spud": "spud_date",
    "completion": "completion_date",
    "first_prod": "first_production_date",
    "bh_latitude": "bottom_hole_latitude",
    "bh_longitude": "bottom_hole_longitude",
}


def add_well(
    conn: sqlite3.Connection,
    prop_id: str,
    *,
    rsv_cat: RsvCat,
    api_10: str | None = None,
    header_fields: Mapping[str, object],
) -> list[Change]:
    """Insert a Main row for a new well.

    Every model assignment starts as ``MAIN``, the same default Obsidian gives a
    well created inside the application. A blank model name never resolves in
    Obsidian (a blank expense model means *zero* expenses), so the defaults are
    written here rather than left for the caller.
    """
    _require_enum(rsv_cat, RsvCat, method="add_well", param="rsv_cat")
    validate_new_prop_id(prop_id, field="prop_id", method="add_well")
    validate_api10(api_10, method="add_well")
    if _exists(conn, "Main", "prop_id", prop_id):
        raise DuplicateError(f"add_well(prop_id={prop_id!r}): PropID already exists")
    fields = _header_fields(header_fields, method="add_well")
    fields["api_10"] = api_10
    fields["rsv_cat"] = rsv_cat
    values = _main_values(prop_id, fields, method="add_well")
    # Before the Main insert: creating the table (on a file that predates it)
    # gives every well already in Main its row, and the new well gets its row
    # from `_insert_default_attributes` below. Created after the insert, the
    # backfill would add the new well's row too, and the second insert would
    # fail the table's unique index.
    _ensure_well_attributes_table(conn)
    placeholders = ", ".join("?" for _ in MAIN_COLUMNS)
    columns = ", ".join(quote_identifier(column) for column in MAIN_COLUMNS)
    conn.execute(f"INSERT INTO Main ({columns}) VALUES ({placeholders})", values)
    conn.execute(
        """
        INSERT INTO WellModels (
            prop_id, scenario, exp_model_name, capex_model_name, diff_model_name,
            tax_model_name, shrink_yield_model_name, interest_model_name
        ) VALUES (?, 'MAIN', 'MAIN', 'MAIN', 'MAIN', 'MAIN', 'MAIN', 'MAIN')
        """,
        (prop_id,),
    )
    conn.execute(
        "INSERT INTO Interest (prop_id, model, start, wi_pct, nri_pct) VALUES (?, 'MAIN', '2000-01-01', 100.0, 75.0)",
        (prop_id,),
    )
    conn.execute(
        "INSERT INTO Abandonment (prop_id, model, cost_gross) VALUES (?, 'MAIN', 100000.0)",
        (prop_id,),
    )
    # A row can outlive its well when Main is edited directly. Obsidian drops
    # such rows when it loads the file; drop it here so the new well starts
    # from defaults instead of inheriting the old well's values.
    conn.execute("DELETE FROM WellAttributes WHERE prop_id = ?", (prop_id,))
    # The same for the two tables `delete_well` left behind before 1.3: a well
    # deleted then must not hand its stored forecast months to a new well of
    # the same id.
    for table in ("ForecastVolumes", "DailyPressure"):
        if _table_exists(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE prop_id = ?", (prop_id,))
    _insert_default_attributes(conn, prop_id)
    return _well_changes(conn, prop_id)


def delete_well(conn: sqlite3.Connection, prop_id: str, *, confirm: bool) -> None:
    """Delete a well: its Main row, its rows in the per-well tables, and its
    individual econ models.

    The per-well tables are Monthly, Daily, Forecast, WellModels, Interest,
    Capex, Abandonment, Survey, Reservoir, Completion, Casing, Perfs,
    PredictorAssignment, WellAttributes, ForecastVolumes (a well's stored
    monthly forecast volumes) and the other tables Obsidian keys by well. One the file
    does not have (it was last saved by an older Obsidian) holds no rows for
    the well and is skipped.
    """
    if not confirm:
        raise ValidationError("delete_well: confirm=True is required")
    _require_well(conn, prop_id, method="delete_well")
    _delete_synthetic_models_for_well(conn, prop_id)
    for table in _WELL_TABLES:
        if _table_exists(conn, table):
            conn.execute(f"DELETE FROM {quote_identifier(table)} WHERE prop_id = ?", (prop_id,))
    conn.execute("DELETE FROM Main WHERE prop_id = ?", (prop_id,))


def copy_well(conn: sqlite3.Connection, from_prop_id: str, to_prop_id: str) -> list[Change]:
    """Copy a well: its Main row, its rows in the per-well tables, and its
    individual econ models, all under the new PropID.

    The per-well tables are the ones `delete_well` clears. One the file does not
    have is skipped.
    """
    _require_well(conn, from_prop_id, method="copy_well")
    validate_new_prop_id(to_prop_id, field="to_prop_id", method="copy_well")
    if _exists(conn, "Main", "prop_id", to_prop_id):
        raise DuplicateError(f"copy_well(to_prop_id={to_prop_id!r}): PropID already exists")
    _copy_prop_id_rows(conn, "Main", from_prop_id, to_prop_id)
    for table in _WELL_TABLES:
        if _table_exists(conn, table):
            _copy_prop_id_rows(conn, table, from_prop_id, to_prop_id)
    _copy_synthetic_models_for_well(conn, from_prop_id, to_prop_id)
    return _well_changes(conn, to_prop_id)


def set_well_header(
    conn: sqlite3.Connection, prop_id: str, fields: Mapping[str, object]
) -> list[Change]:
    """Update selected Main columns for an existing well."""
    if not fields:
        raise ValidationError("set_well_header: at least one field must be specified")
    if not _exists(conn, "Main", "prop_id", prop_id):
        raise WellNotFoundError(prop_id, f"set_well_header(prop_id={prop_id!r}): well not found")
    fields = _header_fields(fields, method="set_well_header")
    assignments: list[str] = []
    values: list[object] = []
    for field, value in fields.items():
        if field not in HEADER_FIELD_TO_SQL:
            valid = ", ".join(sorted(HEADER_FIELD_TO_SQL))
            raise ValidationError(
                f"set_well_header: unknown field {field!r}. Valid fields: {valid}"
            )
        if field == "rsv_cat":
            if value is None:
                raise ValidationError("set_well_header: rsv_cat cannot be cleared")
            _require_enum(value, RsvCat, method="set_well_header", param="rsv_cat")
        if field == "api_10":
            validate_api10(
                value if isinstance(value, str) or value is None else str(value),
                method="set_well_header",
            )
        assignments.append(f"{quote_identifier(HEADER_FIELD_TO_SQL[field])} = ?")
        values.append(_db_value(value))
    columns = [HEADER_FIELD_TO_SQL[field] for field in fields]
    selected = ", ".join(quote_identifier(column) for column in columns)
    before = conn.execute(f"SELECT {selected} FROM Main WHERE prop_id = ?", (prop_id,)).fetchone()
    changed = [
        column
        for column, value in zip(columns, values, strict=True)
        if _blank_as_none(before[column]) != _blank_as_none(value)
    ]
    values.append(prop_id)
    conn.execute(f"UPDATE Main SET {', '.join(assignments)} WHERE prop_id = ?", tuple(values))
    return [well_change(MAIN, prop_id, column) for column in changed]


def set_monthly_prod(conn: sqlite3.Connection, rows: list[MonthlyRow]) -> None:
    """Upsert monthly production rows."""
    if not rows:
        return
    for row in rows:
        _require_well(conn, row.prop_id, method="set_monthly_prod")
        validate_month(row.month, field="month", method="set_monthly_prod")
        conn.execute(
            """
            INSERT INTO Monthly (prop_id, month, oil_monthly_bbl, gas_monthly_mscf, water_monthly_bbl)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(prop_id, month) DO UPDATE SET
                oil_monthly_bbl = excluded.oil_monthly_bbl,
                gas_monthly_mscf = excluded.gas_monthly_mscf,
                water_monthly_bbl = excluded.water_monthly_bbl
            """,
            # Monthly.month is stored as 6-digit YYYYMM text, matching Obsidian's
            # native format.
            (
                row.prop_id,
                f"{row.month.year:04d}{row.month.month:02d}",
                row.oil_bbl,
                row.gas_mscf,
                row.water_bbl,
            ),
        )
    for prop_id in sorted({row.prop_id for row in rows}):
        earliest = min(row.month for row in rows if row.prop_id == prop_id)
        conn.execute(
            """
            UPDATE Main
            SET spud = COALESCE(spud, ?),
                completion = COALESCE(completion, ?),
                first_prod = COALESCE(first_prod, ?)
            WHERE prop_id = ?
            """,
            (
                _as_date(earliest).isoformat(),
                _as_date(earliest).isoformat(),
                _as_date(earliest).isoformat(),
                prop_id,
            ),
        )


def set_daily_prod(conn: sqlite3.Connection, rows: list[DailyRow]) -> None:
    """Upsert daily production rows."""
    if not rows:
        return
    for row in rows:
        _require_well(conn, row.prop_id, method="set_daily_prod")
        conn.execute(
            """
            INSERT INTO Daily (prop_id, date, oil_bopd, gas_mcfd, water_bwpd)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(prop_id, date) DO UPDATE SET
                oil_bopd = excluded.oil_bopd,
                gas_mcfd = excluded.gas_mcfd,
                water_bwpd = excluded.water_bwpd
            """,
            (
                row.prop_id,
                _as_date(row.date).isoformat(),
                row.oil_bopd,
                row.gas_mcfd,
                row.water_bwpd,
            ),
        )


def delete_monthly_prod(
    conn: sqlite3.Connection, prop_id: str | None = None, *, confirm: bool = False
) -> None:
    """Delete monthly production for one well or all wells."""
    if not confirm:
        raise ValidationError("delete_monthly_prod: confirm=True is required")
    if prop_id is None:
        conn.execute("DELETE FROM Monthly")
        return
    _require_well(conn, prop_id, method="delete_monthly_prod")
    conn.execute("DELETE FROM Monthly WHERE prop_id = ?", (prop_id,))


def delete_daily_prod(
    conn: sqlite3.Connection, prop_id: str | None = None, *, confirm: bool = False
) -> None:
    """Delete daily production for one well or all wells."""
    if not confirm:
        raise ValidationError("delete_daily_prod: confirm=True is required")
    if prop_id is None:
        conn.execute("DELETE FROM Daily")
        return
    _require_well(conn, prop_id, method="delete_daily_prod")
    conn.execute("DELETE FROM Daily WHERE prop_id = ?", (prop_id,))


def set_forecast(
    conn: sqlite3.Connection,
    prop_id: str,
    model: str,
    phase: Phase,
    segments: list[ForecastSegment],
) -> list[Change]:
    """Replace forecast segments for one PropID/model/phase.

    ``model`` is a forecast-model name. The curve is not applied until a
    scenario references the name through ``set_scenario(forecast_model=...)``.
    Stored monthly volumes the phase starts with are left as they are, with a
    warning: they take precedence over a segment for the months they cover.
    """
    _require_enum(phase, Phase, method="set_forecast", param="phase")
    _require_well(conn, prop_id, method="set_forecast")
    if not segments:
        raise ValidationError(
            "set_forecast: segments cannot be empty; use delete_forecast to clear"
        )
    starts = [segment.start for segment in segments]
    if len(starts) != len(set(starts)):
        raise ValidationError("set_forecast: duplicate segment start dates are not allowed")
    for segment in segments:
        _require_segment_numbers(segment)
    stored = _stored_volumes(conn, " WHERE prop_id = ? AND model = ?", [prop_id, model], phase)
    if stored:
        _, _, first, months = stored[0]
        after = _months_after(first, months)
        # A tail that starts where the stored months end is the ordinary case
        # and needs no word. A segment that starts earlier does.
        if any(_as_date(segment.start) < after for segment in segments):
            warnings.warn(
                f"set_forecast(prop_id={prop_id!r}, model={model!r}): this forecast starts "
                f"with {months} stored monthly volumes, {first:%Y-%m} up to {after:%Y-%m}. "
                "Obsidian starts the phase at the first of them and uses them as they are, "
                f"so a segment has no effect before {after:%Y-%m}. They are not changed; "
                "forecasts() returns them.",
                MonthlyVolumesWarning,
                stacklevel=3,
            )
    conn.execute(
        "DELETE FROM Forecast WHERE prop_id = ? AND model = ? AND upper(phase) = ?",
        (prop_id, model, phase.value),
    )
    for segment in sorted(segments, key=lambda item: item.start):
        conn.execute(
            """
            INSERT INTO Forecast (
                prop_id, model, phase, start, type_curve, rate_init, decline_init,
                b_factor, decline_min
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prop_id,
                model,
                phase.value,
                _as_date(segment.start).isoformat(),
                segment.type_curve,
                segment.rate_init,
                segment.decline_init,
                segment.b_factor,
                segment.decline_min,
            ),
        )
    return [well_change(FORECAST, prop_id, model)]


def delete_forecast(
    conn: sqlite3.Connection,
    prop_id: str | None = None,
    model: str | None = None,
    phase: Phase | None = None,
    *,
    confirm: bool = False,
) -> list[Change]:
    """Delete forecast rows matching optional filters.

    A forecast is its segments and any stored monthly volumes it starts with;
    both go. With a ``phase``, that phase's segments go and its stored months
    are cleared, the other phases' kept, which is what deleting one phase in
    Obsidian does.
    """
    if not confirm:
        raise ValidationError("delete_forecast: confirm=True is required")
    if phase is not None:
        _require_enum(phase, Phase, method="delete_forecast", param="phase")
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        _require_well(conn, prop_id, method="delete_forecast")
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    forecast_where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if phase is not None:
        changes = _clear_stored_phase(conn, forecast_where, params, phase)
        where = f" WHERE {' AND '.join([*clauses, 'upper(phase) = ?'])}"
        changes += _keyed_changes(
            conn, FORECAST, "Forecast", "model", where, [*params, phase.value]
        )
        conn.execute(f"DELETE FROM Forecast{where}", (*params, phase.value))
        return changes
    changes = _keyed_changes(conn, FORECAST, "Forecast", "model", forecast_where, params)
    if _table_exists(conn, "ForecastVolumes"):
        # A forecast with both parts is named twice; the log keeps one row per key.
        changes += _keyed_changes(
            conn, FORECAST, "ForecastVolumes", "model", forecast_where, params
        )
        conn.execute(f"DELETE FROM ForecastVolumes{forecast_where}", tuple(params))
    conn.execute(f"DELETE FROM Forecast{forecast_where}", tuple(params))
    return changes


def set_price_model(
    conn: sqlite3.Connection, name: str, segments: list[PriceModelSegment]
) -> list[Change]:
    """Replace a price model with one or more segments.

    Stores a named, shared deck only. It is applied to a scenario's wells
    globally via ``set_scenario(price_model=name)``; there is no per-well price
    assignment.
    """
    reject_reserved_name(name, field="name", method="set_price_model")
    _require_segments(segments, method="set_price_model", delete_method="delete_price_model")
    _require_unique_dates([segment.start_date for segment in segments], method="set_price_model")
    conn.execute("DELETE FROM PriceModel WHERE price_model_name = ?", (name,))
    for segment in sorted(segments, key=lambda item: item.start_date):
        conn.execute(
            "INSERT INTO PriceModel (price_model_name, start_date, oil, gas, ngl) VALUES (?, ?, ?, ?, ?)",
            (name, _as_date(segment.start_date).isoformat(), segment.oil, segment.gas, segment.ngl),
        )
    return [table_change(PRICE_MODEL, name)]


def delete_price_model(
    conn: sqlite3.Connection, name: str, *, confirm: bool = False
) -> list[Change]:
    """Delete all segments for a price model."""
    if not confirm:
        raise ValidationError("delete_price_model: confirm=True is required")
    deleted = conn.execute("DELETE FROM PriceModel WHERE price_model_name = ?", (name,)).rowcount
    return [table_change(PRICE_MODEL, name)] if deleted else []


def set_expense_model(
    conn: sqlite3.Connection,
    name: str,
    segments: list[ExpenseModelSegment],
) -> list[Change]:
    """Replace a shared expense model."""
    reject_reserved_name(name, field="name", method="set_expense_model")
    _require_segments(segments, method="set_expense_model", delete_method="delete_expense_model")
    segment_keys = [
        _expense_model_type(segment, method="set_expense_model") for segment in segments
    ]
    _require_unique_text(segment_keys, method="set_expense_model", label="model_type")
    _field_refs.carry(
        conn,
        ModelKind.EXPENSE,
        name,
        [_segment_start(segment.age_months, segment.effective_date) for segment in segments],
        method="set_expense_model",
    )
    conn.execute("DELETE FROM ExpenseModel WHERE exp_model_name = ?", (name,))
    for segment, segment_key in sorted(
        zip(segments, segment_keys, strict=True), key=lambda item: item[1]
    ):
        conn.execute(
            """
            INSERT INTO ExpenseModel (
                exp_model_name, model_type, fixed_monthly, variable_oil, variable_gas, variable_water
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                segment_key,
                segment.fixed_monthly,
                segment.variable_oil,
                segment.variable_gas,
                segment.variable_water,
            ),
        )
    _warn_if_read_per_well(conn, ModelKind.EXPENSE, name, method="set_expense_model")
    return [table_change(EXPENSE_MODEL, name)]


def delete_expense_model(
    conn: sqlite3.Connection, name: str, *, confirm: bool = False
) -> list[Change]:
    """Delete a shared expense model."""
    if not confirm:
        raise ValidationError("delete_expense_model: confirm=True is required")
    deleted = conn.execute("DELETE FROM ExpenseModel WHERE exp_model_name = ?", (name,)).rowcount
    _field_refs.delete_for_model(conn, ModelKind.EXPENSE, name)
    return [table_change(EXPENSE_MODEL, name)] if deleted else []


def set_tax_model(
    conn: sqlite3.Connection,
    name: str,
    segments: list[TaxModelSegment],
) -> list[Change]:
    """Replace a shared tax model."""
    reject_reserved_name(name, field="name", method="set_tax_model")
    _require_segments(segments, method="set_tax_model", delete_method="delete_tax_model")
    segment_keys = [_tax_model_type(segment, method="set_tax_model") for segment in segments]
    _require_unique_text(segment_keys, method="set_tax_model", label="model_type")
    for segment in segments:
        validate_percentage(segment.sev_tax_oil, field="sev_tax_oil", method="set_tax_model")
        validate_percentage(segment.sev_tax_gas, field="sev_tax_gas", method="set_tax_model")
        validate_percentage(segment.sev_tax_ngl, field="sev_tax_ngl", method="set_tax_model")
        validate_percentage(segment.ad_valorem_tax, field="ad_valorem_tax", method="set_tax_model")
    _field_refs.carry(
        conn,
        ModelKind.TAX,
        name,
        [_segment_start(segment.age_months, segment.effective_date) for segment in segments],
        method="set_tax_model",
    )
    # The file's own spelling of the ad valorem column; never renamed here.
    ad_valorem_column = tax_ad_valorem_column(conn)
    conn.execute("DELETE FROM TaxModel WHERE tax_model_name = ?", (name,))
    for segment, segment_key in sorted(
        zip(segments, segment_keys, strict=True), key=lambda item: item[1]
    ):
        conn.execute(
            f"""
            INSERT INTO TaxModel (
                tax_model_name, model_type, sev_tax_oil, sev_tax_gas, sev_tax_ngl,
                {ad_valorem_column}
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                segment_key,
                segment.sev_tax_oil,
                segment.sev_tax_gas,
                segment.sev_tax_ngl,
                segment.ad_valorem_tax,
            ),
        )
    _warn_if_read_per_well(conn, ModelKind.TAX, name, method="set_tax_model")
    return [table_change(TAX_MODEL, name)]


def delete_tax_model(conn: sqlite3.Connection, name: str, *, confirm: bool = False) -> list[Change]:
    """Delete a shared tax model."""
    if not confirm:
        raise ValidationError("delete_tax_model: confirm=True is required")
    deleted = conn.execute("DELETE FROM TaxModel WHERE tax_model_name = ?", (name,)).rowcount
    _field_refs.delete_for_model(conn, ModelKind.TAX, name)
    return [table_change(TAX_MODEL, name)] if deleted else []


def set_diff_model(
    conn: sqlite3.Connection,
    name: str,
    segments: list[DiffModelSegment],
) -> list[Change]:
    """Replace a shared differential model.

    Obsidian's DiffModel also carries a condensate differential, which this
    library does not expose. Because the write is a delete-and-reinsert, the
    existing condensate values are carried across per ``start_date`` rather than
    reset, so editing a model here leaves the condensate side as Obsidian set it.
    """
    reject_reserved_name(name, field="name", method="set_diff_model")
    _require_segments(segments, method="set_diff_model", delete_method="delete_diff_model")
    _require_unique_dates([segment.start_date for segment in segments], method="set_diff_model")
    for segment in segments:
        _require_enum(segment.oil_method, DiffType, method="set_diff_model", param="oil_method")
        _require_enum(segment.gas_method, DiffType, method="set_diff_model", param="gas_method")
        _require_enum(segment.ngl_method, DiffType, method="set_diff_model", param="ngl_method")
    existing_condensate = {
        str(row["start_date"]): (row["condensate_diff_method"], row["condensate_diff"])
        for row in conn.execute(
            """
            SELECT start_date, condensate_diff_method, condensate_diff
            FROM DiffModel WHERE diff_model_name = ?
            """,
            (name,),
        ).fetchall()
    }
    _field_refs.carry(
        conn,
        ModelKind.DIFF,
        name,
        [_field_refs.month_start(segment.start_date) for segment in segments],
        method="set_diff_model",
    )
    conn.execute("DELETE FROM DiffModel WHERE diff_model_name = ?", (name,))
    for segment in sorted(segments, key=lambda item: item.start_date):
        # A segment on a start_date the model did not have before gets the same
        # defaults Obsidian's own schema applies to a new row.
        condensate_method, condensate_diff = existing_condensate.get(
            _as_date(segment.start_date).isoformat(), (DiffType.FRACTION.value, 0.0)
        )
        conn.execute(
            """
            INSERT INTO DiffModel (
                diff_model_name, start_date, oil_diff_method, oil_diff, gas_diff_method, gas_diff,
                ngl_diff_method, ngl_diff, condensate_diff_method, condensate_diff
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                _as_date(segment.start_date).isoformat(),
                segment.oil_method.value,
                segment.oil_diff,
                segment.gas_method.value,
                segment.gas_diff,
                segment.ngl_method.value,
                segment.ngl_diff,
                condensate_method,
                condensate_diff,
            ),
        )
    _warn_if_read_per_well(conn, ModelKind.DIFF, name, method="set_diff_model")
    return [table_change(DIFF_MODEL, name)]


def delete_diff_model(
    conn: sqlite3.Connection, name: str, *, confirm: bool = False
) -> list[Change]:
    """Delete a shared differential model."""
    if not confirm:
        raise ValidationError("delete_diff_model: confirm=True is required")
    deleted = conn.execute("DELETE FROM DiffModel WHERE diff_model_name = ?", (name,)).rowcount
    _field_refs.delete_for_model(conn, ModelKind.DIFF, name)
    return [table_change(DIFF_MODEL, name)] if deleted else []


def set_shrink_yield_model(
    conn: sqlite3.Connection,
    name: str,
    *,
    gas_shrink_frac: float,
    ngl_yield_bbl_mmscf: float,
) -> list[Change]:
    """Replace a shared shrink/yield model.

    Obsidian's ShrinkYieldModel also carries a condensate yield, which this
    library does not expose. The existing value is carried across rather than
    reset, so editing a model here leaves it as Obsidian set it.
    """
    reject_reserved_name(name, field="name", method="set_shrink_yield_model")
    validate_percentage(gas_shrink_frac, field="gas_shrink_frac", method="set_shrink_yield_model")
    existing = conn.execute(
        "SELECT condensate_yield_bbl_mmscf FROM ShrinkYieldModel WHERE shrink_yield_model_name = ?",
        (name,),
    ).fetchone()
    # Obsidian's schema defaults a new row's condensate yield to 0.0.
    condensate_yield = 0.0 if existing is None else existing["condensate_yield_bbl_mmscf"]
    conn.execute("DELETE FROM ShrinkYieldModel WHERE shrink_yield_model_name = ?", (name,))
    conn.execute(
        """
        INSERT INTO ShrinkYieldModel (
            shrink_yield_model_name, gas_shrink_frac, ngl_yield_bbl_mmscf,
            condensate_yield_bbl_mmscf
        ) VALUES (?, ?, ?, ?)
        """,
        (name, gas_shrink_frac, ngl_yield_bbl_mmscf, condensate_yield),
    )
    _warn_if_read_per_well(conn, ModelKind.SHRINK_YIELD, name, method="set_shrink_yield_model")
    return [table_change(SHRINK_YIELD_MODEL, name)]


def delete_shrink_yield_model(
    conn: sqlite3.Connection, name: str, *, confirm: bool = False
) -> list[Change]:
    """Delete a shared shrink/yield model."""
    if not confirm:
        raise ValidationError("delete_shrink_yield_model: confirm=True is required")
    deleted = conn.execute(
        "DELETE FROM ShrinkYieldModel WHERE shrink_yield_model_name = ?", (name,)
    ).rowcount
    _field_refs.delete_for_model(conn, ModelKind.SHRINK_YIELD, name)
    return [table_change(SHRINK_YIELD_MODEL, name)] if deleted else []


def create_scenario(conn: sqlite3.Connection, name: str, *, copy_from: str | None = None) -> None:
    """Create a scenario, optionally copying another scenario's assignments."""
    reject_reserved_name(name, field="name", method="create_scenario")
    if _exists(conn, "Scenario", "scenario", name):
        raise DuplicateError(f"create_scenario(name={name!r}): scenario already exists")
    if copy_from is None:
        conn.execute(
            "INSERT INTO Scenario (scenario, forecast_model_name, price_model_name) VALUES (?, '', '')",
            (name,),
        )
        return
    # Copy every column the Scenario table actually has, not just the two this
    # library exposes. Obsidian stores its per-scenario new-well defaults here
    # (default_exp_model, default_wi, ...), and naming only the known columns
    # silently dropped them from the copy.
    source_columns = [
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(Scenario)").fetchall()
        if str(row["name"]) != "scenario"
    ]
    selected = ", ".join(quote_identifier(column) for column in source_columns)
    source = conn.execute(
        f"SELECT {selected} FROM Scenario WHERE scenario = ?"
        if selected
        else "SELECT scenario FROM Scenario WHERE scenario = ?",
        (copy_from,),
    ).fetchone()
    if source is None:
        raise ModelNotFoundError(
            "Scenario",
            copy_from,
            f"create_scenario(copy_from={copy_from!r}): source scenario does not exist",
        )
    insert_columns = ", ".join(quote_identifier(column) for column in ("scenario", *source_columns))
    placeholders = ", ".join("?" * (len(source_columns) + 1))
    conn.execute(
        f"INSERT INTO Scenario ({insert_columns}) VALUES ({placeholders})",
        (name, *(source[column] for column in source_columns)),
    )
    conn.execute(
        """
        INSERT INTO WellModels (
            prop_id, scenario, exp_model_name, capex_model_name, diff_model_name,
            tax_model_name, shrink_yield_model_name, interest_model_name
        )
        SELECT prop_id, ?, exp_model_name, capex_model_name, diff_model_name,
               tax_model_name, shrink_yield_model_name, interest_model_name
        FROM WellModels
        WHERE scenario = ?
        """,
        (name, copy_from),
    )
    _copy_synthetic_models_for_scenario(conn, from_scenario=copy_from, to_scenario=name)


def delete_scenario(conn: sqlite3.Connection, name: str, *, confirm: bool) -> None:
    """Delete a scenario and its WellModels assignments."""
    if name == "MAIN":
        raise ValidationError("delete_scenario: the MAIN scenario cannot be deleted")
    if not confirm:
        raise ValidationError("delete_scenario: confirm=True is required")
    for row in conn.execute(
        "SELECT prop_id FROM WellModels WHERE scenario = ?", (name,)
    ).fetchall():
        _delete_synthetic_model_set(conn, _synthetic_model_name(str(row["prop_id"]), name))
    conn.execute("DELETE FROM WellModels WHERE scenario = ?", (name,))
    conn.execute("DELETE FROM Scenario WHERE scenario = ?", (name,))


def set_scenario(
    conn: sqlite3.Connection,
    name: str,
    *,
    forecast_model: str | None = None,
    price_model: str | None = None,
) -> None:
    """Partially update a scenario's forecast and price model names.

    Forecast and price models are applied globally to every well in the
    scenario, so they are assigned here rather than per well in
    ``set_well_models``. ``price_model`` must name an existing deck (hard error
    otherwise); a missing ``forecast_model`` only warns, since it is a label in
    the Forecast table that may be populated later.
    """
    if forecast_model is None and price_model is None:
        raise ValidationError("set_scenario: at least one field must be specified")
    if not _exists(conn, "Scenario", "scenario", name):
        raise ModelNotFoundError(
            "Scenario", name, f"set_scenario(name={name!r}): scenario not found"
        )
    assignments: list[str] = []
    params: list[object] = []
    if forecast_model is not None:
        _warn_if_label_missing(conn, "Forecast", "model", forecast_model, method="set_scenario")
        assignments.append("forecast_model_name = ?")
        params.append(forecast_model)
    if price_model is not None:
        _require_model(conn, "Price", "PriceModel", "price_model_name", price_model, "set_scenario")
        assignments.append("price_model_name = ?")
        params.append(price_model)
    params.append(name)
    conn.execute(f"UPDATE Scenario SET {', '.join(assignments)} WHERE scenario = ?", tuple(params))


def set_run_settings(
    conn: sqlite3.Connection, name: str, *, effective_date: date | None = None
) -> list[Change]:
    """Update a run settings' effective date.

    The date is the first day of the month the economics run from. The change
    is logged only when the date moves, as Obsidian logs it.
    """
    if effective_date is None:
        raise ValidationError("set_run_settings: at least one field must be specified")
    validate_month(effective_date, field="effective_date", method="set_run_settings")
    row = conn.execute(
        "SELECT effective_date FROM RunSettings WHERE settings_name = ?", (name,)
    ).fetchone()
    if row is None:
        raise ModelNotFoundError(
            "RunSettings", name, f"set_run_settings(name={name!r}): run settings not found"
        )
    stored = _as_date(effective_date).isoformat()
    if str(row[0] or "")[:10] == stored:
        return []
    conn.execute(
        "UPDATE RunSettings SET effective_date = ? WHERE settings_name = ?", (stored, name)
    )
    return [table_change(RUN_SETTINGS, name)]


def set_well_models(
    conn: sqlite3.Connection,
    prop_ids: str | list[str],
    scenario: str,
    *,
    expense_model: str | None = None,
    capex_model: str | None = None,
    diff_model: str | None = None,
    tax_model: str | None = None,
    shrink_yield_model: str | None = None,
    interest_model: str | None = None,
    exp_model: str | None = None,
) -> list[Change]:
    """Partially update per-well model assignments for a scenario.

    Handles the six per-well model kinds only. The scenario-global forecast and
    price models are assigned through ``set_scenario``, not here.
    """
    # `exp_model` is the name `expense_model` had before 1.3.
    if exp_model is not None:
        if expense_model is not None:
            raise ValidationError(
                "set_well_models: the expense model was given twice, as 'expense_model' "
                "and as 'exp_model'"
            )
        expense_model = exp_model
    updates = {
        "exp_model_name": expense_model,
        "capex_model_name": capex_model,
        "diff_model_name": diff_model,
        "tax_model_name": tax_model,
        "shrink_yield_model_name": shrink_yield_model,
        "interest_model_name": interest_model,
    }
    updates = {key: value for key, value in updates.items() if value is not None}
    if not updates:
        raise ValidationError("set_well_models: at least one field must be specified")
    if not _exists(conn, "Scenario", "scenario", scenario):
        raise ModelNotFoundError(
            "Scenario", scenario, f"set_well_models(scenario={scenario!r}): scenario not found"
        )
    ids = _prop_id_list(prop_ids)
    for prop_id in ids:
        _require_well(conn, prop_id, method="set_well_models")
    if expense_model is not None:
        _require_model(
            conn, "Expense", "ExpenseModel", "exp_model_name", expense_model, "set_well_models"
        )
    if diff_model is not None:
        _require_model(conn, "Diff", "DiffModel", "diff_model_name", diff_model, "set_well_models")
    if tax_model is not None:
        _require_model(conn, "Tax", "TaxModel", "tax_model_name", tax_model, "set_well_models")
    if shrink_yield_model is not None:
        _require_model(
            conn,
            "ShrinkYield",
            "ShrinkYieldModel",
            "shrink_yield_model_name",
            shrink_yield_model,
            "set_well_models",
        )
    if capex_model is not None:
        _warn_if_label_missing(conn, "Capex", "model", capex_model, method="set_well_models")
    if interest_model is not None:
        _warn_if_label_missing(conn, "Interest", "model", interest_model, method="set_well_models")
    for prop_id in ids:
        _ensure_well_models_row(conn, prop_id, scenario)
        assignments = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE WellModels SET {assignments} WHERE prop_id = ? AND scenario = ?",
            (*updates.values(), prop_id, scenario),
        )
    return [well_change(WELL_MODELS, prop_id, scenario) for prop_id in ids]


def set_interest(
    conn: sqlite3.Connection, prop_ids: str | list[str], model: str, segments: list[InterestSegment]
) -> list[Change]:
    """Replace an interest schedule for one model across one or more wells."""
    reject_reserved_name(model, field="model", method="set_interest")
    _require_segments(segments, method="set_interest", delete_method="delete_interest")
    ids = _prop_id_list(prop_ids)
    for prop_id in ids:
        _require_well(conn, prop_id, method="set_interest")
    starts = [segment.start for segment in segments]
    _require_unique_dates(starts, method="set_interest")
    # Interest columns hold percentages from 0 to 100, e.g. 75.0 for 75%.
    for segment in segments:
        validate_percent_0_100(segment.wi_pct, field="wi_pct", method="set_interest")
        validate_percent_0_100(segment.nri_pct, field="nri_pct", method="set_interest")
    is_new_model = not _exists(conn, "Interest", "model", model)
    missing: list[str] = []
    if is_new_model:
        all_prop_ids = _all_prop_ids(conn)
        missing = sorted(all_prop_ids - set(ids))
        for prop_id in missing:
            conn.execute(
                "INSERT INTO Interest (prop_id, model, start, wi_pct, nri_pct) VALUES (?, ?, '2000-01-01', 0.0, 0.0)",
                (prop_id, model),
            )
        LOGGER.info("set_interest: gap-filled model %s for %d PropIDs", model, len(missing))
    for prop_id in ids:
        conn.execute("DELETE FROM Interest WHERE prop_id = ? AND model = ?", (prop_id, model))
        for segment in sorted(segments, key=lambda item: item.start):
            conn.execute(
                "INSERT INTO Interest (prop_id, model, start, wi_pct, nri_pct) VALUES (?, ?, ?, ?, ?)",
                (
                    prop_id,
                    model,
                    _as_date(segment.start).isoformat(),
                    segment.wi_pct,
                    segment.nri_pct,
                ),
            )
    return [well_change(INTEREST, prop_id, model) for prop_id in (*ids, *missing)]


def delete_interest(
    conn: sqlite3.Connection,
    prop_ids: str | list[str] | None = None,
    model: str | None = None,
    *,
    confirm: bool = False,
) -> list[Change]:
    """Delete interest rows matching optional filters."""
    if not confirm:
        raise ValidationError("delete_interest: confirm=True is required")
    clauses: list[str] = []
    params: list[object] = []
    if prop_ids is not None:
        ids = _prop_id_list(prop_ids)
        placeholders = ", ".join("?" for _ in ids)
        clauses.append(f"prop_id IN ({placeholders})")
        params.extend(ids)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    changes = _keyed_changes(conn, INTEREST, "Interest", "model", where, params)
    conn.execute(f"DELETE FROM Interest{where}", tuple(params))
    return changes


def set_capex(
    conn: sqlite3.Connection, prop_id: str, model: str, items: list[CapexItem]
) -> list[Change]:
    """Replace capex items for one PropID/model pair."""
    reject_reserved_name(model, field="model", method="set_capex")
    _require_well(conn, prop_id, method="set_capex")
    if not items:
        raise ValidationError("set_capex: items cannot be empty; use delete_capex to clear")
    for item in items:
        _require_enum(item.job_type, CapexJobType, method="set_capex", param="job_type")
        require_number(item.cost_gross, field="cost_gross", method="set_capex")
    conn.execute("DELETE FROM Capex WHERE prop_id = ? AND model = ?", (prop_id, model))
    for item in sorted(items, key=lambda entry: entry.date):
        conn.execute(
            """
            INSERT INTO Capex (prop_id, model, date, job_type, cost_gross, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prop_id,
                model,
                _as_date(item.date).isoformat(),
                item.job_type.value,
                item.cost_gross,
                item.description,
            ),
        )
    return [well_change(CAPEX, prop_id, model)]


def delete_capex(
    conn: sqlite3.Connection,
    prop_id: str | None = None,
    model: str | None = None,
    *,
    confirm: bool = False,
) -> list[Change]:
    """Delete capex rows matching optional filters."""
    if not confirm:
        raise ValidationError("delete_capex: confirm=True is required")
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        _require_well(conn, prop_id, method="delete_capex")
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    changes = _keyed_changes(conn, CAPEX, "Capex", "model", where, params)
    conn.execute(f"DELETE FROM Capex{where}", tuple(params))
    return changes


def set_abandonment(
    conn: sqlite3.Connection, prop_id: str, model: str, cost_gross: float
) -> list[Change]:
    """Replace one abandonment cost row.

    Every well carries an abandonment cost for each model; pass ``cost_gross=0.0``
    to clear it rather than removing the row.
    """
    reject_reserved_name(model, field="model", method="set_abandonment")
    _require_well(conn, prop_id, method="set_abandonment")
    require_number(cost_gross, field="cost_gross", method="set_abandonment")
    conn.execute("DELETE FROM Abandonment WHERE prop_id = ? AND model = ?", (prop_id, model))
    conn.execute(
        "INSERT INTO Abandonment (prop_id, model, cost_gross) VALUES (?, ?, ?)",
        (prop_id, model, cost_gross),
    )
    return [well_change(CAPEX, prop_id, model)]


def set_surveys(conn: sqlite3.Connection, prop_id: str, points: list[SurveyPointInput]) -> None:
    """Replace every survey point for a well."""
    _require_well(conn, prop_id, method="set_surveys")
    if not points:
        raise ValidationError("set_surveys: points cannot be empty; use delete_surveys to clear")
    conn.execute("DELETE FROM Survey WHERE prop_id = ?", (prop_id,))
    for point in sorted(points, key=lambda item: item.point_md):
        conn.execute(
            """
            INSERT INTO Survey (
                prop_id, point_md, point_tvd, azimuth_angle, inclination_angle,
                deviation_ns, deviation_ew
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prop_id,
                point.point_md,
                point.point_tvd,
                point.azimuth_angle,
                point.inclination_angle,
                point.deviation_ns,
                point.deviation_ew,
            ),
        )


def delete_surveys(
    conn: sqlite3.Connection, prop_id: str | None = None, *, confirm: bool = False
) -> None:
    """Delete survey rows for one well or all wells."""
    if not confirm:
        raise ValidationError("delete_surveys: confirm=True is required")
    if prop_id is None:
        conn.execute("DELETE FROM Survey")
        return
    _require_well(conn, prop_id, method="delete_surveys")
    conn.execute("DELETE FROM Survey WHERE prop_id = ?", (prop_id,))


def set_reservoir(
    conn: sqlite3.Connection,
    prop_id: str,
    reservoir: str,
    *,
    top_depth_ft: float,
    thickness_ft: float | None = None,
) -> None:
    """Upsert one reservoir row."""
    _require_well(conn, prop_id, method="set_reservoir")
    exists = conn.execute(
        "SELECT 1 FROM Reservoir WHERE prop_id = ? AND reservoir = ?",
        (prop_id, reservoir),
    ).fetchone()
    if exists is None:
        conn.execute(
            """
            INSERT INTO Reservoir (prop_id, reservoir, top_depth_ft, gross_thickness_ft)
            VALUES (?, ?, ?, ?)
            """,
            (prop_id, reservoir, top_depth_ft, thickness_ft),
        )
        return
    # Update only the columns this writer owns so any other reservoir columns
    # the database carries are preserved.
    conn.execute(
        """
        UPDATE Reservoir SET top_depth_ft = ?, gross_thickness_ft = ?
        WHERE prop_id = ? AND reservoir = ?
        """,
        (top_depth_ft, thickness_ft, prop_id, reservoir),
    )


def delete_reservoir_data(
    conn: sqlite3.Connection,
    prop_id: str | None = None,
    reservoir: str | None = None,
    *,
    confirm: bool = False,
) -> None:
    """Delete reservoir rows matching optional filters."""
    if not confirm:
        raise ValidationError("delete_reservoir_data: confirm=True is required")
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        _require_well(conn, prop_id, method="delete_reservoir_data")
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if reservoir is not None:
        clauses.append("reservoir = ?")
        params.append(reservoir)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn.execute(f"DELETE FROM Reservoir{where}", tuple(params))


def set_completion(
    conn: sqlite3.Connection,
    prop_id: str,
    *,
    frac_proppant_lb: float | None = None,
    frac_fluid_bbl: float | None = None,
    frac_stages: int | None = None,
) -> None:
    """Partially update or create a completion row."""
    _require_well(conn, prop_id, method="set_completion")
    fields = {
        "frac_proppant_lb": frac_proppant_lb,
        "frac_fluid_bbl": frac_fluid_bbl,
        "frac_stages": frac_stages,
    }
    fields = {key: value for key, value in fields.items() if value is not None}
    if not fields:
        raise ValidationError("set_completion: at least one field must be specified")
    row = conn.execute("SELECT 1 FROM Completion WHERE prop_id = ?", (prop_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO Completion (prop_id, frac_proppant_lb, frac_fluid_bbl, frac_stages) VALUES (?, NULL, NULL, NULL)",
            (prop_id,),
        )
    assignments = ", ".join(f"{column} = ?" for column in fields)
    conn.execute(
        f"UPDATE Completion SET {assignments} WHERE prop_id = ?",
        (*fields.values(), prop_id),
    )


def delete_completion(conn: sqlite3.Connection, prop_id: str, *, confirm: bool = False) -> None:
    """Delete one completion row."""
    if not confirm:
        raise ValidationError("delete_completion: confirm=True is required")
    _require_well(conn, prop_id, method="delete_completion")
    conn.execute("DELETE FROM Completion WHERE prop_id = ?", (prop_id,))


def set_perfs(conn: sqlite3.Connection, prop_id: str, perfs: list[PerfsInput]) -> None:
    """Replace every perforation interval for a well."""
    _require_well(conn, prop_id, method="set_perfs")
    if not perfs:
        raise ValidationError("set_perfs: perfs cannot be empty; use delete_perfs to clear")
    conn.execute("DELETE FROM Perfs WHERE prop_id = ?", (prop_id,))
    for perf in sorted(perfs, key=lambda item: item.perf_start_md_ft):
        conn.execute(
            """
            INSERT INTO Perfs (prop_id, perf_start_md_ft, perf_end_md_ft, producing)
            VALUES (?, ?, ?, ?)
            """,
            (prop_id, perf.perf_start_md_ft, perf.perf_end_md_ft, int(perf.producing)),
        )


def delete_perfs(
    conn: sqlite3.Connection, prop_id: str | None = None, *, confirm: bool = False
) -> None:
    """Delete perforation rows for one well or all wells."""
    if not confirm:
        raise ValidationError("delete_perfs: confirm=True is required")
    if prop_id is None:
        conn.execute("DELETE FROM Perfs")
        return
    _require_well(conn, prop_id, method="delete_perfs")
    conn.execute("DELETE FROM Perfs WHERE prop_id = ?", (prop_id,))


def set_well_attribute(
    conn: sqlite3.Connection, prop_id: str, column: str, value: AttributeValue
) -> None:
    """Set one WellAttributes cell.

    A well with no WellAttributes row (one added to Main by another tool) gets
    one first, with the same defaults `add_well` gives a new well, so the value
    is stored rather than the update matching nothing.
    """
    _require_well(conn, prop_id, method="set_well_attribute")
    attr_type = _require_attribute_column(conn, column, method="set_well_attribute")
    db_value = _attribute_db_value(value, attr_type=attr_type, method="set_well_attribute")
    if not _exists(conn, "WellAttributes", "prop_id", prop_id):
        _insert_default_attributes(conn, prop_id)
    conn.execute(
        f"UPDATE WellAttributes SET {quote_identifier(column)} = ? WHERE prop_id = ?",
        (db_value, prop_id),
    )


def set_well_attributes_bulk(conn: sqlite3.Connection, updates: list[WellAttributeUpdate]) -> None:
    """Set many WellAttributes cells."""
    if not updates:
        return
    for update in updates:
        set_well_attribute(conn, update.prop_id, update.column, update.value)


def add_well_attribute_column(
    conn: sqlite3.Connection,
    name: str,
    attr_type: AttributeType,
    default: AttributeValue | None = None,
) -> None:
    """Add a user-defined WellAttributes column and backfill existing rows.

    The default is stored on the column itself, matching the DDL Obsidian emits,
    so wells this library adds later start with it too. Obsidian gives a well it
    adds the type default (0, 2000-01-01 or empty) instead.
    """
    _require_enum(attr_type, AttributeType, method="add_well_attribute_column", param="attr_type")
    _ensure_well_attributes_table(conn)
    _validate_attribute_column_name(conn, name, method="add_well_attribute_column")
    db_default = _attribute_db_value(
        _default_for_attribute_type(attr_type) if default is None else default,
        attr_type=attr_type,
        method="add_well_attribute_column",
    )
    # NOT NULL DEFAULT matches the DDL Obsidian itself emits, and SQLite
    # backfills existing rows with the default, so no follow-up UPDATE is
    # needed. `_insert_default_attributes` relies on the declared default for
    # wells added later.
    conn.execute(
        f"ALTER TABLE WellAttributes ADD COLUMN {quote_identifier(name)} "
        f"{_attribute_sql_type(attr_type)} NOT NULL DEFAULT {_sql_literal(db_default)}"
    )


def rename_well_attribute_column(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Rename a user-defined WellAttributes column."""
    _require_attribute_column(conn, old, method="rename_well_attribute_column")
    _validate_attribute_column_name(conn, new, method="rename_well_attribute_column")
    conn.execute(
        f"ALTER TABLE WellAttributes RENAME COLUMN {quote_identifier(old)} TO {quote_identifier(new)}"
    )
    # An econ model field that reads the column keeps reading it.
    _field_refs.rename_column(conn, old, new)


def delete_well_attribute_column(
    conn: sqlite3.Connection, name: str, *, confirm: bool, detach_model_refs: bool = False
) -> None:
    """Delete a user-defined WellAttributes column.

    A column that an econ model field reads per well is not deleted unless
    ``detach_model_refs`` is set: the references are then removed with it, and
    those fields go back to the number stored on the model. It is Obsidian's
    own rule (its "Detach & Delete").
    """
    if not confirm:
        raise ValidationError("delete_well_attribute_column: confirm=True is required")
    _require_attribute_column(conn, name, method="delete_well_attribute_column")
    models = _field_refs.models_on_column(conn, name)
    if models and not detach_model_refs:
        shown = ", ".join(models[:5]) + (f" (+{len(models) - 5} more)" if len(models) > 5 else "")
        raise ValidationError(
            f"delete_well_attribute_column: column {name!r} is read per well by econ model "
            f"fields on {shown}. Pass detach_model_refs=True to remove those references with "
            "the column (the models keep their stored numbers); model_field_refs() lists them."
        )
    _field_refs.detach_column(conn, name)
    conn.execute(f"ALTER TABLE WellAttributes DROP COLUMN {quote_identifier(name)}")


def set_parent_version(conn: sqlite3.Connection, version: str) -> None:
    """Record the version the database now descends from (``Metadata.Parent``),
    as Obsidian does when a snapshot lands. A file without the Metadata table
    is left as it is."""
    if not _table_exists(conn, "Metadata"):
        return
    updated = conn.execute(
        "UPDATE Metadata SET value = ? WHERE type = 'Parent'", (version,)
    ).rowcount
    if updated == 0:
        conn.execute("INSERT INTO Metadata (type, value) VALUES ('Parent', ?)", (version,))


def _well_changes(conn: sqlite3.Connection, prop_id: str) -> list[Change]:
    """What a new or copied well logs: one change per forecast model it has
    rows under, segments or stored monthly volumes.

    This is all Obsidian logs for a well it creates or copies. The interests,
    capital and assignments a well starts with are its defaults, not edits.
    """
    models: dict[str, None] = {}
    for table in ("Forecast", "ForecastVolumes"):
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT DISTINCT model FROM {table} WHERE prop_id = ? ORDER BY 1", (prop_id,)
        ).fetchall()
        models.update(dict.fromkeys(str(row[0]) for row in rows))
    return [well_change(FORECAST, prop_id, model) for model in sorted(models)]


def _keyed_changes(
    conn: sqlite3.Connection,
    log_table: str,
    table: str,
    column: str,
    where: str,
    params: Sequence[object],
) -> list[Change]:
    """One change per well and model among the rows a delete is about to
    remove. A filter that matches nothing logs nothing."""
    rows = conn.execute(
        f"SELECT DISTINCT prop_id, {column} FROM {table}{where} ORDER BY 1, 2", tuple(params)
    ).fetchall()
    return [well_change(log_table, str(row[0]), str(row[1])) for row in rows]


def _blank_as_none(value: object) -> object:
    """Obsidian stores a missing optional value as NULL or as empty text."""
    return None if value == "" else value


_VOLUME_COLUMNS = {Phase.OIL: "oil_bbl", Phase.GAS: "gas_mscf", Phase.WATER: "water_bbl"}


def _stored_rows(
    conn: sqlite3.Connection, where: str, params: Sequence[object]
) -> list[tuple[str, str, date, dict[Phase, tuple[float, ...]]]]:
    """The ForecastVolumes rows Obsidian uses among the wells and models asked
    for: ``(prop_id, model, first month, the months of each phase)``. A row it
    skips at load (unreadable, or naming no model) is not among them."""
    if not _table_exists(conn, "ForecastVolumes"):
        return []
    rows = conn.execute(
        "SELECT prop_id, model, start, oil_bbl, gas_mscf, water_bbl "
        f"FROM ForecastVolumes{where} ORDER BY 1, 2",
        tuple(params),
    ).fetchall()
    used: list[tuple[str, str, date, dict[Phase, tuple[float, ...]]]] = []
    for row in rows:
        first = parse_date(row[2])
        if first is None or not str(row[1] or "").strip():
            continue
        try:
            oil, gas, water = stored_monthly_volumes(row[3], row[4], row[5])
        except DataIntegrityError:
            continue
        series = {Phase.OIL: oil, Phase.GAS: gas, Phase.WATER: water}
        used.append((str(row[0]), str(row[1]), first.replace(day=1), series))
    return used


def _stored_volumes(
    conn: sqlite3.Connection, where: str, params: Sequence[object], phase: Phase
) -> list[tuple[str, str, date, int]]:
    """The forecasts among the wells and models asked for that start with stored
    monthly volumes of the phase: ``(prop_id, model, first month, months)``. A
    phase whose stored months are all zero has none, as Obsidian reads it."""
    return [
        (prop_id, model, first, len(series[phase]))
        for prop_id, model, first, series in _stored_rows(conn, where, params)
        if any(volume > 0.0 for volume in series[phase])
    ]


def _clear_stored_phase(
    conn: sqlite3.Connection, where: str, params: Sequence[object], phase: Phase
) -> list[Change]:
    """Clear one phase's stored months, as Obsidian's own delete of a phase
    does: the phase's months become zero, and a row left with no phase at all
    is removed. Left in place they would go on being the phase's forecast."""
    changes: list[Change] = []
    for prop_id, model, _, series in _stored_rows(conn, where, params):
        if not any(volume > 0.0 for volume in series[phase]):
            continue
        key = (prop_id, model)
        others = [volumes for other, volumes in series.items() if other is not phase]
        if all(volume <= 0.0 for volumes in others for volume in volumes):
            conn.execute("DELETE FROM ForecastVolumes WHERE prop_id = ? AND model = ?", key)
        else:
            zeros = struct.pack(f"<{len(series[phase])}f", *([0.0] * len(series[phase])))
            conn.execute(
                f"UPDATE ForecastVolumes SET {_VOLUME_COLUMNS[phase]} = ? "
                "WHERE prop_id = ? AND model = ?",
                (zeros, *key),
            )
        changes.append(well_change(FORECAST, prop_id, model))
    return changes


def _months_after(first: date, months: int) -> date:
    """The first day of the month that follows a run of months."""
    count = first.year * 12 + (first.month - 1) + months
    return date(count // 12, count % 12 + 1, 1)


def _as_date(value: date) -> date:
    """A date with no time of day. A ``datetime`` (or a pandas ``Timestamp``)
    is a date too, and written as it is would store a time Obsidian and this
    library both refuse to read back."""
    return date(value.year, value.month, value.day)


def _require_segment_numbers(segment: ForecastSegment) -> None:
    """A segment needs a start date and, unless it follows a type curve, its
    four decline numbers."""
    if not isinstance(segment.start, date):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValidationError(
            f"set_forecast: a segment's start must be a date; got {segment.start!r}"
        )
    if segment.type_curve:
        return
    for name in ("rate_init", "decline_init", "b_factor", "decline_min"):
        value = getattr(segment, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValidationError(
                f"set_forecast: the segment starting {segment.start} has {name}={value!r}; a "
                "decline segment needs a number there. (A row of forecasts() whose kind is "
                "MONTHLY_VOLUMES is not a segment and cannot be written as one.)"
            )


def _segment_start(age_months: int | None, effective_date: date | None) -> _field_refs.Start:
    """When an expense or tax segment starts, for matching its field references."""
    if age_months is not None:
        return _field_refs.aged_start(age_months)
    if effective_date is not None:
        return _field_refs.dated_start(effective_date)
    return _field_refs.simple_start()


def _warn_if_read_per_well(
    conn: sqlite3.Connection, kind: ModelKind, name: str, *, method: str
) -> None:
    """Warn when fields of the model just written are read per well: the
    numbers written for them are only the stored fallback."""
    fields = _field_refs.live_fields(conn, kind, name)
    if not fields:
        return
    shown = ", ".join(f"{field} (from {column!r})" for field, column in fields)
    warnings.warn(
        f"{method}(name={name!r}): Obsidian reads these fields of the model per well, from "
        f"a well attribute column: {shown}. The numbers written for them are only the "
        "stored fallback; each well keeps its own value. The references are unchanged; "
        "read them with model_field_refs().",
        ModelFieldRefWarning,
        stacklevel=4,
    )


def _require_enum(value: object, enum_cls: type[StrEnum], *, method: str, param: str) -> None:
    if not isinstance(value, enum_cls):
        raise ValidationError(
            f"{method}: {param} must be a {enum_cls.__name__} enum member "
            f"(e.g. {enum_cls.__name__}.{next(iter(enum_cls)).name}), not {value!r}"
        )


def _header_fields(fields: Mapping[str, object], *, method: str) -> dict[str, object]:
    """Header fields under their own names, whichever name each came in under."""
    named: dict[str, object] = {}
    for field, value in fields.items():
        own = HEADER_FIELD_EARLIER_NAMES.get(field, field)
        if own in named:
            raise ValidationError(f"{method}: {own!r} was given twice, under two names")
        named[own] = _header_value(own, value, method=method)
    return named


_HEADER_DATES = frozenset({"spud_date", "completion_date", "first_production_date"})
_HEADER_NUMBERS = frozenset(
    {
        "tvd_ft",
        "md_ft",
        "lateral_length_ft",
        "surface_latitude",
        "surface_longitude",
        "bottom_hole_latitude",
        "bottom_hole_longitude",
    }
)


def _header_value(field: str, value: object, *, method: str) -> object:
    """A header value the readers, and Obsidian, can read back. A date field
    takes a date (or ISO date text) and a number field a finite number; either
    takes None to clear it."""
    if value is None:
        return None
    if field in _HEADER_DATES:
        parsed = parse_date(value) if isinstance(value, str) else value
        if isinstance(parsed, bool) or not isinstance(parsed, date):
            raise ValidationError(
                f"{method}: {field} must be a date (or 'YYYY-MM-DD' text); got {value!r}"
            )
        return _as_date(parsed)
    if field in _HEADER_NUMBERS:
        require_number(value, field=field, method=method)
    return value


def _main_values(prop_id: str, fields: Mapping[str, object], *, method: str) -> tuple[object, ...]:
    unknown = set(fields) - set(HEADER_FIELD_TO_SQL)
    if unknown:
        valid = ", ".join(sorted(HEADER_FIELD_TO_SQL))
        raise ValidationError(
            f"{method}: unknown header field {sorted(unknown)[0]!r}. Valid fields: {valid}"
        )
    values: dict[str, object] = {column: None for column in MAIN_COLUMNS}
    values["prop_id"] = prop_id
    for field, value in fields.items():
        values[HEADER_FIELD_TO_SQL[field]] = _db_value(value)
    if values["rsv_cat"] is None:
        raise ValidationError(f"{method}: rsv_cat is required and cannot be null")
    return tuple(values[column] for column in MAIN_COLUMNS)


def _require_segments(segments: Sequence[object], *, method: str, delete_method: str) -> None:
    if not segments:
        raise ValidationError(f"{method}: segments cannot be empty; use {delete_method} to clear")


def _require_unique_dates(values: list[date], *, method: str) -> None:
    if len(values) != len(set(values)):
        raise ValidationError(f"{method}: duplicate segment dates are not allowed")


def _require_unique_text(values: list[str], *, method: str, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValidationError(f"{method}: duplicate {label} values are not allowed")


def _expense_model_type(segment: ExpenseModelSegment, *, method: str) -> str:
    _require_enum(segment.kind, ExpenseModelKind, method=method, param="kind")
    return _kind_model_type(
        kind=segment.kind,
        age_months=segment.age_months,
        effective_date=segment.effective_date,
        method=method,
    )


def _tax_model_type(segment: TaxModelSegment, *, method: str) -> str:
    _require_enum(segment.kind, TaxModelKind, method=method, param="kind")
    return _kind_model_type(
        kind=segment.kind,
        age_months=segment.age_months,
        effective_date=segment.effective_date,
        method=method,
    )


def _kind_model_type(
    *,
    kind: ExpenseModelKind | TaxModelKind,
    age_months: int | None,
    effective_date: date | None,
    method: str,
) -> str:
    if kind.value == ExpenseModelKind.SIMPLE.value:
        if age_months is not None or effective_date is not None:
            raise ValidationError(f"{method}: SIMPLE segments reject age_months and effective_date")
        return "SIMPLE"
    if kind.value == ExpenseModelKind.AGE_BASED.value:
        if age_months is None:
            raise ValidationError(f"{method}: AGE_BASED segments require age_months")
        if effective_date is not None:
            raise ValidationError(f"{method}: AGE_BASED segments reject effective_date")
        return f"AGEBASED({age_months})"
    if effective_date is None:
        raise ValidationError(f"{method}: DATE_BASED segments require effective_date")
    if age_months is not None:
        raise ValidationError(f"{method}: DATE_BASED segments reject age_months")
    return f"DATEBASED({_as_date(effective_date).isoformat()})"


def _well_attribute_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(WellAttributes)").fetchall()
        if str(row["name"]).lower() != "prop_id"
    }


def _attribute_column_types(conn: sqlite3.Connection) -> dict[str, AttributeType]:
    result: dict[str, AttributeType] = {}
    for row in conn.execute("PRAGMA table_info(WellAttributes)").fetchall():
        name = str(row["name"])
        if name.lower() == "prop_id":
            continue
        result[name] = _attribute_type_from_sql(str(row["type"]))
    return result


def _attribute_type_from_sql(sql_type: str) -> AttributeType:
    upper = sql_type.upper()
    if "REAL" in upper or "NUM" in upper or "FLOA" in upper or "DOUB" in upper:
        return AttributeType.NUMERIC
    if "DATE" in upper:
        return AttributeType.DATE
    return AttributeType.TEXT


def _require_attribute_column(
    conn: sqlite3.Connection, column: str, *, method: str
) -> AttributeType:
    # A file with no WellAttributes table has no attribute columns either:
    # Obsidian creates the table without any, so it is an unknown column, not a
    # missing table.
    if not _table_exists(conn, "WellAttributes"):
        raise ValidationError(
            f"{method}: unknown WellAttributes column {column!r}. The database has no "
            "WellAttributes table yet, so it has no attribute columns; "
            "add_well_attribute_column creates the table and adds the column"
        )
    columns = _attribute_column_types(conn)
    if not columns:
        raise ValidationError(
            f"{method}: unknown WellAttributes column {column!r}. The database has no "
            "attribute columns yet; add one with add_well_attribute_column"
        )
    if column not in columns:
        valid = ", ".join(sorted(columns))
        raise ValidationError(
            f"{method}: unknown WellAttributes column {column!r}. Valid columns: {valid}"
        )
    return columns[column]


def _validate_attribute_column_name(conn: sqlite3.Connection, name: str, *, method: str) -> None:
    if not name:
        raise ValidationError(f"{method}: WellAttributes column name must be non-empty")
    if name.startswith("_"):
        raise ValidationError(f"{method}: WellAttributes column names must not start with '_'")
    if name.lower() == "prop_id":
        raise ValidationError(f"{method}: WellAttributes column name {name!r} is reserved")
    if not all(char.isascii() and (char.isalnum() or char in {" ", "_", "-"}) for char in name):
        raise ValidationError(
            f"{method}: WellAttributes column names may contain only ASCII letters, "
            "numbers, spaces, underscores, and hyphens"
        )
    existing = {column.lower() for column in _well_attribute_columns(conn)}
    if name.lower() in existing:
        raise ValidationError(f"{method}: WellAttributes column {name!r} already exists")


def _sql_literal(value: str | float) -> str:
    """Render a validated attribute default as a SQL literal.

    SQLite does not accept a bound parameter in ``ADD COLUMN ... DEFAULT``, so
    the value has to be inlined. Only values that already passed
    ``_attribute_db_value`` reach here, meaning a float or a str.
    """
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return repr(float(value))


def _attribute_sql_type(attr_type: AttributeType) -> str:
    if attr_type is AttributeType.NUMERIC:
        return "REAL"
    if attr_type is AttributeType.DATE:
        return "DATE"
    return "TEXT"


def _default_for_attribute_type(attr_type: AttributeType) -> AttributeValue:
    if attr_type is AttributeType.NUMERIC:
        return 0.0
    if attr_type is AttributeType.DATE:
        return date(2000, 1, 1)
    return ""


def _attribute_db_value(
    value: AttributeValue, *, attr_type: AttributeType, method: str
) -> str | float:
    if attr_type is AttributeType.NUMERIC:
        if not isinstance(value, float):
            raise ValidationError(f"{method}: NUMERIC WellAttributes values must be float")
        if not math.isfinite(value):
            raise ValidationError(
                f"{method}: NUMERIC WellAttributes values must be finite, got {value!r}"
            )
        return value
    if attr_type is AttributeType.DATE:
        if not isinstance(value, date):
            raise ValidationError(f"{method}: DATE WellAttributes values must be datetime.date")
        return value.isoformat()
    if not isinstance(value, str):
        raise ValidationError(f"{method}: TEXT WellAttributes values must be str")
    return value


def _all_prop_ids(conn: sqlite3.Connection) -> set[str]:
    return {str(row["prop_id"]) for row in conn.execute("SELECT prop_id FROM Main").fetchall()}


def _db_value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, date):
        return _as_date(value).isoformat()
    return value


def _copy_prop_id_rows(
    conn: sqlite3.Connection, table: str, from_prop_id: str, to_prop_id: str
) -> None:
    columns = [
        str(row["name"]) for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")
    ]
    if "prop_id" not in columns:
        return
    rows = conn.execute(
        f"SELECT * FROM {quote_identifier(table)} WHERE prop_id = ?", (from_prop_id,)
    ).fetchall()
    if not rows:
        return
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    for row in rows:
        values = [row[column] for column in columns]
        values[columns.index("prop_id")] = to_prop_id
        conn.execute(
            f"INSERT INTO {quote_identifier(table)} ({quoted_columns}) VALUES ({placeholders})",
            tuple(values),
        )


def _delete_synthetic_models_for_well(conn: sqlite3.Connection, prop_id: str) -> None:
    # Without WellModels no individual model is assigned to the well.
    if not _table_exists(conn, "WellModels"):
        return
    rows = conn.execute("SELECT scenario FROM WellModels WHERE prop_id = ?", (prop_id,)).fetchall()
    for row in rows:
        _delete_synthetic_model_set(conn, _synthetic_model_name(prop_id, str(row["scenario"])))


def _delete_synthetic_model_set(conn: sqlite3.Connection, model_name: str) -> None:
    for _model_type, table, name_column, _well_models_column in _SYNTHETIC_MODEL_SPECS:
        if not _table_exists(conn, table):
            continue
        conn.execute(
            f"DELETE FROM {quote_identifier(table)} WHERE {quote_identifier(name_column)} = ?",
            (model_name,),
        )
    _field_refs.delete_for_model_name(conn, model_name)


def _copy_synthetic_models_for_well(
    conn: sqlite3.Connection, from_prop_id: str, to_prop_id: str
) -> None:
    if not _table_exists(conn, "WellModels"):
        return
    rows = conn.execute(
        "SELECT * FROM WellModels WHERE prop_id = ? ORDER BY scenario", (to_prop_id,)
    ).fetchall()
    for row in rows:
        scenario = str(row["scenario"])
        _copy_synthetic_model_assignments(
            conn,
            row=row,
            prop_id=to_prop_id,
            scenario=scenario,
            from_model_name=_synthetic_model_name(from_prop_id, scenario),
            to_model_name=_synthetic_model_name(to_prop_id, scenario),
        )


def _copy_synthetic_models_for_scenario(
    conn: sqlite3.Connection, *, from_scenario: str, to_scenario: str
) -> None:
    rows = conn.execute(
        "SELECT * FROM WellModels WHERE scenario = ? ORDER BY prop_id", (to_scenario,)
    ).fetchall()
    for row in rows:
        prop_id = str(row["prop_id"])
        _copy_synthetic_model_assignments(
            conn,
            row=row,
            prop_id=prop_id,
            scenario=to_scenario,
            from_model_name=_synthetic_model_name(prop_id, from_scenario),
            to_model_name=_synthetic_model_name(prop_id, to_scenario),
        )


def _copy_synthetic_model_assignments(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    prop_id: str,
    scenario: str,
    from_model_name: str,
    to_model_name: str,
) -> None:
    updated_columns: list[str] = []
    for model_type, table, name_column, well_models_column in _SYNTHETIC_MODEL_SPECS:
        if str(row[well_models_column] or "") != from_model_name:
            continue
        copied = _copy_named_model_rows(
            conn,
            table=table,
            name_column=name_column,
            from_model_name=from_model_name,
            to_model_name=to_model_name,
        )
        if copied:
            _field_refs.copy_for_model(conn, model_type, from_model_name, to_model_name)
            updated_columns.append(well_models_column)
    if not updated_columns:
        return
    assignments = ", ".join(f"{quote_identifier(column)} = ?" for column in updated_columns)
    conn.execute(
        f"UPDATE WellModels SET {assignments} WHERE prop_id = ? AND scenario = ?",
        (*[to_model_name for _column in updated_columns], prop_id, scenario),
    )


def _copy_named_model_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    name_column: str,
    from_model_name: str,
    to_model_name: str,
) -> bool:
    if not _table_exists(conn, table):
        return False
    columns = [
        str(row["name"]) for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")
    ]
    rows = conn.execute(
        f"SELECT * FROM {quote_identifier(table)} WHERE {quote_identifier(name_column)} = ?",
        (from_model_name,),
    ).fetchall()
    if not rows:
        return False
    conn.execute(
        f"DELETE FROM {quote_identifier(table)} WHERE {quote_identifier(name_column)} = ?",
        (to_model_name,),
    )
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    name_index = columns.index(name_column)
    for row in rows:
        values = [row[column] for column in columns]
        values[name_index] = to_model_name
        conn.execute(
            f"INSERT INTO {quote_identifier(table)} ({quoted_columns}) VALUES ({placeholders})",
            tuple(values),
        )
    return True


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table,),
        ).fetchone()
        is not None
    )


def _exists(conn: sqlite3.Connection, table: str, column: str, value: str) -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {quote_identifier(table)} WHERE {quote_identifier(column)} = ? LIMIT 1",
        (value,),
    ).fetchone()
    return row is not None


def _require_well(conn: sqlite3.Connection, prop_id: str, *, method: str) -> None:
    if not _exists(conn, "Main", "prop_id", prop_id):
        raise WellNotFoundError(prop_id, f"{method}(prop_id={prop_id!r}): well not found")


def _prop_id_list(prop_ids: str | list[str]) -> list[str]:
    ids = [prop_ids] if isinstance(prop_ids, str) else list(prop_ids)
    if not ids:
        raise ValidationError("prop_ids must contain at least one PropID")
    return ids


def _require_model(
    conn: sqlite3.Connection,
    model_kind: str,
    table: str,
    column: str,
    name: str,
    method: str,
) -> None:
    if not _exists(conn, table, column, name):
        raise ModelNotFoundError(
            model_kind,
            name,
            f"{method}: {model_kind} model {name!r} does not exist",
        )


def _warn_if_label_missing(
    conn: sqlite3.Connection, table: str, column: str, label: str, *, method: str
) -> None:
    if not _exists(conn, table, column, label):
        warnings.warn(
            f"{method}: label {label!r} has no rows yet in {table}",
            UserWarning,
            stacklevel=3,
        )


def _ensure_well_models_row(conn: sqlite3.Connection, prop_id: str, scenario: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM WellModels WHERE prop_id = ? AND scenario = ? LIMIT 1",
        (prop_id, scenario),
    ).fetchone()
    if row is not None:
        return
    conn.execute(
        """
        INSERT INTO WellModels (
            prop_id, scenario, exp_model_name, capex_model_name, diff_model_name,
            tax_model_name, shrink_yield_model_name, interest_model_name
        ) VALUES (?, ?, 'MAIN', 'MAIN', 'MAIN', 'MAIN', 'MAIN', 'MAIN')
        """,
        (prop_id, scenario),
    )


def _synthetic_model_name(prop_id: str, scenario: str) -> str:
    return f"{scenario}<>{prop_id}"


# Older databases may predate the WellAttributes table; create it the way the
# Obsidian application does (prop_id plus a unique index, no user columns).
def _ensure_well_attributes_table(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "WellAttributes"):
        return
    conn.execute('CREATE TABLE "WellAttributes" (prop_id TEXT NOT NULL)')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_WellAttributes ON WellAttributes (prop_id)")
    conn.executemany(
        "INSERT INTO WellAttributes (prop_id) VALUES (?)",
        [(prop_id,) for prop_id in sorted(_all_prop_ids(conn))],
    )


# The table must already exist; `add_well` creates it before touching Main.
# A column with a declared default is left out of the insert so SQLite applies
# it; one without (an older column, or one added by another tool) gets its type
# default.
def _insert_default_attributes(conn: sqlite3.Connection, prop_id: str) -> None:
    columns = conn.execute("PRAGMA table_info(WellAttributes)").fetchall()
    names: list[str] = ["prop_id"]
    values: list[AttributeValue] = [prop_id]
    for column in columns:
        name = str(column["name"])
        if name.lower() == "prop_id":
            continue
        declared_default = column["dflt_value"]
        if declared_default is not None and str(declared_default).upper() != "NULL":
            continue
        names.append(name)
        values.append(_default_attribute_value(str(column["type"])))
    quoted_names = ", ".join(quote_identifier(name) for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO WellAttributes ({quoted_names}) VALUES ({placeholders})", tuple(values)
    )


def _default_attribute_value(sql_type: str) -> AttributeValue:
    upper = sql_type.upper()
    if "REAL" in upper or "NUM" in upper:
        return 0.0
    if "DATE" in upper:
        return "2000-01-01"
    return ""
