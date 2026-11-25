"""Typed exceptions raised by the Obsidian database client."""


class ObsidianDbError(Exception):
    """Base class for all library-specific exceptions."""


class DatabaseLockedError(ObsidianDbError):
    """Raised when SQLite cannot acquire the write lock."""


class ReadOnlyDatabaseError(ObsidianDbError):
    """Raised when a write is attempted on a database that was opened read-only.

    ``Database.open(path, read_only=True)`` opens a file this way.
    """


class DataIntegrityError(ObsidianDbError):
    """Raised when database contents or schema cannot be interpreted."""


class MissingTableError(DataIntegrityError):
    """Raised when an operation needs a table the database file does not have.

    Obsidian adds every table its current version uses each time it opens a
    database, so this almost always means the file predates that table: it was
    last saved by an older Obsidian, or built by another tool.
    """

    def __init__(self, table: str, message: str | None = None) -> None:
        self.table = table
        super().__init__(
            message
            or (
                f"table {table!r} is missing: this database predates the current "
                "Obsidian schema, or was not created by Obsidian. Open it once in "
                "Obsidian, which adds every current table, then retry."
            )
        )


class WellNotFoundError(ObsidianDbError):
    """Raised when a writer references a PropID absent from Main."""

    def __init__(self, prop_id: str, message: str | None = None) -> None:
        self.prop_id = prop_id
        super().__init__(message or f"well not found: {prop_id!r}")


class ModelNotFoundError(ObsidianDbError):
    """Raised when a writer references a missing named model."""

    def __init__(self, model_kind: str, name: str, message: str | None = None) -> None:
        self.model_kind = model_kind
        self.name = name
        super().__init__(message or f"{model_kind} model not found: {name!r}")


class DuplicateError(ObsidianDbError):
    """Raised when an add operation targets an existing key."""


class ValidationError(ObsidianDbError):
    """Raised when a public method receives invalid input."""


class MissingDependencyError(ObsidianDbError):
    """Raised when an optional dependency is required but not installed."""


class MonthlyVolumesWarning(UserWarning):
    """Warned when a forecast segment is written under stored monthly volumes.

    A forecast can start with stored monthly volumes (a saved Spatial
    Forecasting run writes them). Obsidian starts the phase at the first of
    them and uses them as they are for the months they cover, so a segment has
    no effect before they end. ``set_forecast`` never changes them; a segment
    that starts after them is the ordinary case and does not warn.
    """


class ModelFieldRefWarning(UserWarning):
    """Warned when a shared econ model is written while one of its fields is
    read per well.

    A field of an expense, tax, differential or shrink/yield model can take each
    well's own value from a well attribute column (Obsidian's model editor sets
    this up, and an ARIES import does). The number written for such a field is
    only the stored fallback: every well keeps being charged its own value.
    ``Database.model_field_refs()`` lists them.
    """
