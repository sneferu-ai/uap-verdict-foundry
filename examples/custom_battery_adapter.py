"""Example 4 — template: a custom battery category adapter (FR-006 plugin).

The battery engine (`src/uapvf/battery.py`) loads categories from
`var/battery_config.yaml`. To add your own category:

1. Implement one class with the interface below (this file is that
   template). Place it somewhere importable — e.g. copy it into
   `src/uapvf/adapters/` or put its directory on PYTHONPATH.

2. Register it in `var/battery_config.yaml`:

       categories:
         # ... the four built-in categories ...
         - name: balloons
           enabled: true
           adapter: "my_adapters.balloon_adapter.BalloonAdapter"
           config:
             max_altitude_m: 30000

3. Restart `uapvf serve` and rerun cases (`uapvf case rerun <id>`):
   the new category runs in the battery stage. Configuration errors at
   adapter construction are UNRECOVERABLE by design (the case fails with
   a battery-config error rather than silently skipping the category),
   so validate your module imports cleanly before pointing config at it.

Built-in reference implementations: `src/uapvf/adapters/adsb_adapter.py`,
`satellite_adapter.py`, `archive_adapter.py`, `lineage_adapter.py`.
"""
from __future__ import annotations


class BalloonAdapter:
    """Example adapter: checks whether a weather/research balloon launch
    plausibly explains the capture. Replace the body with real logic."""

    def __init__(self, config: dict):
        # config comes from the category's `config:` mapping in
        # var/battery_config.yaml. ${ENV_VAR} values are substituted by the
        # loader. Construction errors -> BatteryConfigError -> unrecoverable.
        self.max_altitude_m = float(config.get("max_altitude_m", 30000))

    def run(self, media_path, metadata: dict, lineage_outputs, config: dict) -> dict:
        """Return exactly one of three results with provenance.

        media_path: normalized media file (Path or None).
        metadata: case fields (observed_at, latitude, longitude,
                  viewing_direction, ...).
        lineage_outputs: vision-lineage dicts (may be empty when the
                  quality gate skipped analysis).
        config: the category config dict again.

        result must be "positive" | "negative" | "insufficient".
        Unreachable/missing data sources must return "insufficient" with a
        sentinel source_stamp — never raise for external unavailability.
        """
        observed_at = metadata.get("observed_at")
        if not observed_at:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": {
                    "source_id": "balloon_unavailable",
                    "reason": "missing observed_at; cannot test category",
                },
            }
        # (Your data source + matching logic goes here.)
        return {
            "result": "negative",
            "evidence_citation": (
                "no balloon launches recorded within the capture window "
                "(example adapter — replace with real data)"
            ),
            "source_stamp": {
                "source_id": "balloon_template",
                "queried_at": observed_at,
                "max_altitude_m": self.max_altitude_m,
            },
        }
