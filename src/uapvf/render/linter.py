"""Export linter: banned ET-assertion phrases (spec §5, FR-009).

Block-based scanner with precise section rules:
  1. The HTML report is divided into sections by `data-section` attributes:
     disclaimer | evidence | interpretation.
  2. Disclaimer: all phrases allowed (negation/disclaimer context).
  3. Evidence: no banned phrase in any context.
  4. Interpretation: a banned phrase is allowed only when preceded by a
     negation phrase within the same sentence. Sentences are delimited by
     ". ", "! ", "? ". Negations are matched case-insensitively with
     word-boundary regexes. HTML entities are decoded before scanning and
     all element text (including alt/title attributes) is scanned.
  5. Missing data-section attributes -> "report section attributes missing".
For report.json the same sentence/negation rule is applied to verdict_text,
every evidence_citation, and each lineage hypothesis.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from html.parser import HTMLParser
from typing import List, Tuple

BANNED_PHRASES = [
    "extraterrestrial origin",
    "alien origin",
    "non-human intelligence",
    "off-world",
    "not of this earth",
    "alien craft",
    "extraterrestrial craft",
    "interstellar origin",
    "anomalous vehicle",
    "craft of unknown origin",
    "extraterrestrial",
    "alien",
]

NEGATION_PHRASES = [
    "does not claim",
    "does not assert",
    "is not",
    "no evidence of",
    "not",
    "without",
]

SECTION_DISCLAIMER = "disclaimer"
SECTION_EVIDENCE = "evidence"
SECTION_INTERPRETATION = "interpretation"
REQUIRED_SECTIONS = (SECTION_DISCLAIMER, SECTION_EVIDENCE, SECTION_INTERPRETATION)


def _phrase_pattern(phrase: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def split_sentences(text: str) -> List[str]:
    """Split on '. ', '! ', '? ' delimiters (spec §5)."""
    if not text:
        return []
    parts = re.split(r"(?<=[.!?)])\s+", text)
    return [p for p in parts if p.strip()]


def _negation_precedes(sentence: str, phrase_start: int) -> bool:
    lowered = sentence.lower()
    for neg in NEGATION_PHRASES:
        for m in _phrase_pattern(neg).finditer(lowered):
            if m.start() < phrase_start:
                return True
    return False


def _check_text_interpretation(text: str) -> List[str]:
    """Interpretation rule: banned phrases allowed only when negated in the
    same sentence. Returns violation descriptions."""
    violations = []
    for sentence in split_sentences(text):
        for phrase in BANNED_PHRASES:
            for m in _phrase_pattern(phrase).finditer(sentence):
                if not _negation_precedes(sentence, m.start()):
                    violations.append(
                        f"banned phrase {phrase!r} in interpretation without "
                        f"preceding negation (sentence: {sentence[:120]!r})"
                    )
    return violations


class _SectionExtractor2(HTMLParser):
    """Robust extractor pairing data-section opens with closes via an
    explicit stack of (tag, section-or-None)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: List[Tuple[str, str]] = []  # (tag, section or "")
        self.sections = {name: [] for name in REQUIRED_SECTIONS}
        self.seen_sections = set()

    def _current_section(self):
        for _tag, sec in reversed(self.stack):
            if sec:
                return sec
        return None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        section = attrs.get("data-section") or ""
        if section:
            self.seen_sections.add(section)
        self.stack.append((tag, section))
        target = section if section in self.sections else self._current_section()
        if target in self.sections:
            for attr in ("alt", "title"):
                value = attrs.get(attr)
                if value:
                    self.sections[target].append(" " + value + " ")

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not data:
            return
        sec = self._current_section()
        if sec in self.sections:
            self.sections[sec].append(data)


def extract_sections(html_text: str) -> Tuple[dict, set]:
    parser = _SectionExtractor2()
    parser.feed(html_text)
    parser.close()
    return parser.sections, parser.seen_sections


def lint_report_html(html_text: str) -> dict:
    """Returns {ok, violations[]}."""
    violations: List[str] = []
    if "data-section" not in html_text:
        return {
            "ok": False,
            "violations": ["report section attributes missing"],
        }
    sections, seen = extract_sections(html_text)
    for required in REQUIRED_SECTIONS:
        if required not in seen:
            violations.append("report section attributes missing")
            break
    evidence_text = " ".join(sections.get(SECTION_EVIDENCE, []))
    evidence_text = html_lib.unescape(evidence_text)
    for phrase in BANNED_PHRASES:
        if _phrase_pattern(phrase).search(evidence_text):
            violations.append(
                f"banned phrase {phrase!r} present in evidence section"
            )
    interp_text = " ".join(sections.get(SECTION_INTERPRETATION, []))
    interp_text = html_lib.unescape(interp_text)
    violations.extend(_check_text_interpretation(interp_text))
    return {"ok": not violations, "violations": violations}


def lint_report_json(report: dict) -> dict:
    """Same sentence/negation rule on verdict_text, evidence_citation, and
    lineage hypotheses."""
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except Exception:
            return {"ok": False, "violations": ["report.json unparseable"]}
    violations: List[str] = []
    texts = []
    verdict_text = report.get("verdict_text")
    if verdict_text:
        texts.append(("verdict_text", str(verdict_text)))
    for br in report.get("battery_results") or []:
        citation = br.get("evidence_citation")
        if citation:
            texts.append((f"evidence_citation[{br.get('category')}]", str(citation)))
    for lo in report.get("lineage_outputs") or []:
        hyp = lo.get("hypothesis")
        if hyp:
            texts.append((f"lineage_outputs[{lo.get('lineage_id')}].hypothesis", str(hyp)))
    for label, text in texts:
        for v in _check_text_interpretation(text):
            violations.append(f"{label}: {v}")
    return {"ok": not violations, "violations": violations}


def lint_all(html_text: str, report_json) -> dict:
    html_result = lint_report_html(html_text)
    json_result = lint_report_json(report_json)
    violations = [f"html: {v}" for v in html_result["violations"]] + [
        f"json: {v}" for v in json_result["violations"]
    ]
    return {"ok": not violations, "violations": violations}
