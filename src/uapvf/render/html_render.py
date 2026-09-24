"""Self-contained verdict report HTML (FR-009, S4).

Layout (AC-005/AC-006 contracts):
  <div data-section="disclaimer">    — ET-non-assertion disclaimer header.
  <div data-section="evidence">      — verdict summary, per-category battery
                                       table, lineage classification table,
                                       source stamps, adversarial warnings,
                                       provenance metadata.
  <hr> labelled evidence/interpretation boundary.
  <div data-section="interpretation">— templated reasoning (numbers only —
                                       no category names, no stamps, no
                                       citations), uncertainty value +
                                       reason, low-confidence warning.
No JavaScript. Embedded CSS only.
"""
from __future__ import annotations

import html as html_lib
from typing import List

from uapvf import __version__
from uapvf.config import utcnow_iso

DISCLAIMER_TEXT = (
    "This report does not claim extraterrestrial origin. All findings are "
    "provisional."
)

_CSS = """
body{font-family:Georgia,'Times New Roman',serif;margin:2rem auto;max-width:900px;
padding:0 1rem;color:#1a1a2e;line-height:1.5;background:#fdfdfb}
h1{font-size:1.5rem}h2{font-size:1.15rem;border-bottom:2px solid #33475b;
padding-bottom:.25rem}
table{border-collapse:collapse;width:100%;margin:.75rem 0;font-size:.92rem}
th,td{border:1px solid #9aa5b1;padding:.4rem .55rem;text-align:left;vertical-align:top}
th{background:#eef1f4}
.stamp{font-family:'Courier New',monospace;font-size:.8rem;color:#33475b;
word-break:break-all}
.stamp strong{display:block;font-family:Georgia,'Times New Roman',serif;font-size:.9rem;
color:#1a1a2e;word-break:normal}.stamp span{display:block;word-break:normal}
.stamp details{margin-top:.3rem}.stamp summary{cursor:pointer;font-family:Georgia,'Times New Roman',serif}
.disclaimer{background:#fff3cd;border:2px solid #856404;padding:.75rem 1rem;
font-weight:bold}
.verdict{font-size:1.2rem;font-weight:bold;text-transform:uppercase}
.boundary{border:none;border-top:4px double #856404;margin:1.5rem 0}
.boundary-label{font-weight:bold;text-align:center;color:#856404;
letter-spacing:.06em}
.warning{background:#fdecea;border:1px solid #b71c1c;padding:.5rem .75rem;
margin:.4rem 0}
.contract{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.6rem;
margin:.75rem 0}.metric{background:#eef1f4;border:1px solid #c7ced5;padding:.6rem}
.metric b{display:block;font-size:1.05rem}.label{font-size:.72rem;letter-spacing:.08em;
text-transform:uppercase;color:#52606d}.claim{font-size:.78rem}
.positive{color:#1b5e20;font-weight:bold}
.negative{color:#0d47a1}
.insufficient{color:#856404;font-weight:bold}
footer{margin-top:2rem;font-size:.8rem;color:#52606d}
.brand{display:flex;align-items:center;gap:.8rem}.brand svg{width:52px;height:52px;flex:none}
"""

_BRAND_MARK = """<svg viewBox="0 0 256 256" role="img" aria-label="Verdict Foundry mark"><rect width="256" height="256" rx="48" fill="#1C1E25"/><path d="M-12 70H62c30 0 36 18 55 42l19 24" fill="none" stroke="#78DCE8" stroke-width="24" stroke-linecap="round"/><path d="M68-12v50c0 29 19 41 42 60l28 23M145 132c22 1 31 15 46 35l77 101" fill="none" stroke="#ABDFB9" stroke-width="24" stroke-linecap="round"/><circle cx="140" cy="128" r="43" fill="#1C1E25" stroke="#E9EDE6" stroke-width="8"/><path d="M105 111h70l-24 42h-22z" fill="#E9EDE6"/><circle cx="140" cy="128" r="10" fill="#1C1E25"/></svg>"""

_SOURCE_LABELS = {
    "adsb": "Aircraft track source",
    "archive": "Astronomical catalog",
    "tle": "Satellite orbital catalog",
    "lineage": "Media-analysis lineages",
    "lineage_unavailable": "Media analysis unavailable",
    "weather": "Weather observation source",
    "taxonomy": "Configured mundane taxonomy",
}

_LINEAGE_LABELS = {
    "l1-photometric": "Light and brightness",
    "l2-spectral": "Colour and spectrum",
    "l3-geometric": "Shape and geometry",
    "l4-metadata": "Capture metadata",
    "l5-trajectory": "Motion and trajectory",
    "l6-resnet50": "Certified object classifier",
    "l7-audio": "Audio signature",
}


def esc(value) -> str:
    if value is None:
        return ""
    return html_lib.escape(str(value), quote=True)


