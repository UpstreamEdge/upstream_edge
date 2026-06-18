"""Immutable dataclasses returned by readers and accepted by writers."""

from dataclasses import dataclass
from datetime import date

from .enums import (
    AttributeType,
    CapexJobType,
    DiffType,
    ExpenseModelKind,
    Phase,
    RsvCat,
    TaxModelKind,
)


@dataclass(frozen=True, slots=True)
class Well:
    """Well header row from the Main table."""

    prop_id: str
    api_10: str | None
    rsv_cat: RsvCat
    lease: str | None
    well_number: str | None
    field: str | None
    operator: str | None
    category: str | None
    group: str | None
    reservoir: str | None
    tvd_ft: float | None
    md_ft: float | None
    lateral_length_ft: float | None
    spud: date | None
    completion: date | None
    first_prod: date | None
    state: str | None
    county: str | None
    surface_latitude: float | None
    surface_longitude: float | None
    bh_latitude: float | None
    bh_longitude: float | None

    @property
    def well_name(self) -> str:
        """Return the display name derived from lease and well number.

        Example:
            >>> Well('P1', None, RsvCat.PUD, 'MITCHELL', '1H', None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None).well_name
            'MITCHELL 1H'
        """
        if self.lease and self.well_number:
            return f"{self.lease} {self.well_number}"
        return self.lease or self.well_number or ""


@dataclass(frozen=True, slots=True)
class MonthlyRow:
    """Monthly production row keyed by PropID and first-of-month date."""

    prop_id: str
    month: date
    oil_bbl: float | None
    gas_mscf: float | None
    water_bbl: float | None


@dataclass(frozen=True, slots=True)
class DailyRow:
    """Daily production row keyed by PropID and date."""

    prop_id: str
    date: date
    oil_bopd: float | None
    gas_mcfd: float | None
    water_bwpd: float | None


@dataclass(frozen=True, slots=True)
class Forecast:
    """Forecast segment row.

    Segments driven by a type curve carry the curve name in ``type_curve`` and
    may have no decline parameters of their own; the rate fields are then None.
    """

    prop_id: str
    model: str
    phase: Phase
    start: date
    type_curve: str | None
    rate_init: float | None
    decline_init: float | None
    b_factor: float | None
    decline_min: float | None


@dataclass(frozen=True, slots=True)
class ForecastSegment:
    """Input segment for a forecast."""

    start: date
    rate_init: float
    decline_init: float
    b_factor: float
    decline_min: float
    type_curve: str | None = None


@dataclass(frozen=True, slots=True)
class PriceModel:
    """Price model segment."""

    name: str
    start_date: date
    oil: float
    gas: float
    ngl: float


@dataclass(frozen=True, slots=True)
class PriceModelSegment:
    """Input segment for a price model."""

    start_date: date
    oil: float
    gas: float
    ngl: float


@dataclass(frozen=True, slots=True)
class ExpenseModel:
    """Expense model segment."""

    name: str
    kind: ExpenseModelKind
    fixed_monthly: float
    variable_oil: float
    variable_gas: float
    variable_water: float
    age_months: int | None = None
    effective_date: date | None = None


@dataclass(frozen=True, slots=True)
class ExpenseModelSegment:
    """Input segment for an expense model."""

    kind: ExpenseModelKind
    fixed_monthly: float
    variable_oil: float
    variable_gas: float
    variable_water: float
    age_months: int | None = None
    effective_date: date | None = None


@dataclass(frozen=True, slots=True)
class TaxModel:
    """Tax model segment."""

    name: str
    kind: TaxModelKind
    sev_tax_oil: float
    sev_tax_gas: float
    sev_tax_ngl: float
    ad_valorum_tax: float
    age_months: int | None = None
    effective_date: date | None = None


@dataclass(frozen=True, slots=True)
class TaxModelSegment:
    """Input segment for a tax model."""

    kind: TaxModelKind
    sev_tax_oil: float
    sev_tax_gas: float
    sev_tax_ngl: float
    ad_valorum_tax: float
    age_months: int | None = None
    effective_date: date | None = None


@dataclass(frozen=True, slots=True)
class DiffModel:
    """Differential model segment."""

    name: str
    start_date: date
    oil_method: DiffType
    oil_diff: float
    gas_method: DiffType
    gas_diff: float
    ngl_method: DiffType
    ngl_diff: float


