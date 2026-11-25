"""Typed read helpers for Obsidian database tables."""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TypeVar

from . import _field_refs
from ._sql import (
    missing_table_name,
    parse_date,
    stored_monthly_volumes,
    tax_ad_valorem_column,
)
from .enums import (
    CapexJobType,
    CutoffMethod,
    DiffType,
    ExpenseModelKind,
    IdcMethod,
    ModelKind,
    Phase,
    RsvCat,
    TaxModelKind,
    TypeCurveFitKind,
)
from .exceptions import DataIntegrityError, MissingTableError
from .models import (
    Abandonment,
    Capex,
    Completion,
    DailyRow,
    DiffModel,
    ExpenseModel,
    Forecast,
    Interest,
    ModelFieldRef,
    MonthlyRow,
    Perfs,
    PriceModel,
    Reservoir,
    RunSettings,
    Scenario,
    ShrinkYieldModel,
    SurveyPoint,
    TaxModel,
    TypeCurve,
    Well,
    WellModels,
)

EnumT = TypeVar("EnumT", bound=StrEnum)

LOGGER = logging.getLogger("upstream_edge.obsidian_db")


# Per-well override models live in the shared model tables under synthetic
# "<scenario><>{prop_id}" names. Unfiltered listings exclude them so only real
# shared models appear; fetching by exact name still works.
def _shared_model_where(column: str, name: str | None) -> str:
    if name is None:
        return f"WHERE instr({column}, '<>') = 0"
    return f"WHERE {column} = ?"


def wells(conn: sqlite3.Connection) -> list[Well]:
    """Return every well header ordered by PropID."""
    rows = _query(
        conn,
        """
        SELECT prop_id, api_10, rsv_cat, lease, well_number, field, operator, category,
               "group", reservoir, tvd, md, lateral_length, spud, completion, first_prod,
               state, county, surface_latitude, surface_longitude, bh_latitude, bh_longitude
        FROM Main
        ORDER BY prop_id
        """,
    )
    return [_well_from_row(row) for row in rows]


def well(conn: sqlite3.Connection, prop_id: str) -> Well | None:
    """Return one well header by PropID, or None when absent."""
    rows = _query(
        conn,
        """
        SELECT prop_id, api_10, rsv_cat, lease, well_number, field, operator, category,
               "group", reservoir, tvd, md, lateral_length, spud, completion, first_prod,
               state, county, surface_latitude, surface_longitude, bh_latitude, bh_longitude
        FROM Main
        WHERE prop_id = ?
        """,
        (prop_id,),
    )
    return _well_from_row(rows[0]) if rows else None


def well_by_api(conn: sqlite3.Connection, api_10: str) -> Well | None:
    """Return the first well matching API10, or None when absent."""
    rows = _query(
        conn,
        """
        SELECT prop_id, api_10, rsv_cat, lease, well_number, field, operator, category,
               "group", reservoir, tvd, md, lateral_length, spud, completion, first_prod,
               state, county, surface_latitude, surface_longitude, bh_latitude, bh_longitude
        FROM Main
        WHERE api_10 = ?
        ORDER BY prop_id
        LIMIT 1
        """,
        (api_10,),
    )
    return _well_from_row(rows[0]) if rows else None


def production_monthly(conn: sqlite3.Connection, prop_id: str | None = None) -> list[MonthlyRow]:
    """Return monthly production rows, optionally filtered by PropID."""
    where = "" if prop_id is None else "WHERE prop_id = ?"
    params: tuple[object, ...] = () if prop_id is None else (prop_id,)
    rows = _query(
        conn,
        f"""
        SELECT prop_id, month, oil_monthly_bbl, gas_monthly_mscf, water_monthly_bbl
        FROM Monthly
        {where}
        ORDER BY prop_id, month
        """,
        params,
    )
    return [
        MonthlyRow(
            prop_id=str(row["prop_id"]),
            month=_month_from_db(row["month"], field="Monthly.month"),
            oil_bbl=_optional_float(row["oil_monthly_bbl"], field="Monthly.oil_monthly_bbl"),
            gas_mscf=_optional_float(row["gas_monthly_mscf"], field="Monthly.gas_monthly_mscf"),
            water_bbl=_optional_float(row["water_monthly_bbl"], field="Monthly.water_monthly_bbl"),
        )
        for row in rows
    ]


def production_daily(conn: sqlite3.Connection, prop_id: str | None = None) -> list[DailyRow]:
    """Return daily production rows, optionally filtered by PropID."""
    where = "" if prop_id is None else "WHERE prop_id = ?"
    params: tuple[object, ...] = () if prop_id is None else (prop_id,)
    rows = _query(
        conn,
        f"""
        SELECT prop_id, date, oil_bopd, gas_mcfd, water_bwpd
        FROM Daily
        {where}
        ORDER BY prop_id, date
        """,
        params,
    )
    return [
        DailyRow(
            prop_id=str(row["prop_id"]),
            date=_date_from_db(row["date"], field="Daily.date"),
            oil_bopd=_optional_float(row["oil_bopd"], field="Daily.oil_bopd"),
            gas_mcfd=_optional_float(row["gas_mcfd"], field="Daily.gas_mcfd"),
            water_bwpd=_optional_float(row["water_bwpd"], field="Daily.water_bwpd"),
        )
        for row in rows
    ]


