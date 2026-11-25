"""Per-well field references of the shared econ models.

A field of an expense, tax, differential or shrink/yield model can read each
well's own value from a well attribute column instead of using the number in
the model. Obsidian keeps these in one side table, ``EconModelFieldRefs``: one
row per model, segment and field, naming the attribute column. While a row
exists, the number stored on the model is only a fallback.

A row belongs to a segment through a key Obsidian computes from the segment's
start: ``SIMPLE`` for a model with one segment, ``AGEBASED(n)`` or
``DATEBASED(YYYY-MM-DD)`` otherwise, and always ``DATEBASED(first of the
month)`` for a differential segment. A row whose key matches no segment of its
model is never used.

This module reads the rows and keeps them true when the library changes what
they point at: a model replaced, deleted or copied, an attribute column renamed
or deleted. A file without the table (it predates it) has no references.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from .enums import ModelKind
from .exceptions import ValidationError
from .models import ModelFieldRef

TABLE = "EconModelFieldRefs"

# `EconModelFieldRefs.model_type` for each kind that can hold a reference.
DB_TYPE: dict[ModelKind, str] = {
    ModelKind.EXPENSE: "Expense",
    ModelKind.TAX: "Tax",
    ModelKind.DIFF: "Diff",
    ModelKind.SHRINK_YIELD: "ShrinkYield",
}
_KIND_OF_DB_TYPE: dict[str, ModelKind] = {text: kind for kind, text in DB_TYPE.items()}

# Each kind's table, name column, and the column that tells its segments apart.
_MODEL_TABLE: dict[ModelKind, tuple[str, str, str | None]] = {
    ModelKind.EXPENSE: ("ExpenseModel", "exp_model_name", "model_type"),
    ModelKind.TAX: ("TaxModel", "tax_model_name", "model_type"),
    ModelKind.DIFF: ("DiffModel", "diff_model_name", "start_date"),
    ModelKind.SHRINK_YIELD: ("ShrinkYieldModel", "shrink_yield_model_name", None),
}

# The fields of each kind that can be read per well. Obsidian looks up no
# others, so a row naming another field is never used.
FIELDS: dict[ModelKind, frozenset[str]] = {
    ModelKind.EXPENSE: frozenset(
        {"fixed_monthly", "variable_oil", "variable_gas", "variable_water"}
    ),
    ModelKind.TAX: frozenset(
        {"sev_tax_oil", "sev_tax_gas", "sev_tax_ngl", "ad_valorum_tax", "ad_valorem_tax"}
    ),
    ModelKind.DIFF: frozenset({"oil_diff", "gas_diff", "ngl_diff", "condensate_diff"}),
    ModelKind.SHRINK_YIELD: frozenset(
        {"gas_shrink_frac", "ngl_yield_bbl_mmscf", "condensate_yield_bbl_mmscf"}
    ),
}

SIMPLE_KEY = "SIMPLE"
# Where Obsidian starts a SIMPLE segment when its model has other segments too.
_SIMPLE_START = date(2000, 1, 1)

# The database spells the ad valorem rate `ad_valorum_tax`; a later Obsidian
# will write `ad_valorem_tax`. Either is read, and reported under the correct
# spelling.
AD_VALOREM = "ad_valorem_tax"
AD_VALORUM = "ad_valorum_tax"

# When a segment starts: (0, a date) or (1, an age in months). The order is
# the one Obsidian sorts a model's segments in, dated starts first.
Start = tuple[int, date | int]


def dated_start(value: date) -> Start:
    return (0, value)


def aged_start(months: int) -> Start:
    return (1, months)


def simple_start() -> Start:
    return (0, _SIMPLE_START)


def month_start(value: date) -> Start:
    """A differential segment's start: Obsidian keys it by its month."""
    return (0, value.replace(day=1))


def table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1", (TABLE,)
        ).fetchone()
        is not None
    )


