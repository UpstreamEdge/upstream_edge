"""Connection and transaction support."""

from __future__ import annotations

import logging
import sqlite3
import warnings
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import TracebackType

from . import _pandas, attributes, readers, writers
from .enums import AttributeType, Phase, RsvCat
from .exceptions import DatabaseLockedError
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
from .types import AttributeValue

LOGGER = logging.getLogger("upstream_edge.obsidian_db")


class Database:
    """Open Obsidian SQLite databases and coordinate safe access."""

    def __init__(self, path: Path, conn: sqlite3.Connection) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = conn
        self._transaction_open = False

    @classmethod
    def open(cls, path: str | Path) -> Database:
        """Open a SQLite database file.

        Args:
            path: Path to the Obsidian SQLite database.

        Returns:
            An open Database instance.

        Example:
            >>> db = Database.open(":memory:")
            >>> db.close()
        """
        db_path = Path(path)
        conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        LOGGER.info("opened Obsidian database at %s", db_path)
        return cls(db_path, conn)

    @property
    def path(self) -> Path:
        """Return the database path.

        Example:
            >>> with Database.open(":memory:") as db:
            ...     str(db.path)
            ':memory:'
        """
        self._ensure_open()
        return self._path

    def close(self) -> None:
        """Close the database connection.

        Example:
            >>> db = Database.open(":memory:")
            >>> db.close()
            >>> db.close()
        """
        if self._conn is None:
            return
        self._conn.close()
        self._conn = None
        self._transaction_open = False
        LOGGER.info("closed Obsidian database at %s", self._path)

    def __enter__(self) -> Database:
        """Return this open database for context-manager use."""
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the connection when leaving a context manager."""
        self.close()

    def __del__(self) -> None:
        conn = getattr(self, "_conn", None)
        if conn is not None:
            warnings.warn(
                f"Database at {str(self._path)!r} was not closed; use a context manager or call .close()",
                ResourceWarning,
                stacklevel=2,
            )
            self.close()

    def __repr__(self) -> str:
        """Return a concise representation useful in tracebacks."""
        return f"Database(path={str(self._path)!r})"

    def transaction(self) -> AbstractContextManager[None]:
        """Open a write transaction.

        Raises:
            RuntimeError: The database is closed or another transaction is already open.
            DatabaseLockedError: Another process holds SQLite's write lock.
        """
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        conn = self._ensure_open()
        if self._transaction_open:
            raise RuntimeError("transaction already open")
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise _locked_error(exc) from exc
        self._transaction_open = True
        LOGGER.debug("began transaction for %s", self._path)
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            LOGGER.debug("rolled back transaction for %s", self._path)
            raise
        else:
            conn.execute("COMMIT")
            LOGGER.debug("committed transaction for %s", self._path)
        finally:
            self._transaction_open = False

    @contextmanager
    def _write_context(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._ensure_open()
        if self._transaction_open:
            yield conn
            return
        with self._transaction():
            yield conn

    def wells(self) -> list[Well]:
        """Return all well headers ordered by PropID.

        Returns:
            Well header rows.

        Raises:
            DataIntegrityError: Main contains enum or date values the library cannot interpret.
            RuntimeError: The database has been closed.
        """
        return readers.wells(self._ensure_open())

    def well(self, prop_id: str) -> Well | None:
        """Look up a single well by PropID.

        Args:
            prop_id: The well's property identifier.

        Returns:
            The matching well, or None when no row exists.

        Raises:
            DataIntegrityError: Main contains values the library cannot interpret.
            RuntimeError: The database has been closed.
        """
        return readers.well(self._ensure_open(), prop_id)

    def well_by_api(self, api_10: str) -> Well | None:
        """Look up a single well by API10.

        Args:
            api_10: Ten-digit API identifier.

        Returns:
            The first matching well ordered by PropID, or None when no row exists.

        Raises:
            DataIntegrityError: Main contains values the library cannot interpret.
            RuntimeError: The database has been closed.
        """
        return readers.well_by_api(self._ensure_open(), api_10)

    def production_monthly(self, prop_id: str | None = None) -> list[MonthlyRow]:
        """Return monthly production rows.

        Args:
            prop_id: Optional PropID filter. None returns all wells.

        Returns:
            Monthly production rows ordered by PropID and month.

        Raises:
            DataIntegrityError: Monthly contains dates the library cannot interpret.
            RuntimeError: The database has been closed.
        """
        return readers.production_monthly(self._ensure_open(), prop_id)

    def production_daily(self, prop_id: str | None = None) -> list[DailyRow]:
        """Return daily production rows.

        Args:
            prop_id: Optional PropID filter. None returns all wells.

        Returns:
            Daily production rows ordered by PropID and date.

        Raises:
            DataIntegrityError: Daily contains dates the library cannot interpret.
            RuntimeError: The database has been closed.
        """
        return readers.production_daily(self._ensure_open(), prop_id)

    def wells_df(self):
        """Return all well headers as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.wells(), Well)

    def production_monthly_df(self, prop_id: str | None = None):
        """Return monthly production as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.production_monthly(prop_id), MonthlyRow)

    def production_daily_df(self, prop_id: str | None = None):
        """Return daily production as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.production_daily(prop_id), DailyRow)

    def forecasts(self, prop_id: str | None = None, model: str | None = None) -> list[Forecast]:
        """Return forecast segments with optional PropID and model filters."""
        return readers.forecasts(self._ensure_open(), prop_id, model)

    def forecasts_df(self, prop_id: str | None = None, model: str | None = None):
        """Return forecast segments as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.forecasts(prop_id, model), Forecast)

    def price_models(self, name: str | None = None) -> list[PriceModel]:
        """Return price model segments, optionally filtered by name."""
        return readers.price_models(self._ensure_open(), name)

    def price_models_df(self, name: str | None = None):
        """Return price model segments as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.price_models(name), PriceModel)

    def expense_models(self, name: str | None = None) -> list[ExpenseModel]:
        """Return expense model segments, optionally filtered by name."""
        return readers.expense_models(self._ensure_open(), name)

    def expense_models_df(self, name: str | None = None):
        """Return expense model segments as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.expense_models(name), ExpenseModel)

    def tax_models(self, name: str | None = None) -> list[TaxModel]:
        """Return tax model segments, optionally filtered by name."""
        return readers.tax_models(self._ensure_open(), name)

    def tax_models_df(self, name: str | None = None):
        """Return tax model segments as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.tax_models(name), TaxModel)

    def diff_models(self, name: str | None = None) -> list[DiffModel]:
        """Return differential model segments, optionally filtered by name."""
        return readers.diff_models(self._ensure_open(), name)

    def diff_models_df(self, name: str | None = None):
        """Return differential model segments as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.diff_models(name), DiffModel)

    def shrink_yield_models(self, name: str | None = None) -> list[ShrinkYieldModel]:
        """Return shrink/yield models, optionally filtered by name."""
        return readers.shrink_yield_models(self._ensure_open(), name)

    def shrink_yield_models_df(self, name: str | None = None):
        """Return shrink/yield models as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.shrink_yield_models(name), ShrinkYieldModel)

    def well_models(
        self, prop_id: str | None = None, scenario: str | None = None
    ) -> list[WellModels]:
        """Return per-well scenario model assignments."""
        return readers.well_models(self._ensure_open(), prop_id, scenario)

    def well_models_df(self, prop_id: str | None = None, scenario: str | None = None):
        """Return per-well scenario model assignments as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.well_models(prop_id, scenario), WellModels)

    def interest(self, prop_id: str | None = None, model: str | None = None) -> list[Interest]:
        """Return interest schedule rows."""
        return readers.interest(self._ensure_open(), prop_id, model)

    def interest_df(self, prop_id: str | None = None, model: str | None = None):
        """Return interest schedule rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.interest(prop_id, model), Interest)

    def capex(self, prop_id: str | None = None, model: str | None = None) -> list[Capex]:
        """Return capex schedule rows."""
        return readers.capex(self._ensure_open(), prop_id, model)

    def capex_df(self, prop_id: str | None = None, model: str | None = None):
        """Return capex schedule rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.capex(prop_id, model), Capex)

    def abandonment(
        self, prop_id: str | None = None, model: str | None = None
    ) -> list[Abandonment]:
        """Return abandonment cost rows."""
        return readers.abandonment(self._ensure_open(), prop_id, model)

    def abandonment_df(self, prop_id: str | None = None, model: str | None = None):
        """Return abandonment cost rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.abandonment(prop_id, model), Abandonment)

    def scenarios(self, name: str | None = None) -> list[Scenario]:
        """Return scenarios, optionally filtered by name."""
        return readers.scenarios(self._ensure_open(), name)

    def scenarios_df(self, name: str | None = None):
        """Return scenarios as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.scenarios(name), Scenario)

    def surveys(self, prop_id: str | None = None) -> list[SurveyPoint]:
        """Return directional survey rows."""
        return readers.surveys(self._ensure_open(), prop_id)

    def surveys_df(self, prop_id: str | None = None):
        """Return directional survey rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.surveys(prop_id), SurveyPoint)

    def reservoirs(self, prop_id: str | None = None) -> list[Reservoir]:
        """Return reservoir top and thickness rows."""
        return readers.reservoirs(self._ensure_open(), prop_id)

    def reservoirs_df(self, prop_id: str | None = None):
        """Return reservoir top and thickness rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.reservoirs(prop_id), Reservoir)

    def completions(self, prop_id: str | None = None) -> list[Completion]:
        """Return completion rows."""
        return readers.completions(self._ensure_open(), prop_id)

    def completions_df(self, prop_id: str | None = None):
        """Return completion rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.completions(prop_id), Completion)

    def perfs(self, prop_id: str | None = None) -> list[Perfs]:
        """Return perforation interval rows."""
        return readers.perfs(self._ensure_open(), prop_id)

    def perfs_df(self, prop_id: str | None = None):
        """Return perforation interval rows as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.perfs(prop_id), Perfs)

    def list_attribute_columns(self) -> list[AttributeColumn]:
        """Return user-defined WellAttributes columns in table order."""
        return attributes.list_attribute_columns(self._ensure_open())

    def list_attribute_columns_df(self):
        """Return user-defined WellAttributes columns as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.list_attribute_columns(), AttributeColumn)

    def well_attributes(self, prop_id: str) -> dict[str, AttributeValue]:
        """Return one well's WellAttributes values."""
        return attributes.well_attributes(self._ensure_open(), prop_id)

    def well_attributes_df(self, prop_id: str):
        """Return one well's WellAttributes values as a pandas DataFrame."""
        return _pandas.from_single_mapping(self.well_attributes(prop_id))

    def all_well_attributes(self) -> dict[str, dict[str, AttributeValue]]:
        """Return WellAttributes values for every PropID."""
        return attributes.all_well_attributes(self._ensure_open())

    def all_well_attributes_df(self):
        """Return all WellAttributes values as a pandas DataFrame."""
        return _pandas.from_mapping_rows(self.all_well_attributes())

    def add_well(
        self, prop_id: str, *, rsv_cat: RsvCat, api_10: str | None = None, **header_fields: object
    ) -> None:
        """Create a well."""
        with self._write_context() as conn:
            writers.add_well(
                conn, prop_id, rsv_cat=rsv_cat, api_10=api_10, header_fields=header_fields
            )

    def delete_well(self, prop_id: str, *, confirm: bool) -> None:
        """Delete a well and all rows keyed by its PropID."""
        with self._write_context() as conn:
            writers.delete_well(conn, prop_id, confirm=confirm)

    def copy_well(self, from_prop_id: str, to_prop_id: str) -> None:
        """Copy one well and all rows keyed by its PropID."""
        with self._write_context() as conn:
            writers.copy_well(conn, from_prop_id, to_prop_id)

    def set_well_header(self, prop_id: str, **fields: object) -> None:
        """Update selected Main header fields for an existing well."""
        with self._write_context() as conn:
            writers.set_well_header(conn, prop_id, fields)

    def set_monthly_prod(self, rows: list[MonthlyRow]) -> None:
        """Upsert monthly production rows."""
        with self._write_context() as conn:
            writers.set_monthly_prod(conn, rows)

    def set_daily_prod(self, rows: list[DailyRow]) -> None:
        """Upsert daily production rows."""
        with self._write_context() as conn:
            writers.set_daily_prod(conn, rows)

    def delete_monthly_prod(self, prop_id: str | None = None, *, confirm: bool = False) -> None:
        """Delete monthly production rows."""
        with self._write_context() as conn:
            writers.delete_monthly_prod(conn, prop_id, confirm=confirm)

    def delete_daily_prod(self, prop_id: str | None = None, *, confirm: bool = False) -> None:
        """Delete daily production rows."""
        with self._write_context() as conn:
            writers.delete_daily_prod(conn, prop_id, confirm=confirm)

    def set_forecast(
        self, prop_id: str, model: str, phase: Phase, segments: list[ForecastSegment]
    ) -> None:
        """Replace forecast segments for one PropID/model/phase."""
        with self._write_context() as conn:
            writers.set_forecast(conn, prop_id, model, phase, segments)

    def delete_forecast(
        self,
        prop_id: str | None = None,
        model: str | None = None,
        phase: Phase | None = None,
        *,
        confirm: bool = False,
    ) -> None:
        """Delete forecast rows matching optional filters."""
        with self._write_context() as conn:
            writers.delete_forecast(conn, prop_id, model, phase, confirm=confirm)

    def set_price_model(self, name: str, segments: list[PriceModelSegment]) -> None:
        """Replace a price model with one or more segments."""
        with self._write_context() as conn:
            writers.set_price_model(conn, name, segments)

    def delete_price_model(self, name: str) -> None:
        """Delete all segments for a price model."""
        with self._write_context() as conn:
            writers.delete_price_model(conn, name)

    def set_expense_model(self, name: str, segments: list[ExpenseModelSegment]) -> None:
        """Replace a shared expense model."""
        with self._write_context() as conn:
            writers.set_expense_model(conn, name, segments)

    def delete_expense_model(self, name: str) -> None:
        """Delete a shared expense model."""
        with self._write_context() as conn:
            writers.delete_expense_model(conn, name)

    def set_tax_model(self, name: str, segments: list[TaxModelSegment]) -> None:
        """Replace a shared tax model."""
        with self._write_context() as conn:
            writers.set_tax_model(conn, name, segments)

    def delete_tax_model(self, name: str) -> None:
        """Delete a shared tax model."""
        with self._write_context() as conn:
            writers.delete_tax_model(conn, name)

    def set_diff_model(self, name: str, segments: list[DiffModelSegment]) -> None:
        """Replace a shared differential model."""
        with self._write_context() as conn:
            writers.set_diff_model(conn, name, segments)

    def delete_diff_model(self, name: str) -> None:
        """Delete a shared differential model."""
        with self._write_context() as conn:
            writers.delete_diff_model(conn, name)

    def set_shrink_yield_model(
        self,
        name: str,
        *,
        gas_shrink_frac: float,
        ngl_yield_bbl_mmscf: float,
    ) -> None:
        """Replace a shared shrink/yield model."""
        with self._write_context() as conn:
            writers.set_shrink_yield_model(
                conn,
                name,
                gas_shrink_frac=gas_shrink_frac,
                ngl_yield_bbl_mmscf=ngl_yield_bbl_mmscf,
            )

    def delete_shrink_yield_model(self, name: str) -> None:
        """Delete a shared shrink/yield model."""
        with self._write_context() as conn:
            writers.delete_shrink_yield_model(conn, name)

    def set_well_expense_model(
        self,
        prop_id: str,
        scenario: str,
        segments: list[ExpenseModelSegment],
    ) -> None:
        """Replace a per-well expense model override."""
        with self._write_context() as conn:
            writers.set_well_expense_model(conn, prop_id, scenario, segments)

    def delete_well_expense_model(self, prop_id: str, scenario: str) -> None:
        """Delete a per-well expense model override."""
        with self._write_context() as conn:
            writers.delete_well_expense_model(conn, prop_id, scenario)

    def set_well_tax_model(
        self,
        prop_id: str,
        scenario: str,
        segments: list[TaxModelSegment],
    ) -> None:
        """Replace a per-well tax model override."""
        with self._write_context() as conn:
            writers.set_well_tax_model(conn, prop_id, scenario, segments)

    def delete_well_tax_model(self, prop_id: str, scenario: str) -> None:
        """Delete a per-well tax model override."""
        with self._write_context() as conn:
            writers.delete_well_tax_model(conn, prop_id, scenario)

    def set_well_diff_model(
        self,
        prop_id: str,
        scenario: str,
        segments: list[DiffModelSegment],
    ) -> None:
        """Replace a per-well differential model override."""
        with self._write_context() as conn:
            writers.set_well_diff_model(conn, prop_id, scenario, segments)

    def delete_well_diff_model(self, prop_id: str, scenario: str) -> None:
        """Delete a per-well differential model override."""
        with self._write_context() as conn:
            writers.delete_well_diff_model(conn, prop_id, scenario)

    def set_well_shrink_yield_model(
        self,
        prop_id: str,
        scenario: str,
        *,
        gas_shrink_frac: float,
        ngl_yield_bbl_mmscf: float,
    ) -> None:
        """Replace a per-well shrink/yield model override."""
        with self._write_context() as conn:
            writers.set_well_shrink_yield_model(
                conn,
                prop_id,
                scenario,
                gas_shrink_frac=gas_shrink_frac,
                ngl_yield_bbl_mmscf=ngl_yield_bbl_mmscf,
            )

    def delete_well_shrink_yield_model(self, prop_id: str, scenario: str) -> None:
        """Delete a per-well shrink/yield model override."""
        with self._write_context() as conn:
            writers.delete_well_shrink_yield_model(conn, prop_id, scenario)

    def create_scenario(self, name: str, *, copy_from: str | None = None) -> None:
        """Create a scenario, optionally copying another scenario's assignments."""
        with self._write_context() as conn:
            writers.create_scenario(conn, name, copy_from=copy_from)

    def delete_scenario(self, name: str, *, confirm: bool) -> None:
        """Delete a scenario and its per-well assignments."""
        with self._write_context() as conn:
            writers.delete_scenario(conn, name, confirm=confirm)

    def set_scenario(
        self,
        name: str,
        *,
        forecast_model: str | None = None,
        price_model: str | None = None,
    ) -> None:
        """Partially update a scenario's forecast and price model names."""
        with self._write_context() as conn:
            writers.set_scenario(conn, name, forecast_model=forecast_model, price_model=price_model)

    def set_well_models(
        self,
        prop_ids: str | list[str],
        scenario: str,
        *,
        exp_model: str | None = None,
        capex_model: str | None = None,
        diff_model: str | None = None,
        tax_model: str | None = None,
        shrink_yield_model: str | None = None,
        interest_model: str | None = None,
    ) -> None:
        """Partially update per-well model assignments for a scenario."""
        with self._write_context() as conn:
            writers.set_well_models(
                conn,
                prop_ids,
                scenario,
                exp_model=exp_model,
                capex_model=capex_model,
                diff_model=diff_model,
                tax_model=tax_model,
                shrink_yield_model=shrink_yield_model,
                interest_model=interest_model,
            )

    def set_interest(
        self, prop_ids: str | list[str], model: str, segments: list[InterestSegment]
    ) -> None:
        """Replace an interest schedule for one model across one or more wells."""
        with self._write_context() as conn:
            writers.set_interest(conn, prop_ids, model, segments)

    def delete_interest(
        self,
        prop_ids: str | list[str] | None = None,
        model: str | None = None,
        *,
        confirm: bool = False,
    ) -> None:
        """Delete interest rows matching optional filters."""
        with self._write_context() as conn:
            writers.delete_interest(conn, prop_ids, model, confirm=confirm)

    def set_capex(self, prop_id: str, model: str, items: list[CapexItem]) -> None:
        """Replace capex items for one PropID/model pair."""
        with self._write_context() as conn:
            writers.set_capex(conn, prop_id, model, items)

    def delete_capex(
        self, prop_id: str | None = None, model: str | None = None, *, confirm: bool = False
    ) -> None:
        """Delete capex rows matching optional filters."""
        with self._write_context() as conn:
            writers.delete_capex(conn, prop_id, model, confirm=confirm)

    def set_abandonment(self, prop_id: str, model: str, cost_gross: float) -> None:
        """Replace one abandonment cost row."""
        with self._write_context() as conn:
            writers.set_abandonment(conn, prop_id, model, cost_gross)

    def set_surveys(self, prop_id: str, points: list[SurveyPointInput]) -> None:
        """Replace every survey point for a well."""
        with self._write_context() as conn:
            writers.set_surveys(conn, prop_id, points)

    def delete_surveys(self, prop_id: str | None = None, *, confirm: bool = False) -> None:
        """Delete survey rows."""
        with self._write_context() as conn:
            writers.delete_surveys(conn, prop_id, confirm=confirm)

    def set_reservoir(
        self,
        prop_id: str,
        reservoir: str,
        *,
        top_depth_ft: float,
        thickness_ft: float | None = None,
    ) -> None:
        """Upsert one reservoir row."""
        with self._write_context() as conn:
            writers.set_reservoir(
                conn,
                prop_id,
                reservoir,
                top_depth_ft=top_depth_ft,
                thickness_ft=thickness_ft,
            )

    def delete_reservoir_data(
        self,
        prop_id: str | None = None,
        *,
        reservoir: str | None = None,
        confirm: bool = False,
    ) -> None:
        """Delete reservoir rows matching optional filters."""
        with self._write_context() as conn:
            writers.delete_reservoir_data(conn, prop_id, reservoir, confirm=confirm)

    def set_completion(
        self,
        prop_id: str,
        *,
        frac_proppant_lb: float | None = None,
        frac_fluid_bbl: float | None = None,
        frac_stages: int | None = None,
    ) -> None:
        """Partially update or create a completion row."""
        with self._write_context() as conn:
            writers.set_completion(
                conn,
                prop_id,
                frac_proppant_lb=frac_proppant_lb,
                frac_fluid_bbl=frac_fluid_bbl,
                frac_stages=frac_stages,
            )

    def delete_completion(self, prop_id: str, *, confirm: bool = False) -> None:
        """Delete one completion row."""
        with self._write_context() as conn:
            writers.delete_completion(conn, prop_id, confirm=confirm)

    def set_perfs(self, prop_id: str, perfs: list[PerfsInput]) -> None:
        """Replace every perforation interval for a well."""
        with self._write_context() as conn:
            writers.set_perfs(conn, prop_id, perfs)

    def delete_perfs(self, prop_id: str | None = None, *, confirm: bool = False) -> None:
        """Delete perforation rows."""
        with self._write_context() as conn:
            writers.delete_perfs(conn, prop_id, confirm=confirm)

    def set_well_attribute(self, prop_id: str, column: str, value: AttributeValue) -> None:
        """Set one WellAttributes cell."""
        with self._write_context() as conn:
            writers.set_well_attribute(conn, prop_id, column, value)

    def set_well_attributes_bulk(self, updates: list[WellAttributeUpdate]) -> None:
        """Set many WellAttributes cells."""
        with self._write_context() as conn:
            writers.set_well_attributes_bulk(conn, updates)

    def add_well_attribute_column(
        self, name: str, attr_type: AttributeType, default: AttributeValue | None = None
    ) -> None:
        """Add a user-defined WellAttributes column and backfill existing rows."""
        with self._write_context() as conn:
            writers.add_well_attribute_column(conn, name, attr_type, default)

    def rename_well_attribute_column(self, old: str, new: str) -> None:
        """Rename a user-defined WellAttributes column."""
        with self._write_context() as conn:
            writers.rename_well_attribute_column(conn, old, new)

    def delete_well_attribute_column(self, name: str, *, confirm: bool) -> None:
        """Delete a user-defined WellAttributes column."""
        with self._write_context() as conn:
            writers.delete_well_attribute_column(conn, name, confirm=confirm)

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is closed")
        return self._conn


def _locked_error(exc: sqlite3.OperationalError) -> sqlite3.OperationalError | DatabaseLockedError:
    if "database is locked" in str(exc).lower():
        return DatabaseLockedError(
            "Obsidian appears to have this database open for writes. Close it and retry."
        )
    return exc
