"""String enums mirroring Obsidian database enum values."""

from enum import StrEnum


class RsvCat(StrEnum):
    """Reserve category stored on a well header."""

    PDP = "PDP"
    PDNP = "PDNP"
    SHUT_IN = "ShutIn"
    DUC = "DUC"
    PUD = "PUD"
    PROB = "PROB"
    POSS = "POSS"
    LOC = "LOC"
    TA = "TA"
    PA = "P&A"
    SWD = "SWD"
    UNSPECIFIED = "Blank"
    DATA = "Data"


class Phase(StrEnum):
    """Production phase for forecasts."""

    OIL = "OIL"
    GAS = "GAS"
    WATER = "WATER"


class ForecastKind(StrEnum):
    """What one row of a forecast is.

    ``DECLINE`` is an Arps decline segment. ``TYPE_CURVE`` is a segment that
    follows a saved type curve. ``MONTHLY_VOLUMES`` is a run of stored monthly
    volumes, which Obsidian uses as they are for the months they cover.
    """

    DECLINE = "decline"
    TYPE_CURVE = "type_curve"
    MONTHLY_VOLUMES = "monthly_volumes"


class CapexJobType(StrEnum):
    """Capital job category used by capex schedules."""

    DRILLING = "Drilling"
    COMPLETIONS = "Completions"
    FACILITIES = "Facilities"
    SURFACE_WORK = "Surface Work"
    PUMP_REPAIR = "Pump Repair"
    TUBING_REPAIR = "Tubing Repair"
    ROD_REPAIR = "Rod Repair"
    CASING_REPAIR = "Casing Repair"
    OTHER = "Other"


class DiffType(StrEnum):
    """Differential method for price adjustments."""

    DOLLAR = "DOLLAR"
    FRACTION = "FRACTION"


class AttributeType(StrEnum):
    """WellAttributes storage type."""

    TEXT = "TEXT"
    NUMERIC = "NUMERIC"
    DATE = "DATE"


class ExpenseModelKind(StrEnum):
    """Expense model segment discriminator."""

    SIMPLE = "SIMPLE"
    AGE_BASED = "AGEBASED"
    DATE_BASED = "DATEBASED"


class TaxModelKind(StrEnum):
    """Tax model segment discriminator."""

    SIMPLE = "SIMPLE"
    AGE_BASED = "AGEBASED"
    DATE_BASED = "DATEBASED"


class ModelKind(StrEnum):
    """Kind of a shared, named econ model."""

    PRICE = "price"
    EXPENSE = "expense"
    TAX = "tax"
    DIFF = "diff"
    SHRINK_YIELD = "shrink_yield"


class CutoffMethod(StrEnum):
    """How a run settings ends a well's economic life.

    ``ECON_LIMIT`` stops when net cash flow turns negative after all capital is
    spent; ``ECON_LIMIT_DELAY`` does the same a set number of months later;
    ``LAST_POSITIVE`` stops at the last month net cash flow is positive;
    ``LOSS_OK`` runs the full forecast.
    """

    ECON_LIMIT = "EconLimit"
    ECON_LIMIT_DELAY = "EconLimitDelayMo"
    LAST_POSITIVE = "LastPositive"
    LOSS_OK = "LossOK"


class IdcMethod(StrEnum):
    """How an after-tax run treats intangible drilling costs."""

    EXPENSE_IMMEDIATELY = "ExpenseImmediately"
    AMORTIZE_60_MONTHS = "Amortize60Months"


class TypeCurveFitKind(StrEnum):
    """Whether a saved type curve is the auto-fit's own or was adjusted by hand."""

    AUTO = "auto"
    CUSTOM = "custom"
