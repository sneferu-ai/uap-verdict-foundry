"""Egress allowlist guard for adapter HTTP calls (FR-017 boundary).

Every outbound URL an adapter intends to fetch must pass ``validate_url``
first. The allowlist is a comma-separated set of host patterns read from
``UAPV_EGRESS_ALLOWLIST`` (default covers the two configured ADS-B data
providers). ``*`` wildcard prefixes match any subdomain; exact hosts match
only themselves. Only ``https`` URLs are permitted — plaintext HTTP
credentials/tokens are a hard refusal. Loopback URLs are permitted only
when ``UAPV_EGRESS_ALLOW_LOOPBACK=1`` (mock-mode rigs and the Sneferu
engine probe run on loopback).

This is a fail-closed check: an unparseable or unlisted URL raises
``ValueError`` and the adapter converts that into an honest
``insufficient`` result with a sentinel stamp — never a silent request.
"""
from __future__ import annotations

from typing import Iterable, List
from urllib.parse import urlparse

DEFAULT_EGRESS_ALLOWLIST = "*.adsbexchange.com,*.opensky-network.org"


class EgressDenied(ValueError):
    """URL is not on the egress allowlist (or is malformed)."""


EgressDeniedError = EgressDenied
"""Backward-compatible name retained for existing adapter imports."""


def allowlist(settings=None) -> List[str]:
    """The active host patterns, lowercased and de-blanked."""
    raw = DEFAULT_EGRESS_ALLOWLIST
    if settings is not None:
        raw = getattr(settings, "UAPV_EGRESS_ALLOWLIST", raw) or raw
    patterns = [p.strip().lower() for p in str(raw).split(",")]
    return [p for p in patterns if p]


def _host_matches(hostname: str, patterns: Iterable[str]) -> bool:
    host = hostname.lower().rstrip(".")
    for pattern in patterns:
        if pattern.startswith("*."):
            suffix = pattern[1:]  # ".opensky-network.org"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    return False


def validate_url(url: str, settings=None) -> bool:
    """Return true for an allowlisted URL, otherwise raise EgressDenied.

    Adapters call this immediately before any httpx/requests call so a
    config-poisoned endpoint string can never produce a network request.
    """
    if not url or not isinstance(url, str):
        raise EgressDenied("empty egress URL")
    try:
        parsed = urlparse(url)
    except Exception as exc:  # pragma: no cover - urlparse rarely raises
        raise EgressDenied(f"unparseable egress URL: {exc}") from exc
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise EgressDenied("egress URL has no hostname")
    if scheme == "https":
        pass
    elif scheme == "http":
        allow_loopback = False
        if settings is not None:
            allow_loopback = bool(
                getattr(settings, "UAPV_EGRESS_ALLOW_LOOPBACK", False))
        else:
            import os
            allow_loopback = os.environ.get(
                "UAPV_EGRESS_ALLOW_LOOPBACK", "0") == "1"
        if not (allow_loopback and hostname in ("127.0.0.1", "localhost", "::1")):
            raise EgressDenied("plaintext http egress is not permitted")
    else:
        raise EgressDenied(f"unsupported egress scheme: {scheme!r}")
    patterns = allowlist(settings)
    if not _host_matches(hostname, patterns):
        raise EgressDenied(
            f"egress host {hostname!r} is not allowlisted")
    return True