def forecasts(
    conn: sqlite3.Connection, prop_id: str | None = None, model: str | None = None
) -> list[Forecast]:
    """Return forecast rows with optional PropID and model filters: the
    segments, and any stored monthly volumes a forecast starts with."""
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _query(
        conn,
        f"""
        SELECT prop_id, model, phase, start, type_curve, rate_init, decline_init,
               b_factor, decline_min
        FROM Forecast
        {where}
        ORDER BY prop_id, model, phase, start
        """,
        tuple(params),
    )
    pieces = [
        Forecast(
            prop_id=str(row["prop_id"]),
            model=str(row["model"]),
            phase=_enum_from_db(Phase, str(row["phase"]).upper(), field="Forecast.phase"),
            start=_date_from_db(row["start"], field="Forecast.start"),
            type_curve=_optional_str(row["type_curve"]),
            rate_init=_optional_float(row["rate_init"], field="Forecast.rate_init"),
            decline_init=_optional_float(row["decline_init"], field="Forecast.decline_init"),
            b_factor=_optional_float(row["b_factor"], field="Forecast.b_factor"),
            decline_min=_optional_float(row["decline_min"], field="Forecast.decline_min"),
        )
        for row in rows
    ]
    stored = _stored_monthly_volumes(conn, where, tuple(params))
    # Each phase in order of start, the stored months ahead of a segment that
    # starts with them. Sorted here, not in SQL: a file can spell a phase in
    # another case, and SQL would then order "Oil" apart from "OIL".
    return sorted(
        [*pieces, *stored],
        key=lambda piece: (
            piece.prop_id,
            piece.model,
            piece.phase.value,
            piece.start,
            piece.monthly_volumes is None,
        ),
    )


def _stored_monthly_volumes(
    conn: sqlite3.Connection, where: str, params: tuple[object, ...]
) -> list[Forecast]:
    """The stored monthly volumes of the wells and models asked for, one row
    per phase that has any.

    Obsidian keeps the three phases of a well and model in one ForecastVolumes
    row, the same number of months each. A phase whose months are all zero has
    no stored volumes (its segments are its forecast). A file without the table
    predates stored volumes and has none.

    A row that cannot be read is logged and skipped, which is what Obsidian
    does with it at load: the well's segments are then its whole forecast, and
    one bad row never fails the read of every other well.
    """
    try:
        rows = _query(
            conn,
            f"""
            SELECT prop_id, model, start, oil_bbl, gas_mscf, water_bbl
            FROM ForecastVolumes
            {where}
            ORDER BY prop_id, model
            """,
            params,
        )
    except MissingTableError:
        return []
    stored: list[Forecast] = []
    for row in rows:
        try:
            if not str(row["model"] or "").strip():
                raise DataIntegrityError("the row names no model")
            oil, gas, water = stored_monthly_volumes(
                row["oil_bbl"], row["gas_mscf"], row["water_bbl"]
            )
            start = _date_from_db(row["start"], field="ForecastVolumes.start").replace(day=1)
        except DataIntegrityError as exc:
            LOGGER.warning(
                "stored monthly volumes of %r on %r skipped, as Obsidian skips them: %s",
                row["prop_id"],
                row["model"],
                exc,
            )
            continue
        for phase, volumes in ((Phase.OIL, oil), (Phase.GAS, gas), (Phase.WATER, water)):
            if any(volume > 0.0 for volume in volumes):
                stored.append(
                    Forecast(
                        prop_id=str(row["prop_id"]),
                        model=str(row["model"]),
                        phase=phase,
                        start=start,
                        type_curve=None,
                        rate_init=None,
                        decline_init=None,
                        b_factor=None,
                        decline_min=None,
                        monthly_volumes=volumes,
                    )
                )
    return stored


def price_models(conn: sqlite3.Connection, name: str | None = None) -> list[PriceModel]:
    """Return price model segments, optionally filtered by name."""
    where = "" if name is None else "WHERE price_model_name = ?"
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT price_model_name, start_date, oil, gas, ngl
        FROM PriceModel
        {where}
        ORDER BY price_model_name, start_date
        """,
        params,
    )
    return [
        PriceModel(
            name=str(row["price_model_name"]),
            start_date=_date_from_db(row["start_date"], field="PriceModel.start_date"),
            oil=float(row["oil"]),
            gas=float(row["gas"]),
            ngl=float(row["ngl"]),
        )
        for row in rows
    ]


def expense_models(conn: sqlite3.Connection, name: str | None = None) -> list[ExpenseModel]:
    """Return expense model segments, optionally filtered by name."""
    where = _shared_model_where("exp_model_name", name)
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT exp_model_name, model_type, fixed_monthly, variable_oil, variable_gas, variable_water
        FROM ExpenseModel
        {where}
        ORDER BY exp_model_name, model_type
        """,
        params,
    )
    return [
        ExpenseModel(
            name=str(row["exp_model_name"]),
            kind=kind,
            fixed_monthly=float(row["fixed_monthly"]),
            variable_oil=float(row["variable_oil"]),
            variable_gas=float(row["variable_gas"]),
            variable_water=float(row["variable_water"]),
            age_months=age_months,
            effective_date=effective_date,
        )
        for row in rows
        for kind, age_months, effective_date in [_expense_kind(row["model_type"])]
    ]


