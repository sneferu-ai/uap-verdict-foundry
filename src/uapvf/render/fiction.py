"""Fiction seed formatting + non-evidence labelling enforcement
(FR-011, FR-012, S5).

File layout is EXACT (spec S5):
  1. YAML front matter between `---` lines: case_id, generated_at,
     non_evidence: true, and the derived-fiction statement.
  2. Banner: === SPECULATIVE FICTION — NOT FORENSIC EVIDENCE — DO NOT CITE
     AS EVIDENCE ===
  3. One blank line.
  4. Narrative paragraphs only — each begins with the visible prefix
     "[SPECULATIVE FICTION — NOT FORENSIC EVIDENCE] " followed by >= 40
     characters of substantive text; no headings, lists, code blocks, or
     blockquotes.
File size cap: 10 KB. The quarantined fiction codepath shares nothing with
report generation.
"""
from __future__ import annotations

from typing import List, Tuple

from uapvf.config import utcnow_iso

FICTION_PREFIX = "[SPECULATIVE FICTION — NOT FORENSIC EVIDENCE] "
FICTION_BANNER = (
    "=== SPECULATIVE FICTION — NOT FORENSIC EVIDENCE — DO NOT CITE AS "
    "EVIDENCE ==="
)
FICTION_STATEMENT = (
    "This document is derived fiction. It is not a forensic record and "
    "must not be cited as evidence."
)
FICTION_MAX_BYTES = 10 * 1024
MIN_PARAGRAPH_CHARS = 40


def build_fiction_text(case_id: str, paragraphs: List[str],
                       generated_at: str = "") -> str:
    """Assemble the .fic.md file in code from structured paragraphs."""
    generated_at = generated_at or utcnow_iso()
    lines = [
        "---",
        f"case_id: {case_id}",
        f"generated_at: {generated_at}",
        "non_evidence: true",
        f'statement: "{FICTION_STATEMENT}"',
        "---",
        FICTION_BANNER,
        "",
    ]
    body_paragraphs = []
    for p in paragraphs:
        text = " ".join(str(p).split())
        if not text:
            continue
        body_paragraphs.append(FICTION_PREFIX + text)
    # join(lines) ends "...BANNER\n"; the extra "\n" supplies the single
    # mandated blank line between banner and body (S5 layout item 3).
    text_out = "\n".join(lines) + "\n" + "\n\n".join(body_paragraphs) + "\n"
    encoded = text_out.encode("utf-8")
    if len(encoded) > FICTION_MAX_BYTES:
        # Trim paragraphs (never labels) to fit the 10 KB cap.
        while body_paragraphs and len(encoded) > FICTION_MAX_BYTES:
            body_paragraphs.pop()
            text_out = "\n".join(lines) + "\n" + "\n\n".join(body_paragraphs) + "\n"
            encoded = text_out.encode("utf-8")
    return text_out


def validate_fiction_text(text: str) -> Tuple[bool, List[str]]:
    """Validate the exact S5 layout. Returns (ok, reasons)."""
    reasons: List[str] = []
    if not isinstance(text, str) or not text.strip():
        return False, ["fiction seed is empty"]
    if len(text.encode("utf-8")) > FICTION_MAX_BYTES:
        reasons.append("fiction seed exceeds 10 KB cap")

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        reasons.append("missing YAML front matter opening delimiter")
        return False, reasons
    try:
        close_idx = next(
            i for i in range(1, len(lines)) if lines[i].strip() == "---"
        )
    except StopIteration:
        reasons.append("missing YAML front matter closing delimiter")
        return False, reasons
    front = "\n".join(lines[1:close_idx])
    for required in ("case_id:", "generated_at:", "non_evidence: true"):
        if required not in front:
            reasons.append(f"front matter missing {required!r}")
    if FICTION_STATEMENT not in front:
        reasons.append("front matter missing the derived-fiction statement")

    rest = lines[close_idx + 1 :]
    # Banner must be the next non-empty line, exactly.
    i = 0
    while i < len(rest) and rest[i].strip() == "":
        i += 1
    if i >= len(rest) or rest[i].strip() != FICTION_BANNER:
        reasons.append("missing or malformed speculative-fiction banner line")
        i = len(rest)
    else:
        i += 1
    # One blank line after banner.
    if i >= len(rest) or rest[i].strip() != "":
        reasons.append("expected exactly one blank line after banner")
    else:
        i += 1

    body = "\n".join(rest[i:]).strip("\n")
    if not body:
        reasons.append("no narrative paragraphs present")
        return (not reasons), reasons
    paragraphs = [p for p in body.split("\n\n") if p.strip()]
    for idx, para in enumerate(paragraphs, start=1):
        plines = para.split("\n")
        for ln in plines:
            stripped = ln.strip()
            if stripped.startswith(("#", "- ", "* ", "> ", "```")):
                reasons.append(
                    f"paragraph {idx} contains forbidden heading/list/code/"
                    "blockquote syntax"
                )
        if not para.startswith(FICTION_PREFIX):
            reasons.append(f"paragraph {idx} missing the non-evidence prefix")
            continue
        substantive = para[len(FICTION_PREFIX):].strip()
        if len(substantive) < MIN_PARAGRAPH_CHARS:
            reasons.append(
                f"paragraph {idx} has fewer than {MIN_PARAGRAPH_CHARS} "
                "characters of substantive text"
            )
    return (not reasons), reasons