def _stamp_html(stamp: dict) -> str:
    if not stamp:
        return '<p class="stamp">(no source stamp)</p>'
    source_id = str(stamp.get("source_id") or "recorded_source")
    source = _SOURCE_LABELS.get(source_id, source_id.replace("_", " ").title())
    summary = [f"<strong>{esc(source)}</strong>"]
    if stamp.get("utc_timestamp"):
        summary.append(f"<span>Checked {esc(stamp['utc_timestamp'])} UTC</span>")
    if stamp.get("geographic_coverage"):
        summary.append(f"<span>Coverage: {esc(stamp['geographic_coverage'])}</span>")
    if stamp.get("reason"):
        summary.append(f"<span>{esc(stamp['reason'])}</span>")
    version = stamp.get("source_version") or "unspecified"
    content_hash = str(stamp.get("content_hash") or "unavailable")
    query = stamp.get("query_params") or "{}"
    detail = (
        f"Version {esc(version)} · content {esc(content_hash[:16])}…"
        f"<br>Query: {esc(query)}"
    )
    summary.append(f"<details><summary>Technical source record</summary>{detail}</details>")
    return '<div class="stamp">' + "".join(summary) + "</div>"


def render_report_html(
    case,
    battery_results: List[dict],
    lineage_outputs: List[dict],
    uncertainty: dict,
    warnings: List[dict],
    phase2: dict = None,
) -> str:
    phase2 = phase2 or {}
    total = len(battery_results)
    pos = sum(1 for r in battery_results if r["result"] == "positive")
    neg = sum(1 for r in battery_results if r["result"] == "negative")
    ins = sum(1 for r in battery_results if r["result"] == "insufficient")
    lineage_count = len(lineage_outputs)
    agreement = case["agreement_fraction"]

    battery_rows = []
    for r in battery_results:
        result = r["result"]
        battery_rows.append(
            "<tr>"
            f"<td>{esc(str(r['category']).replace('_', ' ').title())}</td>"
            f'<td class="{esc(result)}">{esc(result.upper())}</td>'
            f"<td>{esc(r.get('evidence_citation') or '—')}</td>"
            "<td>" + _stamp_html(r.get("source_stamp") or {}) + "</td>"
            "</tr>"
        )
    lineage_rows = []
    for o in lineage_outputs:
        claims = o.get("evidence_claims") or []
        if isinstance(claims, dict):
            flattened_claims = [
                *list(claims.get("shared") or []),
                *list(claims.get("specific") or []),
            ]
        elif isinstance(claims, list):
            flattened_claims = claims
        else:
            flattened_claims = [claims]
        claim_text = "; ".join(
            str(item.get("claim") or item.get("name") or item)
            if isinstance(item, dict) else str(item)
            for item in flattened_claims[:5]
        )
        provenance = o.get("provenance") or {}
        times = provenance.get("frame_timestamps_seconds") or []
        time_text = (
            ", ".join(f"{float(value):.2f}s" for value in times[:8])
            + (" …" if len(times) > 8 else "")
        ) if times else "not applicable"
        lineage_rows.append(
            "<tr>"
            f"<td>{esc(_LINEAGE_LABELS.get(o.get('lineage_id'), o.get('lineage_id')))}</td>"
            f"<td>{esc(o.get('classification') or 'No supported classification')}</td>"
            f"<td>{'yes' if o.get('artifact_detected') else 'no'}</td>"
            f"<td>{esc(o.get('hypothesis'))}</td>"
            f"<td>{esc(provenance.get('algorithm') or o.get('model_version') or 'Declared method')}</td>"
            f"<td>{esc(o.get('status') or 'unknown')}</td>"
            f'<td class="claim">{esc(claim_text or o.get("abstention_reason") or "—")}</td>'
            f'<td class="claim">{esc(time_text)}</td>'
            "</tr>"
        )
    if not lineage_rows:
        lineage_rows.append(
            '<tr><td colspan="8">No analysis lineages were run for this case.</td></tr>'
        )
    warning_html = "".join(
        f'<div class="warning">[{esc(w.get("type"))}] {esc(w.get("message"))}</div>'
        for w in (warnings or [])
    )

    if uncertainty.get("calibrated"):
        unc_value = f"{uncertainty.get('value'):.3f}"
    else:
        unc_value = "uncalibrated (null)"
    low_conf_html = ""
    if uncertainty.get("low_confidence") and lineage_count == 7 and phase2.get("rigor"):
        pct = int(round(float(agreement or 0.0) * 100))
        low_conf_html = (
            '<div class="warning">Lineage concordance was low '
            f"({pct}%). Findings should be treated with caution.</div>"
        )
    cal_source = uncertainty.get("calibration_source") or {}
    cal_source_text = (
        f"validation_set_id={esc(cal_source.get('validation_set_id'))} "
        f"validation_set_hash={esc(cal_source.get('validation_set_hash'))}"
        if cal_source
        else "none"
    )

    reasoning = (
        f"The verdict was derived mechanically by applying the tri-state "
        f"rule to the configured mundane-explanation battery. {total} "
        f"categories were evaluated: {pos} returned conclusive positive "
        f"matches, {neg} returned conclusive negative results, and {ins} "
        f"could not be tested with the supplied data. {lineage_count} "
        f"vision lineage hypotheses were considered with a recorded "
        f"concordance of {float(agreement or 0.0):.2f}. Unknown, unavailable, "
        f"and semantically non-comparable outputs never create agreement. Written "
        f"reasoning is templated from these results and is never "
        f"model-generated. No assertion is made about the object's nature "
        f"beyond the tested mundane-explanation categories, and no result "
        f"in this document amounts to a claim of novel or unexplained "
        f"phenomena."
    )
    coverage = phase2.get("coverage") or {}
    rigor = phase2.get("rigor") or {}
    refs = phase2.get("references") or {}
    runtime = phase2.get("lineage_runtime") or "unknown"

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>UAP Verdict Foundry report {esc(case['case_id'])}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        f'<header class="brand">{_BRAND_MARK}<h1>Verdict Foundry — Case Verdict Report</h1></header>',
        f'<div class="disclaimer" data-section="disclaimer">'
        f"{esc(DISCLAIMER_TEXT)}</div>",
        '<div data-section="evidence">',
        "<h2>EVIDENCE FINDINGS</h2>",
        f"<p>Case: {esc(case['case_id'])} — run version {esc(case['run_version'])}</p>",
        f'<p class="verdict">Verdict: {esc(case["verdict"])}</p>',
        f"<p>{esc(case['verdict_text'])}</p>",
        "<h3>Battery results</h3>",
        "<table><thead><tr><th>Category</th><th>Result</th>"
        "<th>Evidence citation</th><th>Source stamp</th></tr></thead><tbody>",
        "".join(battery_rows),
        "</tbody></table>",
        "<h3>Media-analysis lineage summary</h3>",
        '<div class="contract">',
        f'<div class="metric"><span class="label">Runtime</span><b>{esc(runtime)}</b></div>',
        f'<div class="metric"><span class="label">Sandbox verified</span><b>{"YES" if phase2.get("sandbox_verified") else "NO"}</b></div>',
        f'<div class="metric"><span class="label">Rigor adjudication</span><b>{"PASS" if rigor.get("passed") else "NOT PASSED"}</b></div>',
        f'<div class="metric"><span class="label">Battery coverage</span><b>{"COMPLETE" if coverage.get("complete") else "PARTIAL"}</b></div>',
        f'<div class="metric"><span class="label">References</span><b>{"VERIFIED" if refs.get("valid") else "DEGRADED"}</b></div>',
        '</div>',
        "<h3>Lineage classifications and claims</h3>",
        "<table><thead><tr><th>Lineage</th><th>Classification</th>"
        "<th>Artifact detected</th><th>Hypothesis</th><th>Model version</th>"
        "<th>Status</th><th>Validated claims / abstention</th><th>Video timestamps</th>"
        "</tr></thead><tbody>",
        "".join(lineage_rows),
        "</tbody></table>",
        "<h3>Adversarial input warnings</h3>",
        warning_html or "<p>None recorded.</p>",
        "<h3>Provenance</h3>",
        "<table><tbody>"
        f"<tr><th>media_sha256</th><td class='stamp'>{esc(case['media_sha256'])}</td></tr>"
        f"<tr><th>observed_at</th><td>{esc(case['observed_at'])}</td></tr>"
        f"<tr><th>latitude</th><td>{esc(case['latitude'])}</td></tr>"
        f"<tr><th>longitude</th><td>{esc(case['longitude'])}</td></tr>"
        f"<tr><th>quality_score</th><td>{esc(case['quality_score'])}</td></tr>"
        f"<tr><th>quality_gate_pass</th><td>{'1' if case['quality_gate_pass'] else '0'}</td></tr>"
        f"<tr><th>lineage_concordance</th><td>{esc(case['agreement_fraction'])}</td></tr>"
        f"<tr><th>generated_at</th><td>{esc(utcnow_iso())}</td></tr>"
        f"<tr><th>software_version</th><td>{esc(__version__)}</td></tr>"
        "</tbody></table>",
        "</div>",
        '<hr class="boundary">',
        '<p class="boundary-label">EVIDENCE FINDINGS — ABOVE / '
        "INTERPRETIVE COMMENTARY — BELOW</p>",
        '<div data-section="interpretation">',
        "<h2>INTERPRETIVE COMMENTARY</h2>",
        f"<p>{reasoning}</p>",
        f"<p>Uncertainty: <strong>{unc_value}</strong> "
        f"({'calibrated' if uncertainty.get('calibrated') else 'uncalibrated'}). "
        f"Uncertainty reason: {esc(uncertainty.get('uncertainty_reason'))}. "
        f"Calibration source: {cal_source_text}.</p>",
        low_conf_html,
        "</div>",
        "<footer>Generated by UAP Verdict Foundry. This document does not "
        "claim or imply any conclusion beyond the stated battery results.</footer>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts)
