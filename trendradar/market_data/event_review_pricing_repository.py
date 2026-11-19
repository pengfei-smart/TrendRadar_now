"""Immutable, source-traceable model pricing catalog for review usage.

Prices are versioned by provider/model and effective time. Overlapping
validity windows are rejected, and historical entries cannot be edited or
deleted. This module does not perform token-cost calculations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import uuid
from urllib.parse import urlsplit

from .event_review_temporal import parse_aware_datetime
from .storage import MarketStorage


@dataclass(frozen=True)
class ModelPricingRecord:
    pricing_id: str
    provider: str
    model: str
    currency: str
    input_price_per_million: Decimal
    output_price_per_million: Decimal
    valid_from: str
    valid_until: str | None
    source_url: str
    source_checked_at: str


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _timestamp(value: str, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    parsed = parse_aware_datetime(value)
    if parsed is None:
        raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone")
    return value.strip(), parsed


def _price(value: Decimal | str | int, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field} must be Decimal, string, or integer; float is not accepted")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be a finite non-negative decimal")
    return result


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _record(row) -> ModelPricingRecord:
    return ModelPricingRecord(
        pricing_id=row["pricing_id"],
        provider=row["provider"],
        model=row["model"],
        currency=row["currency"],
        input_price_per_million=Decimal(row["input_price_per_million"]),
        output_price_per_million=Decimal(row["output_price_per_million"]),
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        source_url=row["source_url"],
        source_checked_at=row["source_checked_at"],
    )


class EventReviewPricingRepository:
    """Append-only price catalog with exact provider/model matching."""

    def __init__(self, storage: MarketStorage):
        if not isinstance(storage, MarketStorage):
            raise TypeError("storage must be a MarketStorage instance")
        self.storage = storage

    def add_price(
        self,
        *,
        provider: str,
        model: str,
        currency: str,
        input_price_per_million: Decimal | str | int,
        output_price_per_million: Decimal | str | int,
        valid_from: str,
        valid_until: str | None,
        source_url: str,
        source_checked_at: str,
        pricing_id: str | None = None,
    ) -> ModelPricingRecord:
        provider_value = _required_text(provider, "provider")
        model_value = _required_text(model, "model")
        currency_value = _required_text(currency, "currency")
        if len(currency_value) != 3 or not currency_value.isascii() or not currency_value.isalpha() or currency_value.upper() != currency_value:
            raise ValueError("currency must be a three-letter uppercase currency code")
        input_price = _price(input_price_per_million, "input_price_per_million")
        output_price = _price(output_price_per_million, "output_price_per_million")
        valid_from_text, valid_from_dt = _timestamp(valid_from, "valid_from")
        if valid_until is None:
            valid_until_text = None
            valid_until_dt = None
        else:
            valid_until_text, valid_until_dt = _timestamp(valid_until, "valid_until")
            if valid_until_dt <= valid_from_dt:
                raise ValueError("valid_until must be later than valid_from")
        source_value = _required_text(source_url, "source_url")
        parsed_source = urlsplit(source_value)
        if parsed_source.scheme not in {"https", "http"} or not parsed_source.netloc:
            raise ValueError("source_url must be an absolute HTTP(S) URL")
        checked_text, _ = _timestamp(source_checked_at, "source_checked_at")
        identifier = pricing_id or str(uuid.uuid4())
        identifier = _required_text(identifier, "pricing_id")

        # Serialize writers before checking interval overlap, preventing two
        # concurrent catalog updates from creating overlapping active prices.
        with self.storage.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT valid_from,valid_until FROM event_review_model_pricing
                   WHERE provider=? AND model=?""",
                (provider_value, model_value),
            ).fetchall()
            for row in existing:
                old_start = parse_aware_datetime(row["valid_from"])
                old_end = parse_aware_datetime(row["valid_until"]) if row["valid_until"] else None
                if row["valid_until"] and old_end is None:
                    raise RuntimeError("pricing catalog contains an invalid valid_until timestamp")
                new_end = valid_until_dt
                if old_start is None:
                    raise RuntimeError("pricing catalog contains an invalid valid_from timestamp")
                if (new_end is None or old_start < new_end) and (
                    old_end is None or valid_from_dt < old_end
                ):
                    raise ValueError("pricing validity window overlaps an existing provider/model price")
            connection.execute(
                """INSERT INTO event_review_model_pricing (
                    pricing_id,provider,model,currency,input_price_per_million,
                    output_price_per_million,valid_from,valid_until,source_url,
                    source_checked_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifier, provider_value, model_value, currency_value,
                    _decimal_text(input_price), _decimal_text(output_price),
                    valid_from_text, valid_until_text, source_value, checked_text,
                ),
            )
            row = connection.execute(
                "SELECT * FROM event_review_model_pricing WHERE pricing_id=?",
                (identifier,),
            ).fetchone()
        return _record(row)

    def list_prices(
        self, *, provider: str | None = None, model: str | None = None,
    ) -> tuple[ModelPricingRecord, ...]:
        clauses: list[str] = []
        params: list[str] = []
        if provider is not None:
            clauses.append("provider=?")
            params.append(_required_text(provider, "provider"))
        if model is not None:
            clauses.append("model=?")
            params.append(_required_text(model, "model"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.storage.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM event_review_model_pricing" + where
                + " ORDER BY provider,model,valid_from,pricing_id",
                params,
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def find_price(
        self, *, provider: str, model: str, at: str,
    ) -> ModelPricingRecord | None:
        provider_value = _required_text(provider, "provider")
        model_value = _required_text(model, "model")
        at_text, at_dt = _timestamp(at, "at")
        del at_text
        with self.storage.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM event_review_model_pricing
                   WHERE provider=? AND model=?""",
                (provider_value, model_value),
            ).fetchall()
        matches = []
        for row in rows:
            start = parse_aware_datetime(row["valid_from"])
            end = parse_aware_datetime(row["valid_until"]) if row["valid_until"] else None
            if row["valid_until"] and end is None:
                raise RuntimeError("pricing catalog contains an invalid valid_until timestamp")
            if start is None:
                raise RuntimeError("pricing catalog contains an invalid valid_from timestamp")
            if start <= at_dt and (end is None or at_dt < end):
                matches.append(row)
        if len(matches) > 1:
            raise RuntimeError("pricing catalog contains ambiguous overlapping prices")
        return _record(matches[0]) if matches else None


__all__ = ["EventReviewPricingRepository", "ModelPricingRecord"]
