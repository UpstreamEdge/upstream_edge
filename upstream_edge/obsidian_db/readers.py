"""Typed read helpers for Obsidian database tables."""

from __future__ import annotations

import sqlite3
from datetime import date
from enum import StrEnum
from typing import TypeVar

from .enums import (
    CapexJobType,
    DiffType,
    ExpenseModelKind,
    Phase,
    RsvCat,
    TaxModelKind,
)
from .exceptions import DataIntegrityError
from .models import (
    Abandonment,
    Capex,
    Completion,
    DailyRow,
    DiffModel,
    ExpenseModel,
    Forecast,
    Interest,
    MonthlyRow,
    Perfs,
    PriceModel,
    Reservoir,
    Scenario,
    ShrinkYieldModel,
    SurveyPoint,
    TaxModel,
    Well,
    WellModels,
)

EnumT = TypeVar("EnumT", bound=StrEnum)


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
            month=_date_from_db(row["month"], field="Monthly.month"),
            oil_bbl=_optional_float(row["oil_monthly_bbl"]),
            gas_mscf=_optional_float(row["gas_monthly_mscf"]),
            water_bbl=_optional_float(row["water_monthly_bbl"]),
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
            oil_bopd=_optional_float(row["oil_bopd"]),
            gas_mcfd=_optional_float(row["gas_mcfd"]),
            water_bwpd=_optional_float(row["water_bwpd"]),
        )
        for row in rows
    ]


def forecasts(
    conn: sqlite3.Connection, prop_id: str | None = None, model: str | None = None
) -> list[Forecast]:
    """Return forecast segments with optional PropID and model filters."""
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
    return [
        Forecast(
            prop_id=str(row["prop_id"]),
            model=str(row["model"]),
            phase=_enum_from_db(Phase, row["phase"], field="Forecast.phase"),
            start=_date_from_db(row["start"], field="Forecast.start"),
            type_curve=_optional_str(row["type_curve"]),
            rate_init=float(row["rate_init"]),
            decline_init=float(row["decline_init"]),
            b_factor=float(row["b_factor"]),
            decline_min=float(row["decline_min"]),
        )
        for row in rows
    ]


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
    where = "" if name is None else "WHERE exp_model_name = ?"
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
    where = "" if name is None else "WHERE tax_model_name = ?"
    params: tuple[object, ...] = () if name is None else (name,)
    rows = _query(
        conn,
        f"""
        SELECT tax_model_name, model_type, sev_tax_oil, sev_tax_gas, sev_tax_ngl, ad_valorum_tax
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
            ad_valorum_tax=float(row["ad_valorum_tax"]),
            age_months=age_months,
            effective_date=effective_date,
        )
        for row in rows
        for kind, age_months, effective_date in [_tax_kind(row["model_type"])]
    ]


def diff_models(conn: sqlite3.Connection, name: str | None = None) -> list[DiffModel]:
    """Return differential model segments, optionally filtered by name."""
    where = "" if name is None else "WHERE diff_model_name = ?"
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
    where = "" if name is None else "WHERE shrink_yield_model_name = ?"
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
            exp_model=str(row["exp_model_name"] or ""),
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
            wi_pct=float(row["wi_pct"]),
            nri_pct=float(row["nri_pct"]),
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
            cost_gross=float(row["cost_gross"]),
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
            cost_gross=float(row["cost_gross"]),
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
            top_depth_ft=float(row["top_depth_ft"]),
            thickness_ft=_optional_float(row["gross_thickness_ft"]),
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
            frac_proppant_lb=_optional_float(row["frac_proppant_lb"]),
            frac_fluid_bbl=_optional_float(row["frac_fluid_bbl"]),
            frac_stages=_optional_int(row["frac_stages"]),
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
            perf_start_md_ft=float(row["perf_start_md_ft"]),
            perf_end_md_ft=float(row["perf_end_md_ft"]),
            producing=bool(row["producing"]),
        )
        for row in rows
    ]


def _well_from_row(row: sqlite3.Row) -> Well:
    try:
        rsv_cat = RsvCat(str(row["rsv_cat"]))
    except ValueError as exc:
        raise DataIntegrityError(f"Main.rsv_cat has unrecognized value {row['rsv_cat']!r}") from exc
    return Well(
        prop_id=str(row["prop_id"]),
        api_10=_optional_str(row["api_10"]),
        rsv_cat=rsv_cat,
        lease=_optional_str(row["lease"]),
        well_number=_optional_str(row["well_number"]),
        field=_optional_str(row["field"]),
        operator=_optional_str(row["operator"]),
        category=_optional_str(row["category"]),
        group=_optional_str(row["group"]),
        reservoir=_optional_str(row["reservoir"]),
        tvd_ft=_optional_float(row["tvd"]),
        md_ft=_optional_float(row["md"]),
        lateral_length_ft=_optional_float(row["lateral_length"]),
        spud=_optional_date(row["spud"], field="Main.spud"),
        completion=_optional_date(row["completion"], field="Main.completion"),
        first_prod=_optional_date(row["first_prod"], field="Main.first_prod"),
        state=_optional_str(row["state"]),
        county=_optional_str(row["county"]),
        surface_latitude=_optional_float(row["surface_latitude"]),
        surface_longitude=_optional_float(row["surface_longitude"]),
        bh_latitude=_optional_float(row["bh_latitude"]),
        bh_longitude=_optional_float(row["bh_longitude"]),
    )


def _query(
    conn: sqlite3.Connection, query: str, params: tuple[object, ...] = ()
) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(query, params).fetchall())
    except sqlite3.OperationalError as exc:
        raise DataIntegrityError(f"SQLite operation failed for reader query: {exc}") from exc


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _optional_date(value: object, *, field: str) -> date | None:
    return None if value is None else _date_from_db(value, field=field)


def _date_from_db(value: object, *, field: str) -> date:
    if not isinstance(value, str):
        raise DataIntegrityError(f"{field} must be stored as ISO date text; got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataIntegrityError(f"{field} has invalid ISO date value {value!r}") from exc


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