def read(
    conn: sqlite3.Connection, kind: ModelKind | None = None, name: str | None = None
) -> list[ModelFieldRef]:
    """The references Obsidian uses, each with the place of its segment among
    the model's segments as the library's reader returns them."""
    if not table_exists(conn):
        return []
    clauses: list[str] = []
    params: list[object] = []
    if kind is not None:
        clauses.append("model_type = ?")
        params.append(DB_TYPE.get(kind, ""))
    if name is None:
        # A well's own models are listed only when asked for by name, as the
        # model readers do.
        clauses.append("instr(model_name, '<>') = 0")
    else:
        clauses.append("model_name = ?")
        params.append(name)
    rows = conn.execute(
        f"""
        SELECT model_type, model_name, segment_key, field_name, attr_column
        FROM {TABLE}
        WHERE {" AND ".join(clauses)}
        """,
        tuple(params),
    ).fetchall()
    keys_of: dict[tuple[ModelKind, str], list[str]] = {}
    # One reference per model, segment and field. The older spelling of the ad
    # valorem key is taken first, so it is the one kept where a file has both.
    refs: dict[tuple[str, str, int, str], ModelFieldRef] = {}
    for row in sorted(rows, key=lambda row: str(row["field_name"]) != AD_VALORUM):
        model_kind = _KIND_OF_DB_TYPE.get(str(row["model_type"]))
        column = str(row["attr_column"] or "").strip()
        if model_kind is None or not column or str(row["field_name"]) not in FIELDS[model_kind]:
            continue
        model_name = str(row["model_name"])
        model = (model_kind, model_name)
        if model not in keys_of:
            keys_of[model] = _reader_order_keys(conn, model_kind, model_name)
        key = str(row["segment_key"])
        field = _field_out(str(row["field_name"]))
        # Two differential segments in one month share a key, and so a reference.
        for segment, segment_key in enumerate(keys_of[model]):
            if segment_key == key:
                refs.setdefault(
                    (model_kind.value, model_name, segment, field),
                    ModelFieldRef(
                        kind=model_kind,
                        name=model_name,
                        segment=segment,
                        field=field,
                        column=column,
                    ),
                )
    return [refs[place] for place in sorted(refs)]


def live_fields(conn: sqlite3.Connection, kind: ModelKind, name: str) -> list[tuple[str, str]]:
    """``(field, attribute column)`` for each reference Obsidian uses on the model."""
    return [(ref.field, ref.column) for ref in read(conn, kind, name)]


def carry(
    conn: sqlite3.Connection,
    kind: ModelKind,
    name: str,
    new_starts: list[Start],
    *,
    method: str,
) -> None:
    """Move a model's references onto the segments about to replace its own.

    Call before the model's rows are replaced. Each reference goes to the new
    segment in the same place, both sides ordered by start, which is what
    Obsidian's own save through its API does. A replacement with a different
    number of segments has no "same place", so it is refused.
    """
    if not table_exists(conn):
        return
    old_keys = _keys(sorted(_stored_starts(conn, kind, name), key=_order), kind)
    new_keys = _keys(sorted(new_starts, key=_order), kind)
    rows = conn.execute(
        f"SELECT segment_key, field_name, attr_column FROM {TABLE} "
        "WHERE model_type = ? AND model_name = ?",
        (DB_TYPE[kind], name),
    ).fetchall()
    live = [row for row in rows if str(row["segment_key"]) in old_keys]
    if not live:
        return
    if len(set(new_keys)) != len(new_keys):
        together = next(key for key in new_keys if new_keys.count(key) > 1)
        raise ValidationError(
            f"{method}: the {kind.value} model {name!r} has per-well field references, and two "
            f"of this call's segments start together ({together}; a differential segment "
            "starts with its month, and a SIMPLE segment on 2000-01-01). A reference could "
            "not tell them apart; give each segment its own start."
        )
    if len(old_keys) != len(new_keys):
        fields = ", ".join(
            f"{_field_out(str(row['field_name']))} (column {str(row['attr_column'])!r})"
            for row in live
        )
        raise ValidationError(
            f"{method}: the {kind.value} model {name!r} has per-well field references "
            f"({fields}), each tied to one of its {len(old_keys)} segments. Write it with "
            f"{len(old_keys)} segments (this call has {len(new_keys)}), or remove the "
            "references in Obsidian's model editor first."
        )
    if old_keys == new_keys:
        return
    carried: list[tuple[str, str, str]] = []
    for row in rows:
        key = str(row["segment_key"])
        if key in old_keys:
            key = new_keys[old_keys.index(key)]
        elif key in new_keys:
            # Tied to no segment before; it must not start applying to one now.
            continue
        carried.append((key, str(row["field_name"]), str(row["attr_column"])))
    conn.execute(
        f"DELETE FROM {TABLE} WHERE model_type = ? AND model_name = ?", (DB_TYPE[kind], name)
    )
    conn.executemany(
        f"INSERT OR REPLACE INTO {TABLE} "
        "(model_type, model_name, segment_key, field_name, attr_column) VALUES (?, ?, ?, ?, ?)",
        [(DB_TYPE[kind], name, key, field, column) for key, field, column in carried],
    )


def delete_for_model(conn: sqlite3.Connection, kind: ModelKind, name: str) -> None:
    """A deleted model takes its references with it, so a later model of the
    same name does not inherit them."""
    if table_exists(conn):
        conn.execute(
            f"DELETE FROM {TABLE} WHERE model_type = ? AND model_name = ?", (DB_TYPE[kind], name)
        )


