"""Connection and transaction support."""

from __future__ import annotations

import logging
import os
import sqlite3
import warnings
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import date, datetime
from pathlib import Path
from types import TracebackType
from urllib.parse import quote

from . import _pandas, attributes, readers, writers
from ._changelog import ChangeRecorder
from ._sql import missing_table_name
from .enums import AttributeType, ModelKind, Phase, RsvCat
from .exceptions import (
    DatabaseLockedError,
    MissingTableError,
    ReadOnlyDatabaseError,
    ValidationError,
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
    ModelFieldRef,
    MonthlyRow,
    Perfs,
    PerfsInput,
    PriceModel,
    PriceModelSegment,
    Reservoir,
    RunSettings,
    Scenario,
    ShrinkYieldModel,
    SurveyPoint,
    SurveyPointInput,
    TaxModel,
    TaxModelSegment,
    TypeCurve,
    Well,
    WellAttributeUpdate,
    WellModels,
)
from .types import AttributeValue

LOGGER = logging.getLogger("upstream_edge.obsidian_db")


class Database:
    """Open Obsidian SQLite databases and coordinate safe access."""

    def __init__(
        self,
        path: Path,
        conn: sqlite3.Connection,
        *,
        read_only: bool = False,
        change_log: bool = True,
        user: str | None = None,
    ) -> None:
        self._path = path
        # Where the file is, fixed now: a path given relative to the working
        # directory must keep meaning this file if the directory changes later.
        self._file = None if str(path) == ":memory:" else Path(os.path.abspath(path))
        self._conn: sqlite3.Connection | None = conn
        self._transaction_open = False
        self._read_only = read_only
        self._changes = ChangeRecorder(enabled=change_log, user=user)

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        read_only: bool = False,
        change_log: bool = True,
        user: str | None = None,
    ) -> Database:
        """Open a SQLite database file.

        Args:
            path: Path to the Obsidian SQLite database.
            read_only: Open the file for reading only. Every writer then raises
                ``ReadOnlyDatabaseError``, and the file cannot be changed or
                locked for writing by this connection. Use it for a script that
                only reads, above all while Obsidian has the file open.
            change_log: Record each change in the database's ``ChangeLog``
                table, as Obsidian does for its own edits. It is what fills
                "Forecast Last Changed" in the well table. Pass ``False`` when
                loading a new database in bulk, where a row per well would
                only be noise.
            user: Who is making the changes, written to the ChangeLog rows and
                shown by Obsidian as "Changed By". Obsidian writes the sign-in
                email here. Left out, the rows name no one.

        Returns:
            An open Database instance.

        Raises:
            FileNotFoundError: The path does not exist (":memory:" excepted).

        Example:
            >>> db = Database.open(":memory:")
            >>> db.close()
        """
        in_memory = str(path) == ":memory:"
        if not in_memory and not Path(path).exists():
            raise FileNotFoundError(
                f"Obsidian database not found at {str(path)!r}; "
                "Database.open() does not create new files"
            )
        db_path = Path(path)
        if read_only and not in_memory:
            conn = sqlite3.connect(
                _read_only_uri(os.path.abspath(db_path)), uri=True, isolation_level=None
            )
        else:
            conn = sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        name = user.strip() if user is not None else ""
        LOGGER.info("opened Obsidian database at %s", db_path)
        return cls(db_path, conn, read_only=read_only, change_log=change_log, user=name or None)

    @property
    def read_only(self) -> bool:
        """Whether the database was opened with ``read_only=True``.

        Example:
            >>> with Database.open(":memory:", read_only=True) as db:
            ...     db.read_only
            True
        """
        return self._read_only

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
            ReadOnlyDatabaseError: The database is open for reading only.
        """
        return self._transaction()

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        conn = self._ensure_open()
        if self._transaction_open:
            raise RuntimeError("transaction already open")
        if self._read_only:
            raise ReadOnlyDatabaseError(
                f"{str(self._path)!r} was opened with read_only=True; open it without "
                "read_only to write"
            )
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise _locked_error(exc) from exc
        self._transaction_open = True
        LOGGER.debug("began transaction for %s", self._path)
        try:
            yield
            # The ChangeLog rows and the last-modified stamp go in with the
            # changes they describe, so they commit or roll back together.
            self._changes.flush(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            LOGGER.debug("rolled back transaction for %s", self._path)
            raise
        else:
            try:
                conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                # A reader in another process can hold a commit off past the
                # timeout. SQLite then leaves the transaction open, with its
                # lock on the file; end it, so nothing is half done and the
                # next write starts clean.
                with suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                LOGGER.debug("commit failed, rolled back transaction for %s", self._path)
                raise _locked_error(exc) from exc
            self._changes.committed()
            LOGGER.debug("committed transaction for %s", self._path)
        finally:
            self._transaction_open = False
            self._changes.rolled_back()

    def snapshot(self) -> Path:
        """Save a restore point: a copy of the database as it is now.

        The copy goes where Obsidian's own Archive Current DB button puts one, in the
        ``_Archive`` folder beside the file, named for the file and the time
        (``Field_20261124_1530.obsdb``), so Obsidian's Database mode lists it
        as a version that can be opened or branched from. The database then
        records it as the version it descends from, as Obsidian does.

        Each call saves a full copy. One before a bulk change that is hard to
        undo is the use; one before every write is not.

        Returns:
            The path of the copy.

        Raises:
            ValidationError: The database is in memory and has no file.
            ReadOnlyDatabaseError: The database is open for reading only.
            RuntimeError: A transaction is open; take the snapshot before or after it.
            DatabaseLockedError: Another process holds SQLite's write lock; no
                copy is left behind.
            FileExistsError: Every name for this minute is taken (ten snapshots
                of one database inside a minute); wait and call again.
        """
        conn = self._ensure_open()
        source = self._file
        if source is None:
            raise ValidationError("snapshot: an in-memory database has no file to copy")
        if self._read_only:
            raise ReadOnlyDatabaseError(
                f"{str(self._path)!r} was opened with read_only=True; open it without "
                "read_only to take a snapshot"
            )
        if self._transaction_open:
            raise RuntimeError("snapshot cannot run inside a transaction")
        archive = source.parent / _ARCHIVE_FOLDER
        stamp = _next_version_stamp(
            datetime.now().strftime("%Y%m%d_%H%M"),
            [archive, source.parent / _VERSIONS_FOLDER / source.stem],
        )
        version = f"{source.stem}_{stamp}"
        destination = archive / f"{version}.obsdb"
        archive.mkdir(exist_ok=True)
        try:
            # Claim the name before copying. Creating the file fails if the
            # name is taken: by an eleventh snapshot of this minute, or by
            # Obsidian or another script in the moment since the name was
            # chosen. That version is theirs and is left alone; from here on
            # the file is this call's own to remove.
            destination.touch(exist_ok=False)
        except FileExistsError:
            raise FileExistsError(f"snapshot: {str(destination)!r} already exists") from None
        try:
            # A copy of the committed state that a writer in another process
            # cannot tear, which a plain file copy could not promise. SQLite
            # copies into an empty file.
            conn.execute("VACUUM INTO ?", (str(destination),))
        except BaseException as exc:
            # Whatever stopped the copy (a lock, a damaged file, Ctrl+C), no
            # empty version is left for Obsidian to list.
            _remove(destination)
            if isinstance(exc, sqlite3.OperationalError):
                raise _locked_error(exc) from exc
            raise
        try:
            with self._transaction():
                writers.set_parent_version(conn, version)
        except BaseException:
            # All or nothing: no version is left that the database does not name.
            _remove(destination)
            raise
        LOGGER.info("saved snapshot %s", destination)
        return destination

    @contextmanager
    def _write_context(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self._ensure_open()
        # Translated outside `_transaction`, so a write this opened has already
        # rolled back; inside the caller's transaction, theirs rolls back.
        try:
            if self._transaction_open:
                # Inside the caller's transaction a writer that fails takes
                # back what it had done so far. A caller who catches the error
                # and carries on then commits whole writes only, never the
                # first half of one.
                conn.execute("SAVEPOINT writer")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK TO writer")
                    conn.execute("RELEASE writer")
                    raise
                conn.execute("RELEASE writer")
                self._changes.wrote()
                return
            with self._transaction():
                yield conn
                self._changes.wrote()
        except sqlite3.OperationalError as exc:
            table = missing_table_name(exc)
            if table is None:
                raise
            raise MissingTableError(table) from exc

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
        """Return forecast rows with optional PropID and model filters.

        A phase's forecast is its rows in order of ``start``, and ``row.kind``
        says what each is: a decline segment, a type-curve segment, or stored
        monthly volumes (``row.monthly_volumes``), which a saved Spatial
        Forecasting run puts ahead of the segments and which Obsidian uses as
        they are for the months they cover.

        ``model`` is a forecast-model name, not a scenario. To read the curves a
        scenario actually uses, first read its ``forecast_model`` from
        ``scenarios()`` and pass that as ``model``.
        """
        return readers.forecasts(self._ensure_open(), prop_id, model)

    def forecasts_df(self, prop_id: str | None = None, model: str | None = None):
        """Return forecast rows as a pandas DataFrame.

        ``monthly_volumes`` is empty on a segment's row and holds the list of
        volumes on a stored-volumes row.
        """
        return _pandas.from_dataclass_rows(self.forecasts(prop_id, model), Forecast)

    def price_models(self, name: str | None = None) -> list[PriceModel]:
        """Return price model segments, optionally filtered by name.

        To read the deck a scenario actually uses, first read its ``price_model``
        from ``scenarios()`` and pass that as ``name``.
        """
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
        """Return per-well scenario model assignments.

        Covers the per-well model kinds (expense, tax, diff, shrink/yield, capex,
        interest). The scenario-global forecast and price models are not here —
        read those from ``scenarios``.
        """
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
        """Return scenarios, optionally filtered by name.

        Each row carries the scenario-global ``forecast_model`` and
        ``price_model``. The per-well model kinds live in ``well_models``.
        """
        return readers.scenarios(self._ensure_open(), name)

    def scenarios_df(self, name: str | None = None):
        """Return scenarios as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.scenarios(name), Scenario)

    def run_settings(self, name: str | None = None) -> list[RunSettings]:
        """Return run settings, optionally filtered by name.

        A run settings holds what an economics run is made with: the effective
        date, when a well's economic life is cut off, when abandonment lands,
        and the after-tax choices. Obsidian's ribbon picks one by name.
        """
        return readers.run_settings(self._ensure_open(), name)

    def run_settings_df(self, name: str | None = None):
        """Return run settings as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.run_settings(name), RunSettings)

    def type_curves(self, name: str | None = None) -> list[TypeCurve]:
        """Return saved type curves, one row per curve and phase, optionally by name.

        A forecast segment that follows a type curve names it in ``type_curve``.
        """
        return readers.type_curves(self._ensure_open(), name)

    def type_curves_df(self, name: str | None = None):
        """Return saved type curves as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.type_curves(name), TypeCurve)

    def model_field_refs(
        self, kind: ModelKind | None = None, name: str | None = None
    ) -> list[ModelFieldRef]:
        """Return the fields of shared econ models that are read per well.

        A field of an expense, tax, differential or shrink/yield model can take
        each well's own value from a well attribute column. Where one does, the
        number the model readers return for it is only a stored fallback, so
        check here before reading a model's numbers as what every well is
        charged. Without ``name``, wells' individual models are left out, as the
        model readers leave them out.
        """
        return readers.model_field_refs(self._ensure_open(), kind, name)

    def model_field_refs_df(self, kind: ModelKind | None = None, name: str | None = None):
        """Return per-well model field references as a pandas DataFrame."""
        return _pandas.from_dataclass_rows(self.model_field_refs(kind, name), ModelFieldRef)

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
        """Create a well. ``header_fields`` are the fields of :class:`Well`, by keyword."""
        with self._write_context() as conn:
            self._changes.add(
                writers.add_well(
                    conn, prop_id, rsv_cat=rsv_cat, api_10=api_10, header_fields=header_fields
                )
            )

    def delete_well(self, prop_id: str, *, confirm: bool) -> None:
        """Delete a well, its rows in the per-well tables, and its individual econ models.

        The per-well tables are Monthly, Daily, Forecast, WellModels, Interest,
        Capex, Abandonment, Survey, Reservoir, Completion, Perfs, WellAttributes
        and a couple of rarely used ones; one the file does not have is skipped.
        """
        with self._write_context() as conn:
            writers.delete_well(conn, prop_id, confirm=confirm)
            self._changes.forget_well(conn, prop_id)

    def copy_well(self, from_prop_id: str, to_prop_id: str) -> None:
        """Copy a well, its rows in the per-well tables, and its individual econ models.

        The per-well tables are the ones `delete_well` clears; one the file does
        not have is skipped.
        """
        with self._write_context() as conn:
            self._changes.add(writers.copy_well(conn, from_prop_id, to_prop_id))

    def set_well_header(self, prop_id: str, **fields: object) -> None:
        """Update selected Main header fields for an existing well.

        ``fields`` are the fields of :class:`Well`, by keyword.
        """
        with self._write_context() as conn:
            self._changes.add(writers.set_well_header(conn, prop_id, fields))

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
        """Replace forecast segments for one PropID/model/phase.

        ``model`` is a forecast-model name, not a scenario. Writing segments
        here only stores the curve; it is not applied until a scenario points at
        the name via ``set_scenario(scenario, forecast_model=model)``. One model
        name can hold curves for many wells, and a scenario applies that whole
        named set globally.
        """
        with self._write_context() as conn:
            self._changes.add(writers.set_forecast(conn, prop_id, model, phase, segments))

    def delete_forecast(
        self,
        prop_id: str | None = None,
        model: str | None = None,
        phase: Phase | None = None,
        *,
        confirm: bool = False,
    ) -> None:
        """Delete forecast rows matching optional filters.

        Without ``phase`` a forecast is deleted whole: its segments and any
        stored monthly volumes it starts with. With ``phase`` only that phase's
        segments are deleted, and stored volumes are left in place with a
        ``MonthlyVolumesWarning``.
        """
        with self._write_context() as conn:
            self._changes.add(writers.delete_forecast(conn, prop_id, model, phase, confirm=confirm))

    def set_price_model(self, name: str, segments: list[PriceModelSegment]) -> None:
        """Replace a price model with one or more segments.

        This stores a named, shared price deck; it does not assign it to anyone.
        A price model is applied globally to a scenario's wells via
        ``set_scenario(scenario, price_model=name)`` — there is no per-well price
        assignment.
        """
        with self._write_context() as conn:
            self._changes.add(writers.set_price_model(conn, name, segments))

    def delete_price_model(self, name: str, *, confirm: bool = False) -> None:
        """Delete all segments for a price model."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_price_model(conn, name, confirm=confirm))

    def set_expense_model(self, name: str, segments: list[ExpenseModelSegment]) -> None:
        """Replace a shared expense model."""
        with self._write_context() as conn:
            self._changes.add(writers.set_expense_model(conn, name, segments))

    def delete_expense_model(self, name: str, *, confirm: bool = False) -> None:
        """Delete a shared expense model."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_expense_model(conn, name, confirm=confirm))

    def set_tax_model(self, name: str, segments: list[TaxModelSegment]) -> None:
        """Replace a shared tax model."""
        with self._write_context() as conn:
            self._changes.add(writers.set_tax_model(conn, name, segments))

    def delete_tax_model(self, name: str, *, confirm: bool = False) -> None:
        """Delete a shared tax model."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_tax_model(conn, name, confirm=confirm))

    def set_diff_model(self, name: str, segments: list[DiffModelSegment]) -> None:
        """Replace a shared differential model."""
        with self._write_context() as conn:
            self._changes.add(writers.set_diff_model(conn, name, segments))

    def delete_diff_model(self, name: str, *, confirm: bool = False) -> None:
        """Delete a shared differential model."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_diff_model(conn, name, confirm=confirm))

    def set_shrink_yield_model(
        self,
        name: str,
        *,
        gas_shrink_frac: float,
        ngl_yield_bbl_mmscf: float,
    ) -> None:
        """Replace a shared shrink/yield model."""
        with self._write_context() as conn:
            self._changes.add(
                writers.set_shrink_yield_model(
                    conn,
                    name,
                    gas_shrink_frac=gas_shrink_frac,
                    ngl_yield_bbl_mmscf=ngl_yield_bbl_mmscf,
                )
            )

    def delete_shrink_yield_model(self, name: str, *, confirm: bool = False) -> None:
        """Delete a shared shrink/yield model."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_shrink_yield_model(conn, name, confirm=confirm))

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
        """Set a scenario's forecast and price models.

        Forecast and price models are applied globally to every well in the
        scenario, so they live here on the scenario rather than per well. This
        is the only writer that assigns them — they are deliberately absent from
        ``set_well_models``, which handles the per-well model kinds (expense,
        tax, diff, shrink/yield, capex, interest).

        Both ``forecast_model`` and ``price_model`` are names. ``forecast_model``
        points at the ``model`` used by ``set_forecast``; ``price_model`` points
        at a deck created by ``set_price_model``. A missing ``price_model`` is a
        hard error (it is a named entity), while a missing ``forecast_model``
        only warns (it is a label in the Forecast table that you may populate
        later). This is a partial update — only the kwargs you pass are written.
        """
        with self._write_context() as conn:
            writers.set_scenario(conn, name, forecast_model=forecast_model, price_model=price_model)

    def set_run_settings(self, name: str, *, effective_date: date | None = None) -> None:
        """Set a run settings' effective date.

        ``effective_date`` is the first day of the month the economics run from,
        for example ``date(2026, 7, 1)``. Each run settings keeps its own.

        Raises:
            ModelNotFoundError: The database has no run settings of that name.
            ValidationError: The date is not the first of a month.
        """
        with self._write_context() as conn:
            self._changes.add(writers.set_run_settings(conn, name, effective_date=effective_date))

    def set_well_models(
        self,
        prop_ids: str | list[str],
        scenario: str,
        *,
        expense_model: str | None = None,
        capex_model: str | None = None,
        diff_model: str | None = None,
        tax_model: str | None = None,
        shrink_yield_model: str | None = None,
        interest_model: str | None = None,
        exp_model: str | None = None,
    ) -> None:
        """Set the per-well model assignments for a scenario.

        These six model kinds are assigned per well within a scenario. The two
        scenario-global kinds — forecast and price — are *not* set here; use
        ``set_scenario`` for those. This is a partial update: only the kwargs
        you pass are written, and the rest are left unchanged.
        """
        with self._write_context() as conn:
            self._changes.add(
                writers.set_well_models(
                    conn,
                    prop_ids,
                    scenario,
                    expense_model=expense_model,
                    capex_model=capex_model,
                    diff_model=diff_model,
                    tax_model=tax_model,
                    shrink_yield_model=shrink_yield_model,
                    interest_model=interest_model,
                    exp_model=exp_model,
                )
            )

    def set_interest(
        self, prop_ids: str | list[str], model: str, segments: list[InterestSegment]
    ) -> None:
        """Replace an interest schedule for one model across one or more wells."""
        with self._write_context() as conn:
            self._changes.add(writers.set_interest(conn, prop_ids, model, segments))

    def delete_interest(
        self,
        prop_ids: str | list[str] | None = None,
        model: str | None = None,
        *,
        confirm: bool = False,
    ) -> None:
        """Delete interest rows matching optional filters."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_interest(conn, prop_ids, model, confirm=confirm))

    def set_capex(self, prop_id: str, model: str, items: list[CapexItem]) -> None:
        """Replace capex items for one PropID/model pair."""
        with self._write_context() as conn:
            self._changes.add(writers.set_capex(conn, prop_id, model, items))

    def delete_capex(
        self, prop_id: str | None = None, model: str | None = None, *, confirm: bool = False
    ) -> None:
        """Delete capex rows matching optional filters."""
        with self._write_context() as conn:
            self._changes.add(writers.delete_capex(conn, prop_id, model, confirm=confirm))

    def set_abandonment(self, prop_id: str, model: str, cost_gross: float) -> None:
        """Replace one abandonment cost row.

        Every well carries an abandonment cost for each model; pass
        ``cost_gross=0.0`` to clear it rather than removing the row.
        """
        with self._write_context() as conn:
            self._changes.add(writers.set_abandonment(conn, prop_id, model, cost_gross))

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

    def delete_well_attribute_column(
        self, name: str, *, confirm: bool, detach_model_refs: bool = False
    ) -> None:
        """Delete a user-defined WellAttributes column.

        A column that an econ model field reads per well (``model_field_refs``)
        is refused unless ``detach_model_refs=True``, which removes those
        references with it: the fields go back to the number stored on the model.
        """
        with self._write_context() as conn:
            writers.delete_well_attribute_column(
                conn, name, confirm=confirm, detach_model_refs=detach_model_refs
            )

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is closed")
        return self._conn


