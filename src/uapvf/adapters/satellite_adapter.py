"""Satellite conjunction adapter (FR-006).

Requires viewing direction: without it the category is `insufficient` with
the spec-mandated reason. TLE staleness: catalog older than 7 days
(relative to the case's capture time) -> `insufficient`. Match window:
±5 minutes, ±5 degrees. Observer altitude defaults to 0 m.

Mock mode uses deterministic pass fixtures from var/mock_fixtures.json
(TLE age is measured against the case's observed_at, keeping fixtures
deterministic regardless of wall-clock date). Live mode requires
skyfield+sgp4 and TLE_CATALOG_URL; missing pieces -> honest `insufficient`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from uapvf.adapters import BatteryCategoryAdapter, canonical_json, content_hash, sentinel_stamp
from uapvf.adapters.adsb_adapter import load_mock_fixtures
from uapvf.config import get_settings, parse_iso, utcnow_iso


class SatelliteAdapter(BatteryCategoryAdapter):
    name = "satellites"

    def run(self, media_path, metadata: dict, lineage_outputs, config: dict) -> dict:
        settings = get_settings()
        now = utcnow_iso()
        window_min = float(self.params.get("match_window_minutes", 5))
        angle_deg = float(self.params.get("match_angle_deg", 5.0))
        max_age_days = float(self.params.get("max_tle_age_days", 7))
        min_el = float(self.params.get("min_elevation_deg", 10.0))

        vd = metadata.get("viewing_direction")
        query_params = canonical_json(
            {
                "latitude": metadata.get("latitude"),
                "longitude": metadata.get("longitude"),
                "observed_at": metadata.get("observed_at"),
                "viewing_direction": list(vd) if vd else None,
            }
        )
        if not vd:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite",
                    "viewing direction required for satellite conjunction check",
                    now,
                ),
            }
        az, el = vd
        if settings.mock_mode:
            return self._run_mock(metadata, az, el, window_min, angle_deg,
                                  max_age_days, min_el, query_params, now, settings)
        return self._run_live(metadata, az, el, window_min, angle_deg,
                              max_age_days, min_el, query_params, now, settings)

    def _run_mock(self, metadata, az, el, window_min, angle_deg, max_age_days,
                  min_el, query_params, now, settings) -> dict:
        fixtures = load_mock_fixtures(settings).get("satellites") or {}
        if not fixtures:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "no satellite mock fixtures configured", now
                ),
            }
        observed_at = metadata.get("observed_at")
        try:
            case_dt = parse_iso(observed_at)
            tle_dt = parse_iso(fixtures["tle_epoch"])
        except Exception:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "unparseable observed_at or TLE epoch", now
                ),
            }
        tle_age_days = (case_dt - tle_dt).total_seconds() / 86400.0
        if tle_age_days > max_age_days:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite",
                    f"TLE catalog stale ({tle_age_days:.1f} days old > "
                    f"{max_age_days:.0f}; manual refresh required)",
                    now,
                ),
            }
        coverage = fixtures.get("coverage") or {}
        lat = metadata.get("latitude")
        lon = metadata.get("longitude")
        if lat is None or lon is None:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "latitude and longitude required", now
                ),
            }
        in_coverage = (
            coverage
            and coverage.get("lat_min", -90) <= lat <= coverage.get("lat_max", 90)
            and coverage.get("lon_min", -180) <= lon <= coverage.get("lon_max", 180)
        )
        if not in_coverage:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "no satellite coverage for supplied location", now
                ),
            }
        for p in fixtures.get("passes") or []:
            try:
                pass_dt = parse_iso(p["culmination_at"])
            except Exception:
                continue
            if p.get("elevation_deg", 0.0) < min_el:
                continue
            dt_min = abs((case_dt - pass_dt).total_seconds()) / 60.0
            daz = abs(((az - p["azimuth_deg"]) + 180) % 360 - 180)
            del_ = abs(el - p["elevation_deg"])
            if dt_min <= window_min and daz <= angle_deg and del_ <= angle_deg:
                return {
                    "result": "positive",
                    "evidence_citation": (
                        f"satellite {p['satellite']} pass matched within "
                        f"{dt_min:.1f} min and {max(daz, del_):.1f} deg of "
                        "viewing direction (mock TLE fixture)"
                    ),
                    "source_stamp": {
                        "source_id": "satellite_mock_fixture",
                        "query_params": query_params,
                        "utc_timestamp": now,
                        "content_hash": content_hash(p),
                        "source_version": "mock-v1",
                    },
                }
        return {
            "result": "negative",
            "evidence_citation": (
                "TLE fixture coverage confirmed; no satellite pass matched "
                f"the ±{window_min:.0f} min / ±{angle_deg:.0f} deg window at "
                "the supplied viewing direction"
            ),
            "source_stamp": {
                "source_id": "satellite_mock_fixture",
                "query_params": query_params,
                "utc_timestamp": now,
                "content_hash": content_hash(
                    {"passes_checked": len(fixtures.get("passes") or [])}
                ),
                "source_version": "mock-v1",
            },
        }

    def _run_live(self, metadata, az, el, window_min, angle_deg, max_age_days,
                  min_el, query_params, now, settings) -> dict:
        if not settings.TLE_CATALOG_URL:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "source not configured", now
                ),
            }
        try:
            from skyfield.api import EarthSatellite, load, wgs84
            import sgp4  # noqa: F401
        except Exception:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "skyfield/sgp4 not installed", now
                ),
            }
        # Live case analysis never refreshes on demand. It consumes only the
        # manually refreshed, persisted catalog (A-012).
        try:
            from uapvf import db as dbmod
            from uapvf.tle import latest_catalog
            conn = dbmod.connect(settings.db_path)
            try:
                catalog = latest_catalog(conn, settings)
            finally:
                conn.close()
        except Exception:
            catalog = None
        if catalog is None or not catalog["path"] or not Path(catalog["path"]).exists():
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "satellite", "TLE catalog unavailable (manual refresh: uapvf tle refresh)", now
                ),
            }
        try:
            age_days = (parse_iso(now) - parse_iso(catalog["fetched_at"])).total_seconds() / 86400.0
        except Exception:
            age_days = max_age_days + 1
        if age_days > max_age_days:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "satellite", f"TLE catalog stale ({age_days:.1f} days)", now)}
        lat = metadata.get("latitude")
        lon = metadata.get("longitude")
        observed_at = metadata.get("observed_at")
        if lat is None or lon is None:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "satellite", "latitude and longitude required", now)}
        try:
            observed_dt = parse_iso(observed_at)
        except Exception:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "satellite", "unparseable observed_at", now)}

        try:
            import json
            import math
            from datetime import timedelta
            from uapvf.tle import load_catalog

            fmt, records = load_catalog(catalog["path"])
            ts = load.timescale(builtin=True)
            observer = wgs84.latlon(
                float(lat), float(lon),
                elevation_m=float(metadata.get("observer_altitude_m") or 0.0),
            )
            # Thirty-second sampling bounds the timing error inside the
            # configured +/- five-minute window while keeping a full active
            # catalog practical on commodity hardware.
            step_seconds = 30
            offsets = list(range(
                -int(window_min * 60), int(window_min * 60) + 1, step_seconds
            ))
            datetimes = [observed_dt + timedelta(seconds=value) for value in offsets]
            times = ts.from_datetimes(datetimes)
            best = None
            fresh_records = 0
            parse_failures = 0

            def angular_distance_deg(a1, e1, a2, e2):
                a1, e1, a2, e2 = map(math.radians, (a1, e1, a2, e2))
                cosine = (
                    math.sin(e1) * math.sin(e2)
                    + math.cos(e1) * math.cos(e2) * math.cos(a1 - a2)
                )
                return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

            for record in records:
                try:
                    if fmt == "omm_json":
                        satellite = EarthSatellite.from_omm(ts, record)
                        name = str(record.get("OBJECT_NAME") or
                                   record.get("NORAD_CAT_ID") or "unknown")
                        sat_number = str(record.get("NORAD_CAT_ID") or "")
                    else:
                        satellite = EarthSatellite(
                            record["line1"], record["line2"],
                            record.get("name") or record["satellite_number"], ts,
                        )
                        name = str(record.get("name") or record["satellite_number"])
                        sat_number = str(record["satellite_number"])
                    epoch_dt = satellite.epoch.utc_datetime()
                    epoch_age_days = abs(
                        (observed_dt - epoch_dt).total_seconds()
                    ) / 86400.0
                    if epoch_age_days > max_age_days:
                        continue
                    fresh_records += 1
                    topocentric = (satellite - observer).at(times)
                    altitudes, azimuths, _ = topocentric.altaz()
                    for pos, (sat_az, sat_el) in enumerate(zip(
                        azimuths.degrees, altitudes.degrees
                    )):
                        sat_el = float(sat_el)
                        if sat_el < min_el:
                            continue
                        distance = angular_distance_deg(
                            float(az), float(el), float(sat_az), sat_el
                        )
                        if best is None or distance < best["angular_distance_deg"]:
                            best = {
                                "satellite": name,
                                "satellite_number": sat_number,
                                "angular_distance_deg": distance,
                                "time_offset_seconds": offsets[pos],
                                "azimuth_deg": float(sat_az),
                                "elevation_deg": sat_el,
                                "tle_epoch": epoch_dt.isoformat(),
                            }
                except Exception:
                    parse_failures += 1

            detail = json.loads(catalog["detail_json"] or "{}")
            source_payload = {
                "catalog_substantive_hash": catalog["substantive_hash"],
                "catalog_content_sha256": detail.get("content_sha256"),
                "catalog_format": fmt,
                "records_total": len(records),
                "records_fresh_for_observation": fresh_records,
                "records_rejected": parse_failures,
                "best_match": best,
            }
        except Exception as exc:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "satellite",
                        f"catalog propagation failed ({type(exc).__name__})",
                        now)}

        if fresh_records == 0:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "satellite",
                        "catalog has no orbital elements fresh enough for observed_at",
                        now)}
        source_stamp = {
            "source_id": f"satellite:{settings.TLE_CATALOG_URL}",
            "query_params": query_params,
            "utc_timestamp": now,
            "content_hash": content_hash(source_payload),
            "source_version": str(catalog["substantive_hash"]),
        }
        if best is not None and best["angular_distance_deg"] <= angle_deg:
            return {
                "result": "positive",
                "evidence_citation": (
                    f"satellite {best['satellite']} ({best['satellite_number']}) "
                    f"matched within {abs(best['time_offset_seconds']) / 60:.1f} min "
                    f"and {best['angular_distance_deg']:.2f} deg of the supplied "
                    "viewing direction using the persisted orbital catalog"
                ),
                "source_stamp": source_stamp,
            }
        return {
            "result": "negative",
            "evidence_citation": (
                f"{fresh_records} fresh orbital records were propagated across "
                f"the +/-{window_min:.0f} min window; no object matched within "
                f"{angle_deg:.0f} deg of the supplied viewing direction"
            ),
            "source_stamp": source_stamp,
        }
