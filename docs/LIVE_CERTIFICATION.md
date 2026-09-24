# Live Certification

`UAPV_LINEAGE_RUNTIME=live` is a fail-closed production mode. It becomes
ready only when the service has a production ADS-B provider, a fresh orbital
catalog, verified signed ResNet-50 artifacts, and real seven-lineage
independence and calibration evidence. Simulation can exercise the same code,
but it never satisfies these gates.

## 1. Configure real providers

Use ADS-B Exchange with a production API key:

```dotenv
ADSB_SOURCE_URL=https://www.adsbexchange.com/api/aircraft/v2
ADSB_SOURCE_API_KEY=YOUR_KEY
TLE_CATALOG_URL=https://celestrak.org/NORAD/elements/gp.php?GROUP=ACTIVE&FORMAT=JSON
UAPV_EGRESS_ALLOWLIST=www.adsbexchange.com,celestrak.org
```

The CelesTrak URL returns current OMM JSON and supports six-digit catalog
numbers. The service downloads it on startup when no current catalog exists,
then no more often than `UAPV_TLE_REFRESH_HOURS` (default six hours). Cases use
the stored catalog and never trigger a provider download.

Authenticated OpenSky is supported as an evaluation alternative:

```dotenv
ADSB_SOURCE_URL=https://opensky-network.org/api/states/all
OPENSKY_CLIENT_ID=YOUR_CLIENT_ID
OPENSKY_CLIENT_SECRET=YOUR_CLIENT_SECRET
UAPV_EGRESS_ALLOWLIST=opensky-network.org,auth.opensky-network.org,celestrak.org
```

Anonymous OpenSky is deliberately marked `configured_anonymous_evaluation_only`
and cannot make live readiness pass. A custom gateway is also supported, but
it must return explicit `coverage_confirmed: true` before an empty result can
be treated as a negative.

## 2. Install the pinned signed model

Download the exact pinned ONNX Model Zoo artifact and confirm the supplier
hash before installation:

```bash
curl -L --fail \
  'https://huggingface.co/onnxmodelzoo/resnet50-v1-12-int8/resolve/d843735a7f2c4a254931a633f6dc5758d52f06ee/resnet50-v1-12-int8.onnx?download=true' \
  -o /tmp/resnet50-v1-12-int8.onnx
shasum -a 256 /tmp/resnet50-v1-12-int8.onnx
# c234f30975989788b4405f25253275aae247ab6dbdd34aaa69ab0a59ff76f6d0

export UAPV_CLI_TOKEN="$UAPV_OPERATOR_TOKEN"
uapvf model install-resnet50 --file /tmp/resnet50-v1-12-int8.onnx
```

The install command rejects any supplier-hash mismatch, copies the weights and
the fixed ImageNet taxonomy mapping into the operator state directory, and
creates an Ed25519-signed deployment receipt. Copy the four returned values
into `.env` as `UAPV_RESNET50_ONNX`, `UAPV_RESNET50_MAPPING`,
`UAPV_RESNET50_RECEIPT`, and `UAPV_RESNET50_TRUSTED_PUBLIC_KEY`, restart the
service, then run:

```bash
uapvf model verify-resnet50
```

Verification checks the official supplier hash, exact weights and mapping
hashes, signature, pinned operator public key, and runtime mapping contract.

## 3. Record the real operator evaluation collection

Create a JSON collection manifest. Paths may be absolute or relative to the
manifest. Ground-truth labels are read only after all seven sandboxed lineage
inferences complete for an item.

```json
{
  "schema_version": 1,
  "items": [
    {
      "item_id": "general-0001",
      "media": "media/general-0001.mp4",
      "independence_subset": "general",
      "difficulty": "hard",
      "calibration_subset": "general",
      "label": "aircraft"
    },
    {
      "item_id": "audio-0001",
      "media": "media/audio-0001.mp4",
      "independence_subset": "l7_enriched",
      "difficulty": null,
      "calibration_subset": null
    }
  ]
}
```

The independence collection must contain at least 300 items:

- 100 `general`, including at least 30% `hard` or `ambiguous`;
- 100 `l6_enriched`;
- 50 `l4_enriched`;
- 50 `l7_enriched`.

At least 100 items must also set `calibration_subset`; at least 60 calibration
items must be L6-covered (`aircraft`, `bird`, or `insect`). Labels must use the
product taxonomy. L4-enriched material needs meaningful capture metadata, and
L7-enriched material needs real audio-bearing video; duplicated or relabelled
fixtures are not a valid production collection.

Record the collection:

```bash
uapvf lineages record-eval --manifest /absolute/path/collection.json
```

The recorder copies each item into an isolated case directory, runs all seven
lineages with network denied, records the media SHA-256, and promotes neither
manifest until both pass their full composition/schema checks. Progress is
checkpointed; rerunning the same unchanged manifest resumes after a failure
without repeating completed sandbox work. A single-recorder lock prevents two
certification jobs from overwriting each other.

## 4. Run the statistical gates

```bash
uapvf lineages validate
uapvf lineages calibrate
```

Independence evaluates all 21 lineage pairs with 10,000 seeded bootstrap
resamples, a 99.5% interval, at least 100 comparable items per pair, and an
upper Cohen-kappa bound below 0.2. Calibration requires every lineage to have
at least 80 comparable predictions and at least 60% accuracy. The stored
reports are bound to the exact recorder manifest SHA-256 and blinded sandbox
provenance; changing the manifest after either command invalidates readiness.

A failed result is data, not a setup error. Improve the affected lineage or
the legitimately under-covered evaluation subset, record again, and rerun the
gates. Never hand-edit classifications or replace abstentions to make a gate
pass.

## 5. Promote and verify

Set `UAPV_LINEAGE_RUNTIME=live`, restart once after all environment values are
present, and inspect readiness:

```bash
curl -s http://127.0.0.1:8470/readyz | python3 -m json.tool
```

Do not accept production cases until it returns HTTP 200 with `ready: true`,
`adsb: configured`, `tle: fresh`, `resnet50.verified: true`, and both lineage
certification booleans true. Provider outages during a later case yield an
honest `insufficient` category result; they never become an invented negative.