def delete_for_model_name(conn: sqlite3.Connection, name: str) -> None:
    """Every kind's references under one model name (a well's own models share one)."""
    if table_exists(conn):
        conn.execute(f"DELETE FROM {TABLE} WHERE model_name = ?", (name,))


def copy_for_model(conn: sqlite3.Connection, db_type: str, from_name: str, to_name: str) -> None:
    """Give a copied model the references of the one it was copied from."""
    if not table_exists(conn):
        return
    conn.execute(f"DELETE FROM {TABLE} WHERE model_type = ? AND model_name = ?", (db_type, to_name))
    conn.execute(
        f"""
        INSERT INTO {TABLE} (model_type, model_name, segment_key, field_name, attr_column)
        SELECT model_type, ?, segment_key, field_name, attr_column
        FROM {TABLE} WHERE model_type = ? AND model_name = ?
        """,
        (to_name, db_type, from_name),
    )


def rename_column(conn: sqlite3.Connection, old: str, new: str) -> None:
    """References follow a renamed attribute column."""
    if table_exists(conn):
        conn.execute(
            f"UPDATE {TABLE} SET attr_column = ? WHERE lower(attr_column) = lower(?)", (new, old)
        )


def models_on_column(conn: sqlite3.Connection, column: str) -> list[str]:
    """The models with a field that reads the attribute column, as ``Expense 'NAME'``."""
    if not table_exists(conn):
        return []
    rows = conn.execute(
        f"SELECT DISTINCT model_type, model_name FROM {TABLE} "
        "WHERE lower(attr_column) = lower(?) ORDER BY model_type, model_name",
        (column,),
    ).fetchall()
    return [f"{row['model_type']} {str(row['model_name'])!r}" for row in rows]


def detach_column(conn: sqlite3.Connection, column: str) -> None:
    """Drop every reference to the attribute column; the models keep their stored numbers."""
    if table_exists(conn):
        conn.execute(f"DELETE FROM {TABLE} WHERE lower(attr_column) = lower(?)", (column,))


def _field_out(field: str) -> str:
    return AD_VALOREM if field == AD_VALORUM else field


def _keys(starts: list[Start], kind: ModelKind) -> list[str]:
    if kind is ModelKind.SHRINK_YIELD:
        return [SIMPLE_KEY for _ in starts]
    # A differential model is always dated; the others call one segment SIMPLE.
    single = len(starts) == 1 and kind is not ModelKind.DIFF
    return [SIMPLE_KEY if single else _key(start) for start in starts]


def _order(start: Start) -> tuple[int, int]:
    aged, value = start
    return (aged, value.toordinal() if isinstance(value, date) else value)


def _key(start: Start) -> str:
    aged, value = start
    if aged or not isinstance(value, date):
        return f"AGEBASED({value})"
    return f"DATEBASED({value.isoformat()})"


def _stored_starts(conn: sqlite3.Connection, kind: ModelKind, name: str) -> list[Start]:
    """The starts of the model's segments in the file, in the reader's order."""
    table, name_column, segment_column = _MODEL_TABLE[kind]
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1", (table,)
        ).fetchone()
        is None
    ):
        return []
    if segment_column is None:
        found = conn.execute(
            f"SELECT 1 FROM {table} WHERE {name_column} = ? LIMIT 1", (name,)
        ).fetchone()
        return [simple_start()] if found is not None else []
    rows = conn.execute(
        f"SELECT {segment_column} FROM {table} WHERE {name_column} = ? ORDER BY {segment_column}",
        (name,),
    ).fetchall()
    starts: list[Start] = []
    for row in rows:
        start = (
            _diff_start(row[0]) if kind is ModelKind.DIFF else _model_type_start(str(row[0] or ""))
        )
        if start is not None:
            starts.append(start)
    return starts


def _reader_order_keys(conn: sqlite3.Connection, kind: ModelKind, name: str) -> list[str]:
    """Each segment's key, in the order the model's reader returns the segments."""
    return _keys(_stored_starts(conn, kind, name), kind)


def _diff_start(value: object) -> Start | None:
    try:
        return month_start(date.fromisoformat(str(value)))
    except ValueError:
        return None


def _model_type_start(text: str) -> Start | None:
    """An expense or tax row's start from its ``model_type`` text."""
    if text == SIMPLE_KEY:
        return simple_start()
    try:
        if text.startswith("AGEBASED(") and text.endswith(")"):
            return aged_start(int(text[9:-1]))
        if text.startswith("DATEBASED(") and text.endswith(")"):
            return dated_start(date.fromisoformat(text[10:-1]))
    except ValueError:
        return None
    return None
