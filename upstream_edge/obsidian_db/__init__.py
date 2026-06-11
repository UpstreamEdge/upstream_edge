"""Typed Python client for Obsidian SQLite databases.

AI agents: an orientation guide ships with this package as ``AGENTS.md``
(in this directory), also available at
https://github.com/UpstreamEdge/upstream_edge/blob/main/AGENTS.md.
Read it before writing code against this library.
"""

import logging

from .database import Database
from .enums import (
    AttributeType,
    CapexJobType,
    DiffType,
    ExpenseModelKind,
    Phase,
    RsvCat,
    TaxModelKind,
)
from .exceptions import (
    DatabaseLockedError,
    DataIntegrityError,
    DuplicateError,
    MissingDependencyError,
    ModelNotFoundError,
    ObsidianDbError,
    ValidationError,
    WellNotFoundError,
)
from .models import (
    Abandonment,
    AttributeColumn,
    Capex,
    CapexItem,
    Completion,
    DailyRow,
    DiffModel,
    DiffModelSegment,
    ExpenseModel,
    ExpenseModelSegment,
    Forecast,
    ForecastSegment,
    Interest,
    InterestSegment,
    MonthlyRow,
    Perfs,
    PerfsInput,
    PriceModel,
    PriceModelSegment,
    Reservoir,
    Scenario,
    ShrinkYieldModel,
    SurveyPoint,
    SurveyPointInput,
    TaxModel,
    TaxModelSegment,
    Well,
    WellAttributeUpdate,
    WellModels,
)
from .types import API10, AttributeValue, PropID

logging.getLogger("upstream_edge.obsidian_db").addHandler(logging.NullHandler())

__all__ = [
    "API10",
    "Abandonment",
    "AttributeColumn",
    "AttributeType",
    "AttributeValue",
    "Capex",
    "CapexItem",
    "CapexJobType",
    "Completion",
    "DailyRow",
    "DataIntegrityError",
    "Database",
    "DatabaseLockedError",
    "DiffModel",
    "DiffModelSegment",
    "DiffType",
    "DuplicateError",
    "ExpenseModel",
    "ExpenseModelKind",
    "ExpenseModelSegment",
    "Forecast",
    "ForecastSegment",
    "Interest",
    "InterestSegment",
    "MissingDependencyError",
    "ModelNotFoundError",
    "MonthlyRow",
    "ObsidianDbError",
    "Perfs",
    "PerfsInput",
    "Phase",
    "PriceModel",
    "PriceModelSegment",
    "PropID",
    "Reservoir",
    "RsvCat",
    "Scenario",
    "ShrinkYieldModel",
    "SurveyPoint",
    "SurveyPointInput",
    "TaxModel",
    "TaxModelKind",
    "TaxModelSegment",
    "ValidationError",
    "Well",
    "WellAttributeUpdate",
    "WellModels",
    "WellNotFoundError",
]
