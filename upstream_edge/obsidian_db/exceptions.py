"""Typed exceptions raised by the Obsidian database client."""


class ObsidianDbError(Exception):
    """Base class for all library-specific exceptions."""


class DatabaseLockedError(ObsidianDbError):
    """Raised when SQLite cannot acquire the write lock."""


class DataIntegrityError(ObsidianDbError):
    """Raised when database contents or schema cannot be interpreted."""


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
