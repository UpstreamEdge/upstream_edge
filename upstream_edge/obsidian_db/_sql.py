"""Internal SQLite helpers."""

import math
import sqlite3
import struct
from datetime import date

from .exceptions import DataIntegrityError

_NO_SUCH_TABLE = "no such table: "


def quote_identifier(identifier: str) -> str:
    """Return a safely quoted SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


# Longer than any real forecast (100 years); guards against a corrupt blob.
_MONTHS_MAX = 1200


def decode_monthly_volumes(value: object, *, field: str) -> tuple[float, ...]:
    """Decode one phase of stored monthly forecast volumes: little-endian
    32-bit floats, one per month."""
    if value is None:
        return ()
    if not isinstance(value, bytes):
        raise DataIntegrityError(f"{field} must be stored as a blob; got {type(value).__name__}")
    count, rest = divmod(len(value), 4)
    if rest or count > _MONTHS_MAX:
        raise DataIntegrityError(f"{field} is not a series of monthly volumes ({len(value)} bytes)")
    return struct.unpack(f"<{count}f", value)


def parse_date(value: object) -> date | None:
    """A stored date, read as Obsidian reads one: ``YYYY-MM-DD``, or that
    followed by a time, which some importers write. None for anything else."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    if len(text) > 10 and text[10] in " T":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


Series = tuple[float, ...]


def stored_monthly_volumes(
    oil: object, gas: object, water: object
) -> tuple[Series, Series, Series]:
    """Decode the three phases of one ForecastVolumes row.

    Raises DataIntegrityError for a row Obsidian itself does not use: it skips,
    at load, a row with no months, with phases of different lengths, or with a
    volume that is negative or not a number.
    """
    series = (
        decode_monthly_volumes(oil, field="ForecastVolumes.oil_bbl"),
        decode_monthly_volumes(gas, field="ForecastVolumes.gas_mscf"),
        decode_monthly_volumes(water, field="ForecastVolumes.water_bbl"),
    )
    lengths = [len(volumes) for volumes in series]
    if lengths[0] == 0:
        raise DataIntegrityError("the row holds no months")
    if len(set(lengths)) != 1:
        raise DataIntegrityError(
            f"the phases hold different numbers of months ({', '.join(map(str, lengths))})"
        )
    if any(not math.isfinite(volume) or volume < 0.0 for volumes in series for volume in volumes):
        raise DataIntegrityError("a volume is negative or not a number")
    return series


def tax_ad_valorem_column(conn: sqlite3.Connection) -> str:
    """The name of TaxModel's ad valorem column in this file.

    Obsidian has always written ``ad_valorum_tax``, a misspelling; a later
    version will write ``ad_valorem_tax``. The file is read and written under
    whichever it has, and the old name wins in a file with both, as it does in
    Obsidian.
    """
    names = {str(row[1]) for row in conn.execute("PRAGMA table_info(TaxModel)").fetchall()}
    if "ad_valorem_tax" in names and "ad_valorum_tax" not in names:
        return "ad_valorem_tax"
    return "ad_valorum_tax"


def missing_table_name(exc: sqlite3.OperationalError) -> str | None:
    """Return the table a SQLite "no such table" error names, or None for any other error.

    SQLite reports every such error with one fixed, unlocalized message and a
    generic error code, so the message is the only way to tell. A statement
    names its table unqualified; a trigger's statement is reported with the
    ``main.`` schema, which is dropped.
    """
    message = str(exc)
    if not message.startswith(_NO_SUCH_TABLE):
        return None
    return message.removeprefix(_NO_SUCH_TABLE).removeprefix("main.")