def tax_models(conn: sqlite3.Connection, name: str | None = None) -> list[TaxModel]:
    """Return tax model segments, optionally filtered by name."""
    where = _shared_model_where("tax_model_name", name)
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT tax_model_name, model_type, sev_tax_oil, sev_tax_gas, sev_tax_ngl,
               {tax_ad_valorem_column(conn)} AS ad_valorem_tax
        FROM TaxModel
        {where}
        ORDER BY tax_model_name, model_type
        """,
        params,
    )
    return [
        TaxModel(
            name=str(row["tax_model_name"]),
            kind=kind,
            sev_tax_oil=float(row["sev_tax_oil"]),
            sev_tax_gas=float(row["sev_tax_gas"]),
            sev_tax_ngl=float(row["sev_tax_ngl"]),
            ad_valorem_tax=float(row["ad_valorem_tax"]),
            age_months=age_months,
            effective_date=effective_date,
        )
        for row in rows
        for kind, age_months, effective_date in [_tax_kind(row["model_type"])]
    ]


def diff_models(conn: sqlite3.Connection, name: str | None = None) -> list[DiffModel]:
    """Return differential model segments, optionally filtered by name."""
    where = _shared_model_where("diff_model_name", name)
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT diff_model_name, start_date, oil_diff_method, oil_diff, gas_diff_method, gas_diff,
               ngl_diff_method, ngl_diff
        FROM DiffModel
        {where}
        ORDER BY diff_model_name, start_date
        """,
        params,
    )
    return [
        DiffModel(
            name=str(row["diff_model_name"]),
            start_date=_date_from_db(row["start_date"], field="DiffModel.start_date"),
            oil_method=_enum_from_db(
                DiffType, row["oil_diff_method"], field="DiffModel.oil_diff_method"
            ),
            oil_diff=float(row["oil_diff"]),
            gas_method=_enum_from_db(
                DiffType, row["gas_diff_method"], field="DiffModel.gas_diff_method"
            ),
            gas_diff=float(row["gas_diff"]),
            ngl_method=_enum_from_db(
                DiffType, row["ngl_diff_method"], field="DiffModel.ngl_diff_method"
            ),
            ngl_diff=float(row["ngl_diff"]),
        )
        for row in rows
    ]


