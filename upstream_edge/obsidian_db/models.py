"""Immutable dataclasses returned by readers and accepted by writers."""

import functools
import operator
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypeVar

from .enums import (
    AttributeType,
    CapexJobType,
    CutoffMethod,
    DiffType,
    ExpenseModelKind,
    ForecastKind,
    IdcMethod,
    ModelKind,
    Phase,
    RsvCat,
    TaxModelKind,
    TypeCurveFitKind,
)

RowT = TypeVar("RowT")


def _also_named(**earlier: str) -> Callable[[type[RowT]], type[RowT]]:
    """Let a row type keep answering to another name for a field.

    Each keyword is that other name and its value the field's name. The other
    name is taken as a constructor keyword and read as an attribute. It is how
    a script written before a field was renamed keeps running.

    Given both names, the other name's value is used. That is the case
    `dataclasses.replace(row, other_name=value)` makes: it passes every field
    as it stands, then the change.
    """

    def wrap(cls: type[RowT]) -> type[RowT]:
        init: Callable[..., None] = cls.__init__

        @functools.wraps(init)
        def __init__(self: RowT, *args: Any, **kwargs: Any) -> None:
            for other, name in earlier.items():
                if other in kwargs:
                    kwargs[name] = kwargs.pop(other)
            init(self, *args, **kwargs)

        setattr(cls, "__init__", __init__)  # noqa: B010
        for other, name in earlier.items():
            setattr(cls, other, property(operator.attrgetter(name)))
        return cls

    return wrap


@_also_named(
    spud="spud_date",
    completion="completion_date",
    first_prod="first_production_date",
    bh_latitude="bottom_hole_latitude",
    bh_longitude="bottom_hole_longitude",
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
    spud_date: date | None
    completion_date: date | None
    first_production_date: date | None
    state: str | None
    county: str | None
    surface_latitude: float | None
    surface_longitude: float | None
    bottom_hole_latitude: float | None
    bottom_hole_longitude: float | None

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


@_also_named(gas_mcf="gas_mscf")
@dataclass(frozen=True, slots=True)
class MonthlyRow:
    """Monthly production row keyed by PropID and first-of-month date. Gas is in Mcf."""

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
    """One row of a well's forecast on a model, for one phase.

    A phase's forecast is its rows in order of ``start``. ``kind`` says what a
    row is:

    - A decline segment carries its Arps numbers (``rate_init`` per day,
      ``decline_init``, ``b_factor``, ``decline_min``).
    - A type-curve segment names the curve in ``type_curve`` and may have no
      numbers of its own; the rate fields are then None.
    - Stored monthly volumes carry ``monthly_volumes``: one volume per calendar
      month from ``start`` (bbl for oil and water, Mcf for gas), with the rate
      fields None. A saved Spatial Forecasting run writes them. Obsidian uses
      them as they are for the months they cover, and the segments carry the
      forecast on after the last one.
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
    monthly_volumes: tuple[float, ...] | None = None

    @property
    def kind(self) -> ForecastKind:
        """Whether the row is a decline segment, a type-curve segment or stored monthly volumes."""
        if self.monthly_volumes is not None:
            return ForecastKind.MONTHLY_VOLUMES
        return ForecastKind.TYPE_CURVE if self.type_curve else ForecastKind.DECLINE


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


@_also_named(ad_valorum_tax="ad_valorem_tax")
@dataclass(frozen=True, slots=True)
class TaxModel:
    """Tax model segment. Every rate is a fraction (0.046 for 4.6%)."""

    name: str
    kind: TaxModelKind
    sev_tax_oil: float
    sev_tax_gas: float
    sev_tax_ngl: float
    ad_valorem_tax: float
    age_months: int | None = None
    effective_date: date | None = None


@_also_named(ad_valorum_tax="ad_valorem_tax")
@dataclass(frozen=True, slots=True)
class TaxModelSegment:
    """Input segment for a tax model. Every rate is a fraction (0.046 for 4.6%)."""

    kind: TaxModelKind
    sev_tax_oil: float
    sev_tax_gas: float
    sev_tax_ngl: float
    ad_valorem_tax: float
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


@_also_named(exp_model="expense_model")
@dataclass(frozen=True, slots=True)
class WellModels:
    """Per-well scenario model assignment.

    Holds the per-well model kinds. The scenario-global forecast and price
    models are not here — see :class:`Scenario`.
    """

    prop_id: str
    scenario: str
    expense_model: str
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
class RunSettings:
    """Run settings: the choices an economics run is made with.

    ``effective_date`` is the first day of the month the economics run from.
    ``cutoff_delay_months`` applies to ``CutoffMethod.ECON_LIMIT_DELAY`` and is 0
    otherwise. ``abandonment_delay_months`` is how long after the cutoff the
    abandonment cost lands (0 is the month after).

    The three after-tax fields are None unless ``after_tax`` is on:
    ``federal_tax_rate_pct`` is a percentage (21.0 for 21%).

    Obsidian keeps three presets and holds them to their choices whatever the
    file says: ``STANDARD`` and ``STANDARD-AFIT`` cut off at the economic limit
    with abandonment six months later, ``LOSSOK`` runs the full forecast with
    no delay, and ``STANDARD-AFIT`` is after tax at 21% over seven years with
    intangibles expensed. They are returned that way.
    """

    name: str
    effective_date: date
    cutoff_method: CutoffMethod
    cutoff_delay_months: int
    abandonment_delay_months: int
    after_tax: bool
    federal_tax_rate_pct: float | None
    tangible_recovery_years: int | None
    idc_method: IdcMethod | None


@dataclass(frozen=True, slots=True)
class TypeCurve:
    """One phase of a saved type curve.

    Rates are per day (bbl/d for oil and water, Mcf/d for gas) at
    ``normalization_lateral_length_ft``, or as they are when that is None (the
    curve is not normalized). Declines are effective annual fractions, named as
    a forecast segment names them.

    The curve builds up from ``rate_start`` to ``rate_init`` over
    ``build_up_months``, declines at ``decline_bridge`` for ``bridge_months``,
    then follows the Arps decline (``decline_init``, ``b_factor``,
    ``decline_min``).

    ``analog_apis`` are the API10s of the wells it was fitted to. The three
    ``fit_*`` fields say how: the percentile of the analogs, whether the saved
    numbers are the auto-fit's own, and the forecast model the analogs' tails
    were read from (None: their production alone). All three are None when the
    file does not record the fit.
    """

    name: str
    phase: Phase
    normalization_lateral_length_ft: float | None
    rate_start: float
    build_up_months: int
    rate_init: float
    bridge_months: int
    decline_bridge: float
    decline_init: float
    b_factor: float
    decline_min: float
    analog_apis: tuple[str, ...]
    fit_pxx: int | None
    fit_kind: TypeCurveFitKind | None
    fit_forecasts_from: str | None
    saved_by: str | None
    saved_at: datetime | None


@dataclass(frozen=True, slots=True)
class ModelFieldRef:
    """One field of a shared econ model that is read per well.

    Every well is charged its own value for ``field``, taken from its well
    attribute column ``column``; the number the model stores for that field is
    only a fallback. ``segment`` is the place of the segment among the model's
    segments as its reader returns them (``expense_models(name)[segment]``), 0
    for a shrink/yield model.

    A tax rate's column holds percentages (4.5 is 4.5%), where the model's own
    number is a fraction. ``field`` can also name ``condensate_diff`` or
    ``condensate_yield_bbl_mmscf``, which the model readers do not carry.
    """

    kind: ModelKind
    name: str
    segment: int
    field: str
    column: str


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
