"""Authenticated HTTP resource routers and shared request header constraints."""

from typing import Annotated

from fastapi import Header

Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
