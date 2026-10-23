"""Column-only API serialization with exact monetary values."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect

from app.models import Record


def record(row: Record) -> dict[str, Any]:
    """Serialize mapped columns without loading relationships or provider payloads."""
    state = inspect(row)
    assert state is not None, "Only mapped records can be serialized"
    result = {}
    for column in state.mapper.column_attrs:
        value = getattr(row, column.key)
        if isinstance(value, Decimal):
            value = format(value, ".2f")
        elif isinstance(value, datetime):
            value = value.isoformat()
        result[column.key] = value
    return result
