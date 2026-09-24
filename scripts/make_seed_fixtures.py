#!/usr/bin/env python3
"""Generate the deterministic benchmark seed fixtures + test fixtures.

Creates:
  var/benchmark/seed/*.jpg              — 10 seed images with known outcomes
  var/benchmark/seed/catalog_fixture.sqlite — astronomical archive mirror
  var/benchmark/seed/seed_manifest.json — labels manifest (FR-014)
  tests/fixtures/unexplained.jpg        — held-out insufficient_data fixture
  tests/fixtures/aircraft.jpg           — held-out mundane_identified fixture
  tests/fixtures/fields.json            — intake fields for unexplained.jpg
  tests/fixtures/seed_manifest.json     — copy of the manifest

The catalog objects' RA/Dec are computed with the SAME geo module the
archive adapter uses, so positive/negative outcomes are exact by
construction. Re-run any time to regenerate byte-stable fixtures.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from uapvf.adapters.geo import altaz_to_radec, angular_separation_deg  # noqa: E402
from uapvf.config import parse_iso  # noqa: E402

SEED_DIR = ROOT / "var" / "benchmark" / "seed"
TEST_DIR = ROOT / "tests" / "fixtures"


def make_image(path: Path, size=(1920, 1080), color=(40, 60, 90), exif=True,
               quality=85) -> None:
    im = Image.new("RGB", size, color)
    # A little structure so files are not degenerate single-color blocks.
    px = im.load()
    for i in range(0, size[0], max(1, size[0] // 16)):
        for j in range(size[1]):
            px[i, j] = ((i * 7) % 256, (j * 3) % 256, 128)
    kwargs = {"quality": quality}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "UAPVF-FIXTURE"  # Make
        ex[0x0110] = "seed-generator"  # Model
        kwargs["exif"] = ex.tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, format="JPEG", **kwargs)


def fields_for(lat, lon, observed_at, direction=None, shape="light",
               location="Fixture site"):
    intake = {
        "observed_at": observed_at,
        "latitude": lat,
        "longitude": lon,
        "shape": shape,
        "count": 1,
        "weather": "clear",
        "behavior_notes": "Benchmark seed fixture.",
        "location_text": location,
    }
    if direction:
        intake["viewing_direction"] = direction
    return intake


def main() -> int:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)

    # --- catalog fixture: objects exactly where adapters will look ---------
    astro1 = {"lat": 34.3, "lon": -117.9, "at": "2026-01-15T20:45:00+00:00",
              "az": 45.0, "el": 60.0}
    nmm1 = {"lat": 34.5, "lon": -118.0, "at": "2026-01-15T21:15:00+00:00",
            "az": 90.0, "el": 45.0}
    ra1, dec1 = altaz_to_radec(parse_iso(astro1["at"]), astro1["lat"],
                               astro1["lon"], astro1["az"], astro1["el"])
    ra2, dec2 = altaz_to_radec(parse_iso(nmm1["at"]), nmm1["lat"],
                               nmm1["lon"], nmm1["az"], nmm1["el"])

    # Guard: obj1 must not accidentally sit near any other seed's line of
    # sight (else a "negative" label would be wrong).
    others = [
        ("nmm2", 34.6, -117.8, "2026-01-15T21:20:00+00:00", 90.0, 45.0),
        ("nmm3", 34.4, -118.3, "2026-01-15T21:25:00+00:00", 270.0, 30.0),
        ("insuf1", 34.5, -118.0, "2026-01-15T21:30:00+00:00", 90.0, 45.0),
        ("aircraft1", 34.05, -118.24, "2026-01-15T20:30:00+00:00", 90.0, 45.0),
        ("aircraft2", 34.06, -118.25, "2026-01-15T20:31:00+00:00", 270.0, 45.0),
        ("satellite1", 34.2, -118.1, "2026-01-15T20:31:00+00:00", 180.0, 45.0),
    ]
    for name, lat, lon, at, az, el in others:
        ra, dec = altaz_to_radec(parse_iso(at), lat, lon, az, el)
        sep = angular_separation_deg(ra1, dec1, ra, dec)
        assert sep > 1.0, f"catalog obj1 too close to {name} sightline: {sep:.3f} deg"

    catalog = SEED_DIR / "catalog_fixture.sqlite"
    if catalog.exists():
        catalog.unlink()
    conn = sqlite3.connect(catalog)
    conn.execute(
        "CREATE TABLE catalog_objects (object_id TEXT PRIMARY KEY, name TEXT,"
        " ra_deg REAL NOT NULL, dec_deg REAL NOT NULL, magnitude REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO catalog_objects VALUES (?, ?, ?, ?, ?)",
        ("HD-MOCK-1001", "Mock bright star", ra1, dec1, 2.0),
    )
    conn.execute(
        "INSERT INTO catalog_objects VALUES (?, ?, ?, ?, ?)",
        ("HD-MOCK-2002", "Mock faint star", ra2, dec2, 7.0),
    )
    conn.commit()
    conn.close()

    # --- seed manifest ------------------------------------------------------
    def neg_labels():
        return {"aircraft": "negative", "satellites": "negative",
                "lens_artifacts": "negative", "astronomical": "negative",
                "expected_insufficient": []}

    manifest = []

    def seed(fname, lat, lon, at, direction, labels, label_source, overall,
             size=(1920, 1080), exif=True, color=(40, 60, 90)):
        make_image(SEED_DIR / fname, size=size, exif=exif, color=color)
        labels = dict(labels)
        labels["overall"] = overall
        manifest.append({
            "case_file": fname,
            "intake": fields_for(lat, lon, at, direction),
            "labels": labels,
            "label_source": label_source,
        })

    a = neg_labels(); a["aircraft"] = "positive"
    seed("aircraft1.jpg", 34.05, -118.24, "2026-01-15T20:30:00+00:00", "90,45",
         a, "adsb_confirmed", "mundane_identified", color=(90, 60, 40))
    a = neg_labels(); a["aircraft"] = "positive"
    seed("aircraft2.jpg", 34.06, -118.25, "2026-01-15T20:31:00+00:00", "270,45",
         a, "adsb_confirmed", "mundane_identified", color=(100, 70, 30))
    s = neg_labels(); s["satellites"] = "positive"
    seed("satellite1.jpg", 34.2, -118.1, "2026-01-15T20:31:00+00:00", "180,45",
         s, "satellite_confirmed", "mundane_identified", color=(30, 80, 70))
    t = neg_labels(); t["astronomical"] = "positive"
    seed("astro1.jpg", 34.3, -117.9, "2026-01-15T20:45:00+00:00", "45,60",
         t, "astronomical_confirmed", "mundane_identified", color=(60, 40, 90))

    seed("nmm1.jpg", 34.5, -118.0, "2026-01-15T21:15:00+00:00", "90,45",
         neg_labels(), "synthetic", "no_mundane_match", color=(20, 20, 35))
    seed("nmm2.jpg", 34.6, -117.8, "2026-01-15T21:20:00+00:00", "90,45",
         neg_labels(), "synthetic", "no_mundane_match", color=(25, 22, 40))
    seed("nmm3.jpg", 34.4, -118.3, "2026-01-15T21:25:00+00:00", "270,30",
         neg_labels(), "synthetic", "no_mundane_match", color=(18, 26, 33))

    i1 = neg_labels(); i1["expected_insufficient"] = ["lens_artifacts"]
    seed("insuf1.jpg", 34.5, -118.0, "2026-01-15T21:30:00+00:00", "90,45",
         i1, "synthetic", "insufficient_data", size=(320, 240), exif=False,
         color=(70, 70, 70))
    i2 = neg_labels(); i2["expected_insufficient"] = ["satellites", "astronomical"]
    seed("insuf2.jpg", 34.5, -118.0, "2026-01-15T21:35:00+00:00", None,
         i2, "synthetic", "insufficient_data", color=(50, 55, 60))
    i3 = neg_labels(); i3["expected_insufficient"] = [
        "lens_artifacts", "satellites", "astronomical"]
    seed("insuf3.jpg", 34.5, -118.0, "2026-01-15T21:40:00+00:00", None,
         i3, "operator_manual", "insufficient_data", size=(320, 240),
         exif=False, color=(75, 65, 60))

    manifest_path = SEED_DIR / "seed_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True),
                             encoding="utf-8")
    (TEST_DIR / "seed_manifest.json").write_text(
        manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    # --- held-out test fixtures ---------------------------------------------
    make_image(TEST_DIR / "unexplained.jpg", size=(800, 600), exif=True,
               color=(15, 15, 30))
    make_image(TEST_DIR / "aircraft.jpg", size=(800, 600), exif=True,
               color=(90, 60, 40))
    fields = fields_for(40.0, -120.0, "2026-01-15T20:30:00+00:00", "180,45",
                        shape="light", location="Northern California site")
    fields["duration_seconds"] = 30
    fields["terms_accepted"] = True
    (TEST_DIR / "fields.json").write_text(
        json.dumps(fields, indent=1, sort_keys=True), encoding="utf-8"
    )

    print(f"seed images: {len(sorted(SEED_DIR.glob('*.jpg')))}")
    print(f"catalog objects: 2 (mag 2.0 at az45/el60 line, mag 7.0 at nmm1 line)")
    print(f"manifest entries: {len(manifest)}")
    print(f"test fixtures: unexplained.jpg, aircraft.jpg, fields.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
