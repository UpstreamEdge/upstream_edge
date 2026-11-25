"""Runtime validation helpers for public API methods."""

import math
from datetime import date

from .exceptions import ValidationError

MIN_DATE = date(1900, 1, 1)


def require_non_empty(value: str, *, field: str, method: str) -> None:
    if value == "":
        raise ValidationError(f"{method}: {field} must be a non-empty string")


def reject_reserved_name(value: str, *, field: str, method: str) -> None:
    require_non_empty(value, field=field, method=method)
    if "<>" in value:
        raise ValidationError(
            f"{method}: {field} {value!r} contains a disallowed character sequence"
        )


def validate_new_prop_id(prop_id: object, *, field: str, method: str) -> None:
    """An id Obsidian can load. It keeps a PropID in 16 bytes and skips, at
    load, a well whose id is longer, so such a well would exist in the file
    and never appear; ``<>`` is how it names a well's own models."""
    if not isinstance(prop_id, str) or not prop_id.strip():
        raise ValidationError(f"{method}: {field} must be a non-empty string; got {prop_id!r}")
    if prop_id != prop_id.strip() or any(ord(character) < 32 for character in prop_id):
        raise ValidationError(
            f"{method}: {field} {prop_id!r} has leading or trailing spaces or a control character"
        )
    if "<>" in prop_id:
        raise ValidationError(f"{method}: {field} {prop_id!r} contains '<>', which is reserved")
    if len(prop_id.encode("utf-8")) > 16:
        raise ValidationError(
            f"{method}: {field} {prop_id!r} is longer than the 16 bytes Obsidian keeps a "
            "PropID in; Obsidian would not load the well"
        )


def validate_api10(api_10: str | None, *, method: str) -> None:
    if api_10 is not None and (len(api_10) != 10 or not (api_10.isascii() and api_10.isdigit())):
        raise ValidationError(f"{method}: api_10 must be exactly ten digits; got {api_10!r}")


def validate_date(value: date, *, field: str, method: str) -> None:
    if value < MIN_DATE:
        raise ValidationError(
            f"{method}: {field} must be on or after {MIN_DATE.isoformat()}; got {value.isoformat()}"
        )


def validate_month(value: date, *, field: str, method: str) -> None:
    validate_date(value, field=field, method=method)
    if value.day != 1:
        raise ValidationError(
            f"{method}: {field} must be the first day of the month; got {value.isoformat()}"
        )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def require_number(value: object, *, field: str, method: str) -> None:
    """A finite number. NaN, an infinity or None would be stored as an empty
    cell, which reads back as zero in Obsidian."""
    if not _is_number(value) or not math.isfinite(float(str(value))):
        raise ValidationError(f"{method}: {field} must be a number; got {value!r}")


def validate_percentage(value: float, *, field: str, method: str) -> None:
    if not _is_number(value) or not (0.0 <= value <= 1.0):
        raise ValidationError(
            f"{method}: {field} must be a fraction between 0 and 1 "
            f"(e.g. 0.75 for 75%); got {value!r}"
        )


def validate_percent_0_100(value: float, *, field: str, method: str) -> None:
    if not _is_number(value) or not (0.0 <= value <= 100.0):
        raise ValidationError(
            f"{method}: {field} must be a percentage between 0 and 100 "
            f"(e.g. 75.0 for 75%); got {value!r}"
        )