def shrink_yield_models(
    conn: sqlite3.Connection, name: str | None = None
) -> list[ShrinkYieldModel]:
    """Return shrink/yield models, optionally filtered by name."""
    where = _shared_model_where("shrink_yield_model_name", name)
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT shrink_yield_model_name, gas_shrink_frac, ngl_yield_bbl_mmscf
        FROM ShrinkYieldModel
        {where}
        ORDER BY shrink_yield_model_name
        """,
        params,
    )
    return [
        ShrinkYieldModel(
            name=str(row["shrink_yield_model_name"]),
            gas_shrink_frac=float(row["gas_shrink_frac"]),
            ngl_yield_bbl_mmscf=float(row["ngl_yield_bbl_mmscf"]),
        )
        for row in rows
    ]


def well_models(
    conn: sqlite3.Connection, prop_id: str | None = None, scenario: str | None = None
) -> list[WellModels]:
    """Return per-well scenario model assignments."""
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if scenario is not None:
        clauses.append("scenario = ?")
        params.append(scenario)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _query(
        conn,
        f"""
        SELECT prop_id, scenario, exp_model_name, capex_model_name, diff_model_name,
               tax_model_name, shrink_yield_model_name, interest_model_name
        FROM WellModels
        {where}
        ORDER BY scenario, prop_id
        """,
        tuple(params),
    )
    return [
        WellModels(
            prop_id=str(row["prop_id"]),
            scenario=str(row["scenario"]),
            expense_model=str(row["exp_model_name"] or ""),
            capex_model=str(row["capex_model_name"] or ""),
            diff_model=str(row["diff_model_name"] or ""),
            tax_model=str(row["tax_model_name"] or ""),
            shrink_yield_model=str(row["shrink_yield_model_name"] or ""),
            interest_model=str(row["interest_model_name"] or ""),
        )
        for row in rows
    ]


def interest(
    conn: sqlite3.Connection, prop_id: str | None = None, model: str | None = None
) -> list[Interest]:
    """Return interest schedule rows."""
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _query(
        conn,
        f"""
        SELECT prop_id, model, start, wi_pct, nri_pct
        FROM Interest
        {where}
        ORDER BY prop_id, model, start
        """,
        tuple(params),
    )
    return [
        Interest(
            prop_id=str(row["prop_id"]),
            model=str(row["model"]),
            start=_date_from_db(row["start"], field="Interest.start"),
            wi_pct=_number(row["wi_pct"]),
            nri_pct=_number(row["nri_pct"]),
        )
        for row in rows
    ]


def capex(
    conn: sqlite3.Connection, prop_id: str | None = None, model: str | None = None
) -> list[Capex]:
    """Return capex schedule rows."""
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _query(
        conn,
        f"""
        SELECT prop_id, model, date, job_type, cost_gross, description
        FROM Capex
        {where}
        ORDER BY prop_id, model, date
        """,
        tuple(params),
    )
    return [
        Capex(
            prop_id=str(row["prop_id"]),
            model=str(row["model"]),
            date=_date_from_db(row["date"], field="Capex.date"),
            job_type=_enum_from_db(CapexJobType, row["job_type"], field="Capex.job_type"),
            cost_gross=_number(row["cost_gross"]),
            description=str(row["description"] or ""),
        )
        for row in rows
    ]


def abandonment(
    conn: sqlite3.Connection, prop_id: str | None = None, model: str | None = None
) -> list[Abandonment]:
    """Return abandonment cost rows."""
    clauses: list[str] = []
    params: list[object] = []
    if prop_id is not None:
        clauses.append("prop_id = ?")
        params.append(prop_id)
    if model is not None:
        clauses.append("model = ?")
        params.append(model)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = _query(
        conn,
        f"""
        SELECT prop_id, model, cost_gross
        FROM Abandonment
        {where}
        ORDER BY prop_id, model
        """,
        tuple(params),
    )
    return [
        Abandonment(
            prop_id=str(row["prop_id"]),
            model=str(row["model"]),
            cost_gross=_number(row["cost_gross"]),
        )
        for row in rows
    ]


def scenarios(conn: sqlite3.Connection, name: str | None = None) -> list[Scenario]:
    """Return scenario rows, optionally filtered by name."""
    where = "" if name is None else "WHERE scenario = ?"
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT scenario, forecast_model_name, price_model_name
        FROM Scenario
        {where}
        ORDER BY scenario
        """,
        params,
    )
    return [
        Scenario(
            name=str(row["scenario"]),
            forecast_model=str(row["forecast_model_name"] or ""),
            price_model=str(row["price_model_name"] or ""),
        )
        for row in rows
    ]


# What Obsidian holds its three presets to, whatever their rows say:
# (cutoff, abandonment delay in months, after-tax on).
_PRESETS: dict[str, tuple[CutoffMethod, int, bool]] = {
    "STANDARD": (CutoffMethod.ECON_LIMIT, 6, False),
    "LOSSOK": (CutoffMethod.LOSS_OK, 0, False),
    "STANDARD-AFIT": (CutoffMethod.ECON_LIMIT, 6, True),
}
# The effective date Obsidian falls back to for one it cannot read.
_EFFECTIVE_DATE_FALLBACK = date(2024, 7, 1)


def run_settings(conn: sqlite3.Connection, name: str | None = None) -> list[RunSettings]:
    """Return run settings, optionally filtered by name.

    Each row is read the way Obsidian's own loader reads it, fallbacks
    included, so the answer is what Obsidian runs with and no stored value
    fails the read: text it does not know becomes the economic limit, no
    delay, a 21% rate, seven years, intangibles expensed; a date it cannot
    read becomes July 2024; and the three presets take the choices Obsidian
    holds them to.
    """
    where = "" if name is None else "WHERE settings_name = ?"
    params: tuple[object, ...] = () if name is None else (name,)
    columns = _columns(conn, "RunSettings")
    # The after-tax columns were added in early 2026; an older file has none.
    after_tax = ", ".join(
        column if column in columns else f"NULL AS {column}"
        for column in (
            "afit_enabled",
            "fed_corp_tax_rate",
            "tangible_recovery_period_yrs",
            "idc_method",
        )
    )
    rows = _query(
        conn,
        f"""
        SELECT settings_name, effective_date, cutoff_method, abandon_timing, {after_tax}
        FROM RunSettings
        {where}
        ORDER BY settings_name
        """,
        params,
    )
    settings: list[RunSettings] = []
    for row in rows:
        settings_name = str(row["settings_name"])
        cutoff_method, cutoff_delay = _cutoff(row["cutoff_method"])
        abandonment_delay = _abandonment_delay(row["abandon_timing"])
        # Obsidian reads the flag as an integer; anything else is off.
        enabled = isinstance(row["afit_enabled"], int) and row["afit_enabled"] != 0
        rate: float | None = _tax_rate_pct(row["fed_corp_tax_rate"]) if enabled else None
        years: int | None = (
            _recovery_years(row["tangible_recovery_period_yrs"]) if enabled else None
        )
        idc: IdcMethod | None = _idc_method(row["idc_method"]) if enabled else None
        preset = _PRESETS.get(settings_name)
        if preset is not None:
            cutoff_method, abandonment_delay, forced_after_tax = preset
            cutoff_delay = 0
            if forced_after_tax:
                enabled, rate, years, idc = True, 21.0, 7, IdcMethod.EXPENSE_IMMEDIATELY
        effective = parse_date(row["effective_date"]) or _EFFECTIVE_DATE_FALLBACK
        settings.append(
            RunSettings(
                name=settings_name,
                effective_date=effective.replace(day=1),
                cutoff_method=cutoff_method,
                cutoff_delay_months=cutoff_delay,
                abandonment_delay_months=abandonment_delay,
                after_tax=enabled,
                federal_tax_rate_pct=rate,
                tangible_recovery_years=years,
                idc_method=idc,
            )
        )
    return settings


