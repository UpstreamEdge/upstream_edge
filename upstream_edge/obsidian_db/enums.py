"""String enums mirroring Obsidian database enum values."""

from enum import StrEnum


class RsvCat(StrEnum):
    """Reserve category stored on a well header."""

    PDP = "PDP"
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
