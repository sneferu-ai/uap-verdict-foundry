"""Battery category adapter base contract (spec §5 plugin interface).

Every battery category is an adapter class conforming to
``BatteryCategoryAdapter``. Adapters never raise for unreachable sources —
they return `insufficient` with an honest reason and a sentinel source stamp
(FR-006). Construction failures (import error, missing class, bad params)
are surfaced as BatteryConfigError and fail the case (unrecoverable).
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional


class BatteryConfigError(Exception):
    """Battery configuration could not be loaded/constructed (unrecoverable)."""


class BatteryCategoryAdapter:
    """Interface for one battery category.

    run(media_path, metadata, lineage_outputs, config) returns:
        {"result": "positive|negative|insufficient",
         "evidence_citation": str | None,
         "source_stamp": {...}}
    """

    name = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params = dict(params or {})

    def run(self, media_path, metadata: dict, lineage_outputs, config: dict) -> dict:
        raise NotImplementedError


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def sentinel_stamp(category: str, reason: str, utc_timestamp: str) -> dict:
    """FR-006/FR-010 sentinel stamp for unreachable/unconfigured sources.
    Satisfies the NOT NULL constraint while honestly recording that no data
    was retrieved."""
    return {
        "source_id": f"{category}_unavailable",
        "query_params": "{}",
        "utc_timestamp": utc_timestamp,
        "content_hash": "unavailable",
        "source_version": "unavailable",
        "reason": reason,
    }
