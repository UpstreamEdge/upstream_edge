"""Internal SQLite helpers."""

import sqlite3

_NO_SUCH_TABLE = "no such table: "


def quote_identifier(identifier: str) -> str:
    """Return a safely quoted SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


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
