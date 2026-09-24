"""ADS-B aircraft cross-reference adapter (FR-006).

Mock mode: deterministic fixtures from var/mock_fixtures.json.
- UAPV_MOCK_ADSB_POSITIVE=1 forces the pre-registered positive fixture
  regardless of input (FR-018).
- Otherwise: a fixture track within ±window/±radius of the case's
  lat/lon/time -> positive; inside declared coverage with no match ->
  negative (data available, confirmed coverage, no match); outside
  coverage -> insufficient.
Live mode: queries ADSB_SOURCE_URL; unset/unreachable -> insufficient.
Match window defaults: ±5 minutes, ±2 km. Timeout 10 s.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from uapvf.adapters import BatteryCategoryAdapter, canonical_json, content_hash
from uapvf.adapters.geo import haversine_km
from uapvf.config import get_settings, parse_iso, utcnow_iso


def load_mock_fixtures(settings=None) -> dict:
    settings = settings or get_settings()
    path = settings.var_dir / "mock_fixtures.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


class AdsbAdapter(BatteryCategoryAdapter):
    name = "aircraft"

    def run(self, media_path, metadata: dict, lineage_outputs, config: dict) -> dict:
        settings = get_settings()
        window_min = float(self.params.get("match_window_minutes", 5))
        radius_km = float(self.params.get("match_radius_km", 2.0))
        lat = metadata.get("latitude")
        lon = metadata.get("longitude")
        observed_at = metadata.get("observed_at")
        query_params = canonical_json(
            {"latitude": lat, "longitude": lon, "observed_at": observed_at}
        )
        now = utcnow_iso()

        if settings.mock_mode:
            return self._run_mock(lat, lon, observed_at, window_min, radius_km,
                                  query_params, now, settings)
        return self._run_live(lat, lon, observed_at, window_min, radius_km,
                              query_params, now, settings)

    def _run_mock(self, lat, lon, observed_at, window_min, radius_km,
                  query_params, now, settings) -> dict:
        if lat is None or lon is None:
            from uapvf.adapters import sentinel_stamp

            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "adsb", "latitude and longitude required", now
                ),
            }
        if os.environ.get("UAPV_MOCK_ADSB_POSITIVE") == "1":
            fixture = {
                "callsign": "MOCK123",
                "latitude": lat,
                "longitude": lon,
                "observed_at": observed_at,
                "altitude_m": 10668,
            }
            return {
                "result": "positive",
                "evidence_citation": (
                    "flight track MOCK123 matched within ±"
                    f"{window_min:.0f} min / ±{radius_km:.0f} km of capture "
                    "time and place (mock ADS-B fixture)"
                ),
                "source_stamp": {
                    "source_id": "adsb_mock_fixture",
                    "query_params": query_params,
                    "utc_timestamp": now,
                    "content_hash": content_hash(fixture),
                    "source_version": "mock-v1",
                },
            }
        fixtures = load_mock_fixtures(settings).get("adsb") or {}
        coverage = fixtures.get("coverage") or {}
        tracks = fixtures.get("tracks") or []
        in_coverage = (
            coverage
            and coverage.get("lat_min", -90) <= lat <= coverage.get("lat_max", 90)
            and coverage.get("lon_min", -180) <= lon <= coverage.get("lon_max", 180)
        )
        if not fixtures or not in_coverage:
            reason = (
                "mock ADS-B fixture: no tracks in match window"
                if not fixtures
                else "no ADS-B coverage for supplied location (mock fixture)"
            )
            from uapvf.adapters import sentinel_stamp

            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp("adsb", reason, now),
            }
        try:
            case_dt = parse_iso(observed_at)
        except Exception:
            from uapvf.adapters import sentinel_stamp

            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp("adsb", "unparseable observed_at", now),
            }
        for track in tracks:
            try:
                track_dt = parse_iso(track["observed_at"])
            except Exception:
                continue
            dt_min = abs((case_dt - track_dt).total_seconds()) / 60.0
            dist = haversine_km(lat, lon, track["latitude"], track["longitude"])
            if dt_min <= window_min and dist <= radius_km:
                return {
                    "result": "positive",
                    "evidence_citation": (
                        f"flight track {track['callsign']} matched within "
                        f"{dt_min:.1f} min and {dist:.2f} km of capture "
                        "time/place (mock ADS-B fixture)"
                    ),
                    "source_stamp": {
                        "source_id": "adsb_mock_fixture",
                        "query_params": query_params,
                        "utc_timestamp": now,
                        "content_hash": content_hash(track),
                        "source_version": "mock-v1",
                    },
                }
        return {
            "result": "negative",
            "evidence_citation": (
                "ADS-B fixture coverage confirmed for supplied location and "
                "time; no flight track matched the ±"
                f"{window_min:.0f} min / ±{radius_km:.0f} km window"
            ),
            "source_stamp": {
                "source_id": "adsb_mock_fixture",
                "query_params": query_params,
                "utc_timestamp": now,
                "content_hash": content_hash({"tracks_checked": len(tracks)}),
                "source_version": "mock-v1",
            },
        }

    def _run_live(self, lat, lon, observed_at, window_min, radius_km,
                  query_params, now, settings) -> dict:
        from uapvf.adapters import sentinel_stamp

        url = settings.ADSB_SOURCE_URL
        if not url:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp("adsb", "source not configured", now),
            }
        if lat is None or lon is None:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "adsb", "latitude and longitude required", now
                ),
            }
        try:
            observed_dt = parse_iso(observed_at)
        except Exception:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp("adsb", "unparseable observed_at", now),
            }
        host = (urlparse(url).hostname or "").lower()
        if host.endswith("adsbexchange.com"):
            return self._run_adsbexchange(
                url, lat, lon, observed_dt, window_min, radius_km,
                query_params, now, settings,
            )
        if host.endswith("opensky-network.org"):
            return self._run_opensky(
                url, lat, lon, observed_dt, window_min, radius_km,
                query_params, now, settings,
            )
        return self._run_generic(
            url, lat, lon, observed_at, window_min, radius_km,
            query_params, now,
        )

    @staticmethod
    def _stamp(url, query_params, now, payload, version):
        return {
            "source_id": f"adsb:{url}",
            "query_params": query_params,
            "utc_timestamp": now,
            "content_hash": content_hash(payload),
            "source_version": str(version),
        }

    @staticmethod
    def _position_time(epoch_seconds):
        return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc)

    def _run_adsbexchange(self, url, lat, lon, observed_dt, window_min,
                          radius_km, query_params, now, settings):
        """Query the official ADS-B Exchange radius endpoint.

        That endpoint is live-only.  A capture outside the returned feed's
        time window is therefore insufficient, never a false negative.
        """
        from uapvf.adapters import sentinel_stamp

        api_key = settings.ADSB_SOURCE_API_KEY
        if not api_key:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", "ADS-B Exchange API key not configured", now)}
        try:
            import httpx
            from uapvf.egress import validate_url

            base = url.split("/lat/", 1)[0].rstrip("/")
            radius_nm = max(1.0, float(radius_km) / 1.852)
            endpoint = (
                f"{base}/lat/{float(lat):.6f}/lon/{float(lon):.6f}/"
                f"dist/{radius_nm:.3f}"
            )
            validate_url(endpoint, settings)
            response = httpx.get(
                endpoint,
                headers={"x-api-key": api_key, "Accept-Encoding": "gzip"},
                timeout=float(self.params.get("timeout_s", 10)),
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            server_seconds = float(payload["now"]) / 1000.0
        except Exception as exc:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", f"ADS-B Exchange unavailable ({type(exc).__name__})",
                        now)}
        server_dt = self._position_time(server_seconds)
        if abs((server_dt - observed_dt).total_seconds()) > window_min * 60:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", "ADS-B Exchange radius feed is live-only for observed_at",
                        now)}
        matches = []
        for aircraft in payload.get("ac") or []:
            try:
                position_dt = self._position_time(
                    server_seconds - float(aircraft.get("seen_pos") or 0.0)
                )
                dt_min = abs((position_dt - observed_dt).total_seconds()) / 60.0
                distance = haversine_km(
                    float(lat), float(lon),
                    float(aircraft["lat"]), float(aircraft["lon"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if dt_min <= window_min and distance <= radius_km:
                matches.append((distance, dt_min, aircraft))
        stamp = self._stamp(
            url, query_params, now, payload,
            f"adsbexchange-v2@{int(server_seconds)}",
        )
        if matches:
            distance, dt_min, best = min(matches, key=lambda item: item[0])
            identifier = (best.get("flight") or best.get("hex") or "unknown").strip()
            return {
                "result": "positive",
                "evidence_citation": (
                    f"ADS-B Exchange track {identifier} matched within "
                    f"{dt_min:.1f} min and {distance:.2f} km of capture time/place"
                ),
                "source_stamp": stamp,
            }
        return {
            "result": "negative",
            "evidence_citation": (
                "ADS-B Exchange live radius coverage was confirmed; no aircraft "
                f"matched the +/-{window_min:.0f} min / +/-{radius_km:.0f} km window"
            ),
            "source_stamp": stamp,
        }

    def _opensky_headers(self, settings):
        """Obtain an OAuth token when configured; anonymous use stays explicit."""
        client_id = settings.OPENSKY_CLIENT_ID
        client_secret = settings.OPENSKY_CLIENT_SECRET
        if not client_id or not client_secret:
            return {}
        import httpx
        from uapvf.egress import validate_url

        token_url = (
            "https://auth.opensky-network.org/auth/realms/opensky-network/"
            "protocol/openid-connect/token"
        )
        validate_url(token_url, settings)
        response = httpx.post(
            token_url,
            data={"grant_type": "client_credentials", "client_id": client_id,
                  "client_secret": client_secret},
            timeout=10,
            follow_redirects=False,
        )
        response.raise_for_status()
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def _run_opensky(self, url, lat, lon, observed_dt, window_min,
                     radius_km, query_params, now, settings):
        from uapvf.adapters import sentinel_stamp
        try:
            import httpx
            from uapvf.egress import validate_url

            validate_url(url, settings)
            lat_delta = radius_km / 110.574
            lon_scale = max(0.01, abs(__import__("math").cos(
                __import__("math").radians(float(lat))
            )))
            lon_delta = radius_km / (111.320 * lon_scale)
            response = httpx.get(
                url,
                params={
                    "time": int(observed_dt.timestamp()),
                    "lamin": float(lat) - lat_delta,
                    "lomin": float(lon) - lon_delta,
                    "lamax": float(lat) + lat_delta,
                    "lomax": float(lon) + lon_delta,
                },
                headers=self._opensky_headers(settings),
                timeout=float(self.params.get("timeout_s", 10)),
                follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            response_dt = self._position_time(payload["time"])
        except Exception as exc:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", f"OpenSky unavailable ({type(exc).__name__})", now)}
        # Anonymous OpenSky ignores historical `time`; authenticated REST is
        # limited to one hour.  Comparing the returned epoch makes both cases
        # fail closed when the requested observation was not actually served.
        if abs((response_dt - observed_dt).total_seconds()) > window_min * 60:
            return {"result": "insufficient", "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", "OpenSky did not return the requested observed_at window",
                        now)}
        matches = []
        for state in payload.get("states") or []:
            try:
                position_dt = self._position_time(state[3])
                distance = haversine_km(
                    float(lat), float(lon), float(state[6]), float(state[5])
                )
                dt_min = abs((position_dt - observed_dt).total_seconds()) / 60.0
            except (IndexError, TypeError, ValueError):
                continue
            if dt_min <= window_min and distance <= radius_km:
                matches.append((distance, dt_min, state))
        stamp = self._stamp(url, query_params, now, payload,
                            f"opensky@{int(payload['time'])}")
        if matches:
            distance, dt_min, best = min(matches, key=lambda item: item[0])
            identifier = (best[1] or best[0] or "unknown").strip()
            return {
                "result": "positive",
                "evidence_citation": (
                    f"OpenSky state vector {identifier} matched within "
                    f"{dt_min:.1f} min and {distance:.2f} km of capture time/place"
                ),
                "source_stamp": stamp,
            }
        return {
            "result": "negative",
            "evidence_citation": (
                "OpenSky returned the requested bounded state-vector window; "
                f"no aircraft matched within {radius_km:.0f} km"
            ),
            "source_stamp": stamp,
        }

    def _run_generic(self, url, lat, lon, observed_at, window_min, radius_km,
                     query_params, now):
        """Backward-compatible operator gateway contract.

        Gateways must now assert ``coverage_confirmed: true`` before an empty
        result can become a negative finding.
        """
        from uapvf.adapters import sentinel_stamp
        try:
            import httpx

            resp = httpx.get(
                url,
                params={
                    "lat": lat,
                    "lon": lon,
                    "at": observed_at,
                    "window_min": window_min,
                    "radius_km": radius_km,
                },
                timeout=float(self.params.get("timeout_s", 10)),
            )
            if resp.status_code >= 400:
                return {
                    "result": "insufficient",
                    "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", f"source returned HTTP {resp.status_code}", now
                    ),
                }
            payload = resp.json()
        except Exception as exc:
            return {
                "result": "insufficient",
                "evidence_citation": None,
                "source_stamp": sentinel_stamp(
                    "adsb", f"source unreachable ({exc.__class__.__name__})", now
                ),
            }
        tracks = payload.get("tracks") or []
        if not tracks:
            if payload.get("coverage_confirmed") is not True:
                return {
                    "result": "insufficient",
                    "evidence_citation": None,
                    "source_stamp": sentinel_stamp(
                        "adsb", "source did not confirm coverage", now
                    ),
                }
            return {
                "result": "negative",
                "evidence_citation": "ADS-B source queried with confirmed coverage; no match",
                "source_stamp": self._stamp(
                    url, query_params, now, payload,
                    payload.get("version", "adsb-v1"),
                ),
            }
        best = tracks[0]
        return {
            "result": "positive",
            "evidence_citation": f"flight track {best.get('callsign', '?')} matched",
            "source_stamp": self._stamp(
                url, query_params, now, payload,
                payload.get("version", "adsb-v1"),
            ),
        }