# The longest build-up and bridge Obsidian's type curve allows, in months.
_TYPE_CURVE_MONTHS_MAX = 100


def type_curves(conn: sqlite3.Connection, name: str | None = None) -> list[TypeCurve]:
    """Return saved type curves, one row per curve and phase, optionally by name.

    Each row is read the way Obsidian's loader reads it: month counts rounded
    and held to 0 to 100, declines and the b-factor held to the range the Type
    Curve panel allows, and a row Obsidian does not load (a phase other than
    OIL, GAS or WATER, or a rate that is not a number) left out.
    """
    where = "" if name is None else "WHERE name = ?"
    params: tuple[object, ...] = () if name is None else (name,)
    columns = _columns(conn, "TypeCurve")
    # How a curve was fitted and who saved it were added in September 2026.
    stamp = ", ".join(
        column if column in columns else f"NULL AS {column}"
        for column in ("fit_basis", "saved_by", "saved_at")
    )
    rows = _query(
        conn,
        f"""
        SELECT name, phase, normalization, start_rate, build_up_months, initial_rate,
               bridge_months, bridge_decline, initial_decline, b_factor, minimum_decline,
               ref_apis, {stamp}
        FROM TypeCurve
        {where}
        ORDER BY name, phase
        """,
        params,
    )
    curves: list[TypeCurve] = []
    for row in rows:
        phase = {"OIL": Phase.OIL, "GAS": Phase.GAS, "WATER": Phase.WATER}.get(str(row["phase"]))
        rate_start, rate_init = row["start_rate"], row["initial_rate"]
        if phase is None or not _is_number(rate_start) or not _is_number(rate_init):
            LOGGER.warning(
                "type curve %r, phase %r, skipped, as Obsidian skips it", row["name"], row["phase"]
            )
            continue
        fit_pxx, fit_kind, fit_forecasts_from = _fit_basis(row["fit_basis"])
        saved_by = row["saved_by"].strip() if isinstance(row["saved_by"], str) else ""
        curves.append(
            TypeCurve(
                name=str(row["name"]),
                phase=phase,
                normalization_lateral_length_ft=_normalization(row["normalization"]),
                rate_start=float(rate_start),
                build_up_months=_month_count(row["build_up_months"]),
                rate_init=float(rate_init),
                bridge_months=_month_count(row["bridge_months"]),
                decline_bridge=_held(row["bridge_decline"], -0.999, 0.999),
                decline_init=_held(row["initial_decline"], -0.999, 0.999),
                b_factor=_held(row["b_factor"], 0.0, 5.0),
                decline_min=_held(row["minimum_decline"], 0.0, 0.999),
                analog_apis=tuple(
                    api.strip() for api in str(row["ref_apis"] or "").split(",") if api.strip()
                ),
                fit_pxx=fit_pxx,
                fit_kind=fit_kind,
                fit_forecasts_from=fit_forecasts_from,
                saved_by=saved_by or None,
                saved_at=_timestamp(row["saved_at"]),
            )
        )
    return curves


def model_field_refs(
    conn: sqlite3.Connection, kind: ModelKind | None = None, name: str | None = None
) -> list[ModelFieldRef]:
    """Return the fields of shared econ models that are read per well.

    A file without the EconModelFieldRefs table predates them and has none.
    """
    try:
        return _field_refs.read(conn, kind, name)
    except sqlite3.OperationalError as exc:
        raise DataIntegrityError(f"SQLite operation failed for reader query: {exc}") from exc


def surveys(conn: sqlite3.Connection, prop_id: str | None = None) -> list[SurveyPoint]:
    """Return directional survey rows."""
    where = "" if prop_id is None else "WHERE prop_id = ?"
    params: tuple[object, ...] = () if prop_id is None else (prop_id,)
    rows = _query(
        conn,
        f"""
        SELECT prop_id, point_md, point_tvd, azimuth_angle, inclination_angle,
               deviation_ns, deviation_ew
        FROM Survey
        {where}
        ORDER BY prop_id, point_md
        """,
        params,
    )
    return [
        SurveyPoint(
            prop_id=str(row["prop_id"]),
            point_md=float(row["point_md"]),
            point_tvd=float(row["point_tvd"]),
            azimuth_angle=float(row["azimuth_angle"]),
            inclination_angle=float(row["inclination_angle"]),
            deviation_ns=float(row["deviation_ns"]),
            deviation_ew=float(row["deviation_ew"]),
        )
        for row in rows
    ]


