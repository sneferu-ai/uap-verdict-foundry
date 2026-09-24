"""Seven-lineage multi-modality analysis package (spec §3.2).

Seven lineages across four modality classes produce contract-valid
``LineageOutput`` records that the rigor engine (``uapvf.rigor``) scores
with classification-gated refutation-consistent concordance. Lineage
implementations run in per-case sandboxed subprocesses (FR-017); this
package holds the shared protocol, taxonomy, vocabulary, sampling,
registry, and independence-validation code.
"""
from uapvf.lineages.protocol import (
    REASON_INSUFFICIENT_EVIDENCE,
    REQUIRED_SHARED_CLAIMS,
    REQUIRED_SPECIFIC_CLAIMS,
    STATUS_NON_COMPARABLE,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    EvidenceClaims,
    LineageOutput,
    LineageRuntime,
    ProtocolError,
    make_non_comparable,
)
from uapvf.lineages.taxonomy import (
    EXPECTED_CATEGORY_COUNT,
    TAXONOMY,
    assert_taxonomy,
    is_valid_taxonomy,
)

assert_taxonomy()

__all__ = [
    "EXPECTED_CATEGORY_COUNT",
    "REASON_INSUFFICIENT_EVIDENCE",
    "REQUIRED_SHARED_CLAIMS",
    "REQUIRED_SPECIFIC_CLAIMS",
    "STATUS_NON_COMPARABLE",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "TAXONOMY",
    "EvidenceClaims",
    "LineageOutput",
    "LineageRuntime",
    "ProtocolError",
    "assert_taxonomy",
    "is_valid_taxonomy",
    "make_non_comparable",
]
