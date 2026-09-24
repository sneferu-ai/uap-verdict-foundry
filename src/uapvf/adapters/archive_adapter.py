"""Astronomical archive cross-match adapter (FR-006) — refutation-only.

Cross-matches the viewing direction against a local astronomical archive
mirror (a SQLite catalog at ARCHIVE_MIRROR_PATH). RA/Dec is derived from
observer lat/lon (altitude 0 m), capture time, and viewing direction via
the topocentric-to-equatorial conversion in uapvf.adapters.geo.

Match window: ±0.5 degrees, magnitude < 6. Timeout: 5 s. Without viewing
direction the category is `insufficient` (spec-mandated reason). This
adapter only refutes: a match means "a known object explains the sighting",
never a discovery claim.
"""
from __future__ import annotations

import sqlite3

from uapvf.adapters import BatteryCategoryAdapter, canonical_json, content_hash, sentinel_stamp
from uapvf.adapters.geo import altaz_to_radec, angular_separation_deg
from uapvf.config import get_settings, parse_iso, utcnow_iso


class ArchiveAdapter(BatteryCategoryAdapter):
    name = "astronomical"

    def run(self, media_path, metadata: dict, lineage_outputs, config: dict) -> dict:
        settings = get_settings()
        now = utcnow_iso()
        match_deg = float(self.params.get("match_angle_deg", 0.5))
        max_mag = float(self.params.get("max_magnitude", 6.0))

        vd = metadata.get("viewing_direction")
        query_params = canonical_json(
            {
                "latitude": metadata.get("latitude"),
                "longitude": metadata.get("longitude"),
                "observed_at": metadata.get("observed_at"),
                "viewing_direction": list(vd) if vd else None,
                "observer_altitude_m": 0,
            }
        )
        if not vd:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "archive",
                    "viewing direction required for astronomical cross-match",
                    now,
                ),
            }
        mirror = self.params.get("mirror_path") or settings.ARCHIVE_MIRROR_PATH
        if not mirror:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp("archive", "source not configured", now),
            }
        try:
            observed_dt = parse_iso(metadata.get("observed_at"))
        except Exception:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "archive", "unparseable observed_at", now
                ),
            }
        latitude = metadata.get("latitude")
        longitude = metadata.get("longitude")
        if latitude is None or longitude is None:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "archive", "latitude and longitude required", now
                ),
            }
        az, el = vd
        ra, dec = altaz_to_radec(
            observed_dt, latitude, longitude, az, el
        )
        total_objects = 0
        candidate_count = 0
        best = None
        best_sep = None
        try:
            conn = sqlite3.connect(f"file:{mirror}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            total_objects = int(conn.execute(
                "SELECT COUNT(*) FROM catalog_objects"
            ).fetchone()[0])
            cursor = conn.execute(
                "SELECT object_id, name, ra_deg, dec_deg, magnitude "
                "FROM catalog_objects WHERE magnitude < ?",
                (max_mag,),
            )
            # Iterate the cursor instead of fetchall(): a production mirror
            # may contain millions of rows and must not be materialized in
            # memory.  Only the current best match is retained.
            for row in cursor:
                candidate_count += 1
                sep = angular_separation_deg(
                    ra, dec, row["ra_deg"], row["dec_deg"]
                )
                if best_sep is None or sep < best_sep:
                    best, best_sep = row, sep
        except Exception as exc:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "archive", f"source unreachable ({exc.__class__.__name__})", now
                ),
            }
        finally:
            if "conn" in locals():
                conn.close()
        stamp_base = {
            "source_id": f"archive:{mirror}",
            "query_params": query_params,
            "utc_timestamp": now,
            "source_version": "archive-mirror-v1",
        }
        if best is not None and best_sep is not None and best_sep <= match_deg:
            matched = {
                "object_id": best["object_id"],
                "name": best["name"],
                "ra_deg": round(best["ra_deg"], 6),
                "dec_deg": round(best["dec_deg"], 6),
                "magnitude": best["magnitude"],
                "separation_deg": round(best_sep, 4),
            }
            return {
                "result": "positive",
                "evidence_citation": (
                    f"catalog object {best['object_id']} ({best['name']}) at "
                    f"{best_sep:.3f} deg separation, magnitude "
                    f"{best['magnitude']:.1f} — consistent with a known "
                    "astronomical object"
                ),
                "source_stamp": {
                    **stamp_base,
                    "content_hash": content_hash(matched),
                },
            }
        if total_objects == 0:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp("archive", "catalog empty", now),
            }
        return {
            "result": "negative",
            "evidence_citation": (
                f"archive mirror queried ({total_objects} objects, "
                f"{candidate_count} brighter than magnitude {max_mag:.0f}); "
                f"no catalog object within {match_deg} deg of the derived "
                f"RA/Dec ({ra:.3f}, {dec:.3f})"
            ),
            "source_stamp": {
                **stamp_base,
                "content_hash": content_hash(
                    {"objects": total_objects, "candidates": candidate_count,
                     "ra_deg": round(ra, 6), "dec_deg": round(dec, 6)}
                ),
            },
        }
