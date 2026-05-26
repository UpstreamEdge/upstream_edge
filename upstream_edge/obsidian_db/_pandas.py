"""Lazy pandas adapter for Database *_df reader siblings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import StrEnum
from typing import Any, TypeVar, cast

from .exceptions import MissingDependencyError

RowT = TypeVar("RowT")


def from_dataclass_rows(rows: list[RowT], row_type: type[RowT]):
    """Build a pandas DataFrame from dataclass rows."""
    pd = _pandas()
    columns = [field.name for field in fields(cast(Any, row_type))]
    data = [_row_dict(row, columns) for row in rows]
    return pd.DataFrame(data, columns=columns)


def from_mapping_rows(rows: Mapping[str, Mapping[str, object]]):
    """Build a pandas DataFrame from PropID-keyed mapping rows."""
    pd = _pandas()
    flattened = [{"prop_id": prop_id, **values} for prop_id, values in rows.items()]
    columns: list[str] = ["prop_id"]
    for values in rows.values():
        for key in values:
            if key not in columns:
                columns.append(key)
    return pd.DataFrame(flattened, columns=columns)


def from_single_mapping(row: Mapping[str, object]):
    """Build a one-row pandas DataFrame from a mapping."""
    pd = _pandas()
    return pd.DataFrame([row], columns=list(row))


def _pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise MissingDependencyError("install with upstream-edge[pandas]") from exc
    return pd


def _row_dict(row: object, columns: list[str]) -> dict[str, object]:
    if not is_dataclass(row):
        raise TypeError("row must be a dataclass instance")
    result: dict[str, object] = {}
    for column in columns:
        value = getattr(row, column)
        result[column] = value.value if isinstance(value, StrEnum) else value
    return result
