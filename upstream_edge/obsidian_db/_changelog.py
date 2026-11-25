"""ChangeLog rows for what the library changes.

Obsidian keeps one append-only table, ``ChangeLog``, that records *that*
something changed (which table, which well, which model, when, by whom), never
the old or new values. It is what fills "Forecast Last Changed" and "Changed
By" in the well table. The library writes the same rows Obsidian would, by the
same rules:

- One row per changed object, whatever the number of SQL rows behind it.
  Saving all three phases of a well's forecast model is one row.
- What ``detail`` holds is fixed per table: the model name for ``Forecast``,
  ``Interest`` and ``Capex``; the scenario for ``WellModels``; the column name
  for ``Main``. A named econ model (price, expense, tax, differential,
  shrink/yield) is not tied to a well, so its row has no ``prop_id`` and the
  model's name as ``detail``.
- Production, surveys, completions, perfs, attributes and scenarios are not
  logged. Obsidian does not log them either.
- ``source`` is 3, Obsidian's value for a change made outside the application.
- The rows are written inside the transaction that made the change, so they
  commit or roll back with it.
- A change to an object this connection logged less than 30 seconds ago adds
  no second row.
- A failure to write the log never fails the write it describes.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

LOGGER = logging.getLogger("upstream_edge.obsidian_db")

# Obsidian's `ChangeSource::External`. The integer is a published contract.
SOURCE_EXTERNAL = 3
COALESCE_SECONDS = 30

FORECAST = "Forecast"
MAIN = "Main"
INTEREST = "Interest"
WELL_MODELS = "WellModels"
CAPEX = "Capex"
RUN_SETTINGS = "RunSettings"
PRICE_MODEL = "PriceModel"
EXPENSE_MODEL = "ExpenseModel"
TAX_MODEL = "TaxModel"
DIFF_MODEL = "DiffModel"
SHRINK_YIELD_MODEL = "ShrinkYieldModel"


@dataclass(frozen=True, slots=True)
class Change:
    """One changed object: the key of a ChangeLog row."""

    table: str
    prop_id: str | None
    detail: str | None


def well_change(table: str, prop_id: str, detail: str) -> Change:
    """A change to one well's data in ``table``."""
    return Change(table, prop_id, detail)


def table_change(table: str, name: str) -> Change:
    """A change not tied to one well: a named model, a run-settings set."""
    return Change(table, None, name)


class ChangeRecorder:
    """Collects the changes of one transaction and writes them when it commits."""

    def __init__(self, *, enabled: bool, user: str | None) -> None:
        self._enabled = enabled
        self._user = user
        self._pending: dict[Change, None] = {}
        self._wrote = False
        # Key -> the time of the row this connection last wrote for it.
        self._logged_at: dict[Change, int] = {}
        self._flushed: list[tuple[Change, int]] = []

    def wrote(self) -> None:
        """A writer ran, logged or not: the commit stamps the file as modified."""
        self._wrote = True

    def add(self, changes: Iterable[Change]) -> None:
        """Queue the changes a writer made."""
        if not self._enabled:
            return
        for change in changes:
            self._pending.setdefault(change, None)

    def forget_well(self, conn: sqlite3.Connection, prop_id: str) -> None:
        """Drop every row of a well that is being deleted.

        Obsidian does the same: the newest row per key is kept for ever, so the
        rows of deleted wells would otherwise grow the log without bound.
        """
        self._pending = {change: None for change in self._pending if change.prop_id != prop_id}
        self._logged_at = {
            change: ts for change, ts in self._logged_at.items() if change.prop_id != prop_id
        }
        try:
            conn.execute("DELETE FROM ChangeLog WHERE prop_id = ?", (prop_id,))
        except sqlite3.Error as exc:
            LOGGER.debug("ChangeLog rows of %s not removed: %s", prop_id, exc)

    def flush(self, conn: sqlite3.Connection) -> None:
        """Write the queued rows and the file's last-modified stamp. Called
        inside the transaction, just before it commits."""
        if not self._wrote:
            return
        now = time.time()
        ts = int(now)
        rows: list[tuple[Change, int]] = []
        for change in self._pending:
            logged = self._logged_at.get(change)
            if (
                logged is not None
                and 0 <= ts - logged < COALESCE_SECONDS
                and self._is_newest(conn, change, logged)
            ):
                continue
            rows.append((change, ts))
        self._pending = {}
        self._flushed = []
        if rows:
            try:
                conn.executemany(
                    "INSERT INTO ChangeLog (ts, table_name, prop_id, detail, source, user) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            ts,
                            change.table,
                            change.prop_id,
                            change.detail,
                            SOURCE_EXTERNAL,
                            self._user,
                        )
                        for change, _ in rows
                    ],
                )
                self._flushed = rows
            except sqlite3.Error as exc:
                # A file last opened by an Obsidian from before the log has no
                # table; the write itself stands.
                # The next write tries again: Obsidian may have opened the file
                # and added the table in between.
                LOGGER.info("ChangeLog rows not written (%s); the write is unaffected", exc)
        _stamp_last_modified(conn, now)

    def _is_newest(self, conn: sqlite3.Connection, change: Change, logged: int) -> bool:
        """Whether the row this connection wrote at ``logged`` is still the
        newest for the object. If Obsidian, or anyone else, has logged a change
        to it since, a repeat is a new change and gets its own row, or the log
        would name them as the last to change it."""
        try:
            row = conn.execute(
                "SELECT ts, source, user FROM ChangeLog "
                "WHERE table_name = ? AND prop_id IS ? AND detail IS ? ORDER BY id DESC LIMIT 1",
                (change.table, change.prop_id, change.detail),
            ).fetchone()
        except sqlite3.Error:
            return True
        return row is not None and tuple(row) == (logged, SOURCE_EXTERNAL, self._user)

    def committed(self) -> None:
        """The transaction committed: its rows are on disk."""
        for change, ts in self._flushed:
            self._logged_at[change] = ts
        self._reset()

    def rolled_back(self) -> None:
        """The transaction rolled back: nothing was written."""
        self._reset()

    def _reset(self) -> None:
        self._pending = {}
        self._flushed = []
        self._wrote = False


def _stamp_last_modified(conn: sqlite3.Connection, now: float) -> None:
    """Set ``Metadata.LastModified``, as every Obsidian write does."""
    stamp = datetime.fromtimestamp(now).astimezone().isoformat()
    try:
        updated = conn.execute(
            "UPDATE Metadata SET value = ? WHERE type = 'LastModified'", (stamp,)
        ).rowcount
        if updated == 0:
            conn.execute("INSERT INTO Metadata (type, value) VALUES ('LastModified', ?)", (stamp,))
    except sqlite3.Error as exc:
        LOGGER.debug("Metadata.LastModified not stamped: %s", exc)