def reservoirs(conn: sqlite3.Connection, prop_id: str | None = None) -> list[Reservoir]:
    """Return reservoir top and thickness rows."""
    where = "" if prop_id is None else "WHERE prop_id = ?"
    params: tuple[object, ...] = () if prop_id is None else (prop_id,)
    rows = _query(
        conn,
        f"""
        SELECT prop_id, reservoir, top_depth_ft, gross_thickness_ft
        FROM Reservoir
        {where}
        ORDER BY prop_id, reservoir
        """,
        params,
    )
    return [
        Reservoir(
            prop_id=str(row["prop_id"]),
            reservoir=str(row["reservoir"]),
            top_depth_ft=_optional_float(row["top_depth_ft"], field="Reservoir.top_depth_ft"),
            thickness_ft=_optional_float(
                row["gross_thickness_ft"], field="Reservoir.gross_thickness_ft"
            ),
        )
        for row in rows
    ]


def completions(conn: sqlite3.Connection, prop_id: str | None = None) -> list[Completion]:
    """Return completion rows."""
    where = "" if prop_id is None else "WHERE prop_id = ?"
    params: tuple[object, ...] = () if prop_id is None else (prop_id,)
    rows = _query(
        conn,
        f"""
        SELECT prop_id, frac_proppant_lb, frac_fluid_bbl, frac_stages
        FROM Completion
        {where}
        ORDER BY prop_id
        """,
        params,
    )
    return [
        Completion(
            prop_id=str(row["prop_id"]),
            frac_proppant_lb=_optional_float(
                row["frac_proppant_lb"], field="Completion.frac_proppant_lb"
            ),
            frac_fluid_bbl=_optional_float(
                row["frac_fluid_bbl"], field="Completion.frac_fluid_bbl"
            ),
            frac_stages=_optional_int(row["frac_stages"], field="Completion.frac_stages"),
        )
        for row in rows
    ]


def perfs(conn: sqlite3.Connection, prop_id: str | None = None) -> list[Perfs]:
    """Return perforation interval rows."""
    where = "" if prop_id is None else "WHERE prop_id = ?"
    params: tuple[object, ...] = () if prop_id is None else (prop_id,)
    rows = _query(
        conn,
        f"""
        SELECT prop_id, perf_start_md_ft, perf_end_md_ft, producing
        FROM Perfs
        {where}
        ORDER BY prop_id, perf_start_md_ft
        """,
        params,
    )
    return [
        Perfs(
            prop_id=str(row["prop_id"]),
            perf_start_md_ft=_optional_float(
                row["perf_start_md_ft"], field="Perfs.perf_start_md_ft"
            ),
            perf_end_md_ft=_optional_float(row["perf_end_md_ft"], field="Perfs.perf_end_md_ft"),
            producing=bool(row["producing"]),
        )
        for row in rows
    ]


# Whole-text spellings Obsidian reads before it looks for the codes below.
_RSV_CAT_WORDS: dict[str, RsvCat] = {
    "producing": RsvCat.PDP,
    "completed": RsvCat.PDP,
    "active": RsvCat.PDP,
    "active producer": RsvCat.PDP,
    "proved developed non-producing": RsvCat.PDNP,
    "proved developed nonproducing": RsvCat.PDNP,
    "proven developed non-producing": RsvCat.PDNP,
    "proven developed nonproducing": RsvCat.PDNP,
    "proved developed behind pipe": RsvCat.PDNP,
    "proved developed behind-pipe": RsvCat.PDNP,
    "bp-prov": RsvCat.PDNP,
    "drilled": RsvCat.DUC,
    "drilling": RsvCat.DUC,
    "completed - not active": RsvCat.DUC,
    "permitted": RsvCat.PUD,
    "t/a": RsvCat.TA,
    "p & a": RsvCat.PA,
    "swd": RsvCat.SWD,
    "expired permit": RsvCat.LOC,
}

# Then the first of these codes the text contains, in Obsidian's order: a
# shut-in marker wins in a combined code such as "PDP_SI", and PDP is tried
# late so that it does. The middle text, when not empty, rules the code out
# ("ta" inside "data" is not temporarily abandoned).
_RSV_CAT_CODES: tuple[tuple[tuple[str, ...], str, RsvCat], ...] = (
    (("si", "shut", "inactive"), "", RsvCat.SHUT_IN),
    (("pdnp", "dnp", "pnp", "pdbp"), "", RsvCat.PDNP),
    (("np",), "", RsvCat.SHUT_IN),
    (("ta",), "data", RsvCat.TA),
    (("pa", "p&a", "plug", "abandon"), "", RsvCat.PA),
    (("duc", "comp"), "", RsvCat.DUC),
    (("pperm",), "", RsvCat.PROB),
    (("pud", "wip", "perm"), "", RsvCat.PUD),
    (("loc", "und"), "", RsvCat.LOC),
    (("prob",), "", RsvCat.PROB),
    (("poss",), "", RsvCat.POSS),
    (("data",), "", RsvCat.DATA),
    (("pdp", "pvpd"), "", RsvCat.PDP),
    (("swd", "salt", "water"), "", RsvCat.SWD),
)


