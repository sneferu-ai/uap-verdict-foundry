"""Weather battery category (FR-006 plugin interface).

NOT WIRED IN THE MVP DEFAULT BATTERY. The four-category FR-006 default
excludes weather (spec §1 resolution 13) and §1 marks
``weather_phenomenon`` UNTESTED, forcing the uncovered-mundane
amendment. The category also has a known evidence-quality hazard: its
only negative path derives from the BUYER's own free-text weather field
(no independent meteorological source ships with the product), which is
why it stays out of the default and remains a §5 plugin example only.

Weather is a battery ENVIRONMENTAL condition, deliberately not a lineage
taxonomy category. The category consumes two possible inputs:

  * the operator's own capture-time weather record (metadata ``weather``):
    when present and non-explanatory (clear/calm conditions that cannot
    account for the observation) the category tests NEGATIVE against the
    operator's own record — no external source is needed to rule out what
    the operator did not observe;
  * a meteorological source cross-reference: no source is configured in
    this product, so an explanatory or absent weather record is an honest
    ``insufficient`` with a sentinel stamp — never an invented negative
    (FR-006 sentinel contract, spec §3.5).
"""
from __future__ import annotations

from uapvf.adapters import BatteryCategoryAdapter, sentinel_stamp
from uapvf.config import utcnow_iso

_NON_EXPLANATORY_RECORDS = {
    "clear", "clear skies", "clear sky", "sunny", "fair", "calm", "dry",
    "no clouds", "cloudless", "no precipitation", "good visibility",
}

_EXPLANATORY_MARKERS = (
    "storm", "rain", "snow", "fog", "cloud", "wind", "haze", "smoke",
    "dust", "lightning", "aurora", "ice", "drizzle", "overcast",
)


class WeatherAdapter(BatteryCategoryAdapter):
    """Operator-record weather test; source cross-reference deferred."""

    def run(self, media_path, metadata, lineage_outputs, config) -> dict:
        now = utcnow_iso()
        record = str((metadata or {}).get("weather") or "").strip()
        source_url = (config or {}).get("source_url") or ""

        def stamp(reason=None):
            base = sentinel_stamp("weather", reason, now)
            return base

        if record:
            lowered = record.lower()
            if lowered in _NON_EXPLANATORY_RECORDS:
                return {
                    "result": "negative",
                    "evidence_citation": (
                        f"operator capture record lists weather as "
                        f"'{record}'; such conditions cannot account for "
                        "the reported observation"),
                    "source_stamp": stamp(),
                }
            if any(marker in lowered for marker in _EXPLANATORY_MARKERS):
                reason = (
                    f"operator record ('{record}') could be explanatory, "
                    "but no meteorological source is configured to confirm")
            else:
                reason = (
                    f"operator record ('{record}') is not classifiable by "
                    "the battery, and no meteorological source is configured")
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": stamp(reason),
            }
        if source_url:
            reason = ("no operator weather record, and no verified "
                      "meteorological integration is shipped for the "
                      "configured source")
        else:
            reason = ("no operator weather record and no configured "
                      "meteorological source")
        return {
            "result": "insufficient",
            "evidence_citation": None,
            "source_stamp": stamp(reason),
        }