# Where Obsidian keeps a database's versions: the folder beside the file that
# every build writes today, and the per-database folder a later build moves to.
# Both are read when a new version is named, so no two share a name.
_ARCHIVE_FOLDER = "_Archive"
_VERSIONS_FOLDER = "_ObsidianVersions"


def _read_only_uri(file: str, *, windows: bool = os.name == "nt") -> str:
    """The SQLite URI that opens an absolute path for reading only.

    Built by hand because `Path.as_uri()` puts a network share's server where
    a URI keeps its host (`file://server/share/..`), which SQLite refuses. A
    share is written with an empty host and the server in the path
    (`file:////server/share/..`), which SQLite opens.
    """
    path = file
    if windows:
        # The long-path forms name the same file without the prefix.
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[8:]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
        path = path.replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path  # a drive: /C:/folder/file
    return f"file://{quote(path, safe='/:')}?mode=ro"


def _remove(path: Path) -> None:
    """Delete a file this call wrote, if it is there. Failing to must not hide
    the error that led here."""
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _next_version_stamp(proposed: str, folders: list[Path]) -> str:
    """A version stamp no file in the folders carries yet: the minute, then
    the minute with a letter for a second snapshot inside it, as Obsidian
    names them."""
    names = [
        path.name
        for folder in folders
        if folder.is_dir()
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in {".obsdb", ".db"}
    ]
    if not any(proposed in name for name in names):
        return proposed
    for letter in "BCDEFGHIJ":
        if not any(f"{proposed}{letter}" in name for name in names):
            return f"{proposed}{letter}"
    return f"{proposed}X"


def _locked_error(exc: sqlite3.OperationalError) -> sqlite3.OperationalError | DatabaseLockedError:
    if "database is locked" in str(exc).lower():
        return DatabaseLockedError(
            "Another program has this database busy (Obsidian reading or saving it, or another "
            "script). Nothing was written; wait and retry."
        )
    return exc
