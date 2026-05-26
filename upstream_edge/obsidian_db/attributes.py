"""WellAttributes dynamic-schema readers and helpers."""

from __future__ import annotations

import sqlite3
from datetime import date

from ._sql import quote_identifier
from .enums import AttributeType
from .exceptions import DataIntegrityError, WellNotFoundError
from .models import AttributeColumn
from .types import AttributeValue


def list_attribute_columns(conn: sqlite3.Connection) -> list[AttributeColumn]:
    """Return user-defined WellAttributes columns in table order."""
    return [
        AttributeColumn(name=name, attr_type=attr_type)
        for name, attr_type in _attribute_types(conn).items()
    ]


def well_attributes(conn: sqlite3.Connection, prop_id: str) -> dict[str, AttributeValue]:
    """Return one well's WellAttributes values."""
    attr_types = _attribute_types(conn)
    row = conn.execute(
        f"SELECT * FROM {quote_identifier('WellAttributes')} WHERE prop_id = ?", (prop_id,)
    ).fetchone()
    if row is None:
        raise WellNotFoundError(
            prop_id,
            f"well_attributes(prop_id={prop_id!r}): no WellAttributes row exists for PropID",
        )
    return {
        name: _attribute_value(row[name], attr_type=attr_type, column=name)
        for name, attr_type in attr_types.items()
    }


def all_well_attributes(conn: sqlite3.Connection) -> dict[str, dict[str, AttributeValue]]:
    """Return WellAttributes values for every PropID."""
    attr_types = _attribute_types(conn)
    rows = conn.execute(
        f"SELECT * FROM {quote_identifier('WellAttributes')} ORDER BY prop_id"
    ).fetchall()
    return {
        str(row["prop_id"]): {
            name: _attribute_value(row[name], attr_type=attr_type, column=name)
            for name, attr_type in attr_types.items()
        }
        for row in rows
    }


def _attribute_types(conn: sqlite3.Connection) -> dict[str, AttributeType]:
    try:
        columns = conn.execute("PRAGMA table_info(WellAttributes)").fetchall()
    except sqlite3.OperationalError as exc:
        raise DataIntegrityError(f"could not inspect WellAttributes schema: {exc}") from exc
    result: dict[str, AttributeType] = {}
    for column in columns:
        name = str(column["name"])
        if name.lower() == "prop_id":
            continue
        result[name] = _attribute_type(str(column["type"]), column=name)
    return result


def _attribute_type(sql_type: str, *, column: str) -> AttributeType:
    upper = sql_type.upper()
    if "REAL" in upper or "NUM" in upper or "FLOA" in upper or "DOUB" in upper:
        return AttributeType.NUMERIC
    if "DATE" in upper:
        return AttributeType.DATE
    if "TEXT" in upper or "CHAR" in upper or upper == "":
        return AttributeType.TEXT
    raise DataIntegrityError(f"WellAttributes column {column!r} has unsupported type {sql_type!r}")


def _attribute_value(value: object, *, attr_type: AttributeType, column: str) -> AttributeValue:
    if value is None:
        raise DataIntegrityError(f"WellAttributes column {column!r} contains NULL")
    if attr_type is AttributeType.NUMERIC:
        return float(str(value))
    if attr_type is AttributeType.DATE:
        if not isinstance(value, str):
            raise DataIntegrityError(f"WellAttributes column {column!r} must store DATE as text")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise DataIntegrityError(
                f"WellAttributes column {column!r} has invalid date value {value!r}"
            ) from exc
    return str(value)