def _rsv_cat_from_db(value: object) -> RsvCat:
    """Read a stored reserve category the way Obsidian does.

    Obsidian writes one of thirteen spellings, and reads any text: a file built
    by a conversion or another tool can hold "SI" or "1PDP", which Obsidian
    files as shut-in and PDP. Text it cannot place is ``RsvCat.UNSPECIFIED``.
    """
    text = "" if value is None else str(value)
    try:
        return RsvCat(text)
    except ValueError:
        pass
    code = text.strip().lower()
    if code in _RSV_CAT_WORDS:
        return _RSV_CAT_WORDS[code]
    for needles, unless, rsv_cat in _RSV_CAT_CODES:
        if any(needle in code for needle in needles) and not (unless and unless in code):
            return rsv_cat
    return RsvCat.UNSPECIFIED


def _well_from_row(row: sqlite3.Row) -> Well:
    return Well(
        prop_id=str(row["prop_id"]),
        api_10=_optional_str(row["api_10"]),
        rsv_cat=_rsv_cat_from_db(row["rsv_cat"]),
        lease=_optional_str(row["lease"]),
        well_number=_optional_str(row["well_number"]),
        field=_optional_str(row["field"]),
        operator=_optional_str(row["operator"]),
        category=_optional_str(row["category"]),
        group=_optional_str(row["group"]),
        reservoir=_optional_str(row["reservoir"]),
        tvd_ft=_optional_float(row["tvd"], field="Main.tvd"),
        md_ft=_optional_float(row["md"], field="Main.md"),
        lateral_length_ft=_optional_float(row["lateral_length"], field="Main.lateral_length"),
        spud_date=_optional_date(row["spud"], field="Main.spud"),
        completion_date=_optional_date(row["completion"], field="Main.completion"),
        first_production_date=_optional_date(row["first_prod"], field="Main.first_prod"),
        state=_optional_str(row["state"]),
        county=_optional_str(row["county"]),
        surface_latitude=_optional_float(row["surface_latitude"], field="Main.surface_latitude"),
        surface_longitude=_optional_float(row["surface_longitude"], field="Main.surface_longitude"),
        bottom_hole_latitude=_optional_float(row["bh_latitude"], field="Main.bh_latitude"),
        bottom_hole_longitude=_optional_float(row["bh_longitude"], field="Main.bh_longitude"),
    )


def _query(
    conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()
) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(query, params).fetchall())
    except sqlite3.OperationalError as exc:
        table = missing_table_name(exc)
        if table is not None:
            raise MissingTableError(table) from exc
        raise DataIntegrityError(f"SQLite operation failed for reader query: {exc}") from exc


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The table's column names; empty when the file has no such table."""
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _is_number(value: object) -> bool:
    """Whether SQLite stored a number (not text, a blob or NULL)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _digits(text: str) -> int:
    """The number spelled by the ASCII digits in the text; 0 when there are
    none or they do not fit Obsidian's 32-bit count."""
    digits = "".join(character for character in text if character in "0123456789")
    number = int(digits) if digits else 0
    return number if number <= 0xFFFFFFFF else 0


def _cutoff(value: object) -> tuple[CutoffMethod, int]:
    text = str(value or "").lower()
    if text == "lastpositive":
        return CutoffMethod.LAST_POSITIVE, 0
    if text == "lossok":
        return CutoffMethod.LOSS_OK, 0
    if "econlimitdelaymo" in text:
        return CutoffMethod.ECON_LIMIT_DELAY, _digits(text)
    return CutoffMethod.ECON_LIMIT, 0


def _abandonment_delay(value: object) -> int:
    text = str(value or "")
    if "MonthDelay" in text:
        return _digits(text)
    if "YearDelay" in text:
        return _digits(text) * 12
    return 0


def _tax_rate_pct(value: object) -> float:
    """A stored percentage, or Obsidian's 21 for one it cannot use."""
    if _is_number(value) and 0.0 <= float(str(value)) <= 100.0:
        return float(str(value))
    return 21.0


