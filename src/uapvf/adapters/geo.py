"""Deterministic astronomy math (pure stdlib).

Used by the archive adapter to derive RA/Dec from observer lat/lon, time,
and viewing direction (alt/az -> equatorial, observer altitude 0 m per
FR-006). Implemented locally so mock mode and offline tests need no
skyfield; the live satellite path prefers skyfield/sgp4 when installed.

Conventions: azimuth measured from north through east; elevation above the
horizon. GMST from the standard IAU formula (degrees).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def julian_date(dt: datetime) -> float:
    dt = _to_utc(dt)
    y = dt.year
    m = dt.month
    d = (
        dt.day
        + dt.hour / 24.0
        + dt.minute / 1440.0
        + dt.second / 86400.0
        + dt.microsecond / 86_400_000_000.0
    )
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5


def gmst_deg(dt: datetime) -> float:
    """Greenwich mean sidereal time in degrees."""
    d = julian_date(_to_utc(dt)) - 2451545.0
    gmst = 280.46061837 + 360.98564736629 * d
    return gmst % 360.0


def altaz_to_radec(
    dt: datetime,
    lat_deg: float,
    lon_deg: float,
    az_deg: float,
    el_deg: float,
) -> tuple:
    """Convert alt/az (el = altitude angle) to (ra_deg, dec_deg)."""
    lat = math.radians(lat_deg)
    el = math.radians(el_deg)
    az = math.radians(az_deg)

    sin_dec = math.sin(el) * math.sin(lat) + math.cos(el) * math.cos(lat) * math.cos(az)
    sin_dec = max(-1.0, min(1.0, sin_dec))
    dec = math.asin(sin_dec)

    y = -math.sin(az) * math.cos(el)
    x = math.sin(el) * math.cos(lat) - math.cos(el) * math.sin(lat) * math.cos(az)
    ha = math.atan2(y, x)  # radians, west-positive by this construction

    lst = (gmst_deg(dt) + lon_deg) % 360.0
    ra = (lst - math.degrees(ha)) % 360.0
    return ra, math.degrees(dec)


def angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle separation between two equatorial coordinates."""
    r1, d1 = math.radians(ra1), math.radians(dec1)
    r2, d2 = math.radians(ra2), math.radians(dec2)
    cos_sep = (
        math.sin(d1) * math.sin(d2)
        + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    )
    cos_sep = max(-1.0, min(1.0, cos_sep))
    return math.degrees(math.acos(cos_sep))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))