@dataclass(frozen=True, slots=True)
class DiffModelSegment:
    """Input segment for a differential model."""

    start_date: date
    oil_method: DiffType
    oil_diff: float
    gas_method: DiffType
    gas_diff: float
    ngl_method: DiffType
    ngl_diff: float


@dataclass(frozen=True, slots=True)
class ShrinkYieldModel:
    """Shrink and yield model row."""

    name: str
    gas_shrink_frac: float
    ngl_yield_bbl_mmscf: float


@dataclass(frozen=True, slots=True)
class WellModels:
    """Per-well scenario model assignment.

    Holds the per-well model kinds. The scenario-global forecast and price
    models are not here — see :class:`Scenario`.
    """

    prop_id: str
    scenario: str
    exp_model: str
    capex_model: str
    diff_model: str
    tax_model: str
    shrink_yield_model: str
    interest_model: str


@dataclass(frozen=True, slots=True)
class Interest:
    """Working-interest and net-revenue-interest segment.

    ``wi_pct`` and ``nri_pct`` are percentages from 0 to 100 (e.g. 75.0 for 75%).
    """

    prop_id: str
    model: str
    start: date
    wi_pct: float
    nri_pct: float


@dataclass(frozen=True, slots=True)
class InterestSegment:
    """Input segment for an interest schedule.

    ``wi_pct`` and ``nri_pct`` are percentages from 0 to 100 (e.g. 75.0 for 75%).
    """

    start: date
    wi_pct: float
    nri_pct: float


@dataclass(frozen=True, slots=True)
class Capex:
    """Capex schedule item."""

    prop_id: str
    model: str
    date: date
    job_type: CapexJobType
    cost_gross: float
    description: str


@dataclass(frozen=True, slots=True)
class CapexItem:
    """Input item for a capex schedule."""

    date: date
    job_type: CapexJobType
    cost_gross: float
    description: str = ""


@dataclass(frozen=True, slots=True)
class Abandonment:
    """Abandonment cost row."""

    prop_id: str
    model: str
    cost_gross: float


@dataclass(frozen=True, slots=True)
class Scenario:
    """Scenario registry row.

    ``forecast_model`` and ``price_model`` are applied globally to every well in
    the scenario. ``forecast_model`` names the ``model`` used by ``set_forecast``;
    ``price_model`` names a deck from ``set_price_model``. The per-well model
    kinds live in :class:`WellModels`.
    """

    name: str
    forecast_model: str
    price_model: str


@dataclass(frozen=True, slots=True)
class SurveyPoint:
    """Directional survey point."""

    prop_id: str
    point_md: float
    point_tvd: float
    azimuth_angle: float
    inclination_angle: float
    deviation_ns: float
    deviation_ew: float


@dataclass(frozen=True, slots=True)
class SurveyPointInput:
    """Input directional survey point without PropID."""

    point_md: float
    point_tvd: float
    azimuth_angle: float
    inclination_angle: float
    deviation_ns: float
    deviation_ew: float


@dataclass(frozen=True, slots=True)
class Reservoir:
    """Reservoir top and thickness row."""

    prop_id: str
    reservoir: str
    top_depth_ft: float | None
    thickness_ft: float | None


@dataclass(frozen=True, slots=True)
class Completion:
    """Completion summary row."""

    prop_id: str
    frac_proppant_lb: float | None
    frac_fluid_bbl: float | None
    frac_stages: int | None


@dataclass(frozen=True, slots=True)
class Perfs:
    """Perforation interval row."""

    prop_id: str
    perf_start_md_ft: float | None
    perf_end_md_ft: float | None
    producing: bool


@dataclass(frozen=True, slots=True)
class PerfsInput:
    """Input perforation interval without PropID."""

    perf_start_md_ft: float
    perf_end_md_ft: float
    producing: bool


@dataclass(frozen=True, slots=True)
class AttributeColumn:
    """WellAttributes user column descriptor."""

    name: str
    attr_type: AttributeType


@dataclass(frozen=True, slots=True)
class WellAttributeUpdate:
    """Input update for one WellAttributes cell."""

    prop_id: str
    column: str
    value: str | float | date