def _recovery_years(value: object) -> int:
    """A stored whole number of years, or Obsidian's 7 for anything else."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
        return value
    return 7


def _idc_method(value: object) -> IdcMethod:
    text = str(value or "").strip().lower()
    for method in IdcMethod:
        if method.value.lower() == text:
            return method
    return IdcMethod.EXPENSE_IMMEDIATELY


def _number(value: object, *, default: float = 0.0) -> float:
    """A stored number, or the default when the cell is empty or not a number."""
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def _month_count(value: object) -> int:
    """A type curve's month count as Obsidian uses it: a number or numeric
    text, rounded half away from zero and held to 0 through 100; 0 for
    anything else."""
    if isinstance(value, bytes) or value is None:
        return 0
    number = _number(value, default=math.nan)
    if not math.isfinite(number) or number <= 0.0:
        return 0
    return min(math.floor(number + 0.5), _TYPE_CURVE_MONTHS_MAX)


def _held(value: object, low: float, high: float) -> float:
    """A stored decline or b-factor held to the range Obsidian holds it to; 0
    when it is not a number at all."""
    number = float(str(value)) if _is_number(value) else 0.0
    if not math.isfinite(number):
        return 0.0
    return min(max(number, low), high)


def _normalization(value: object) -> float | None:
    """The lateral length a type curve is normalized to, None when it is not
    (or when Obsidian could not read the length, and so does not normalize)."""
    text = str(value or "").upper()
    if "LATERALLENGTH" not in text:
        return None
    inner = text.removeprefix("LATERALLENGTH(").rstrip(")")
    if inner != inner.strip() or "_" in inner:
        return None
    try:
        length = float(inner)
    except ValueError:
        return None
    return length if math.isfinite(length) else None


def _fit_basis(value: object) -> tuple[int | None, TypeCurveFitKind | None, str | None]:
    """A type curve's ``fit_basis``: ``P65|AUTO|MAIN``, the model last and optional."""
    parts = str(value or "").strip().split("|", 2)
    if len(parts) < 2 or parts[0].strip()[:1].lower() != "p":
        return None, None, None
    kinds = {"auto": TypeCurveFitKind.AUTO, "custom": TypeCurveFitKind.CUSTOM}
    kind = kinds.get(parts[1].strip().lower())
    percentile = parts[0].strip()[1:]
    if kind is None or not percentile or any(c not in "0123456789" for c in percentile):
        return None, None, None
    if len(percentile) > 3 or int(percentile) > 100:
        return None, None, None
    model = parts[2].strip() if len(parts) == 3 else ""
    return int(percentile), kind, model or None


def _timestamp(value: object) -> datetime | None:
    """Whole seconds since 1970 as a UTC time; None for anything else."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


# Obsidian databases may store missing optional values as empty strings rather
# than SQL NULL, so every optional read must treat "" as absent.
def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_float(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise DataIntegrityError(f"{field} has non-numeric value {value!r}") from exc


def _optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise DataIntegrityError(f"{field} has non-integer value {value!r}") from exc
    if not number.is_integer():
        raise DataIntegrityError(f"{field} has non-integer value {value!r}")
    return int(number)


def _optional_date(value: object, *, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _date_from_db(value, field=field)


def _date_from_db(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise DataIntegrityError(f"{field} must be stored as ISO date text; got {value!r}")
    parsed = parse_date(value)
    if parsed is None:
        raise DataIntegrityError(f"{field} has invalid ISO date value {value!r}")
    return parsed


# Obsidian databases store Monthly.month as 6-digit YYYYMM text (e.g. '202306').
# ISO dates are also accepted for compatibility with rows written by older
# versions of this library.
def _month_from_db(value: object, *, field: str) -> date:
    if isinstance(value, str) and len(value) == 6 and value.isdigit():
        month = int(value[4:6])
        if not 1 <= month <= 12:
            raise DataIntegrityError(f"{field} has invalid YYYYMM value {value!r}")
        return date(int(value[:4]), month, 1)
    return _date_from_db(value, field=field)


def _enum_from_db(enum_cls: type[EnumT], value: object, *, field: str) -> EnumT:
    try:
        return enum_cls(str(value))
    except ValueError as exc:
        raise DataIntegrityError(f"{field} has unrecognized value {value!r}") from exc


def _expense_kind(value: object) -> tuple[ExpenseModelKind, int | None, date | None]:
    kind, age_months, effective_date = _model_kind(str(value), field="ExpenseModel.model_type")
    return ExpenseModelKind(kind.value), age_months, effective_date


def _tax_kind(value: object) -> tuple[TaxModelKind, int | None, date | None]:
    kind, age_months, effective_date = _model_kind(str(value), field="TaxModel.model_type")
    return TaxModelKind(kind.value), age_months, effective_date


def _model_kind(value: str, *, field: str) -> tuple[ExpenseModelKind, int | None, date | None]:
    if value == ExpenseModelKind.SIMPLE.value:
        return ExpenseModelKind.SIMPLE, None, None
    if value.startswith("AGEBASED(") and value.endswith(")"):
        try:
            return ExpenseModelKind.AGE_BASED, int(value[9:-1]), None
        except ValueError as exc:
            raise DataIntegrityError(f"{field} has invalid age-based value {value!r}") from exc
    if value.startswith("DATEBASED(") and value.endswith(")"):
        return ExpenseModelKind.DATE_BASED, None, _date_from_db(value[10:-1], field=field)
    raise DataIntegrityError(f"{field} has unrecognized value {value!r}")
