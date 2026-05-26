"""Runtime validation helpers for public API methods."""

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


def validate_api10(api_10: str | None, *, method: str) -> None:
    if api_10 is not None and (len(api_10) != 10 or not api_10.isdigit()):
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


def validate_percentage(value: float, *, field: str, method: str) -> None:
    if value < 0.0 or value > 1.0:
        raise ValidationError(f"{method}: {field} must be between 0 and 1; got {value!r}")
