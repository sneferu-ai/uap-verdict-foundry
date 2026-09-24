"""Fixed lineage classification taxonomy (spec §3.2).

Exactly 10 categories. No ``unknown``. No ``weather`` (weather is a battery
environmental condition, not a lineage classification). Lineage outputs that
name a category outside this set are invalid and rejected at the protocol
boundary.
"""
from __future__ import annotations

from typing import FrozenSet

TAXONOMY: FrozenSet[str] = frozenset(
    {
        "aircraft",
        "satellite",
        "balloon",
        "drone",
        "bird",
        "insect",
        "meteor",
        "lens_artifact",
        "sensor_artifact",
        "astronomical",
    }
)

EXPECTED_CATEGORY_COUNT = 10


def is_valid_taxonomy(value) -> bool:
    """True when *value* is one of the 10 lineage categories."""
    return isinstance(value, str) and value in TAXONOMY


def assert_taxonomy() -> None:
    """Structural self-check: the taxonomy has exactly 10 categories and
    contains neither ``unknown`` nor ``weather``. Raises AssertionError on
    drift (guards against accidental edits to TAXONOMY)."""
    assert len(TAXONOMY) == EXPECTED_CATEGORY_COUNT, (
        f"taxonomy must have exactly {EXPECTED_CATEGORY_COUNT} categories, "
        f"has {len(TAXONOMY)}"
    )
    assert "unknown" not in TAXONOMY, "taxonomy must not contain 'unknown'"
    assert "weather" not in TAXONOMY, "taxonomy must not contain 'weather'"
