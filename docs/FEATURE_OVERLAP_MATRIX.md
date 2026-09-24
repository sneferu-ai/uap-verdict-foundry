# Feature overlap and product differentiation

Verified against official product documentation on 2026-08-21. This is a
capability comparison, not a claim that the referenced tools are equivalent
products or that Verdict Foundry replaces them.

| Capability | Official reference | Overlap | Verdict Foundry integration |
|---|---|---|---|
| Live/historical aircraft positions and traces | [ADS-B Exchange API v2](https://www.adsbexchange.com/api/aircraft/v2/docs) | Strong source-layer overlap | Time/location window check with a frozen source stamp; absence is never treated as proof when coverage is unavailable. |
| Satellite catalog visualization | [Stellarium User Guide](https://stellarium.org/files/guide.pdf) | Catalog and satellite-domain overlap | Scheduled TLE catalog with substantive-change hashing, staleness gate, and tri-state battery output. |
| Blind astronomical image calibration | [Astrometry.net documentation](https://astrometry.net/doc/readme.html) | Astronomical cross-match overlap | Viewing-direction-gated astronomical battery category; insufficient rather than invented when geometry is absent. |
| Error-level analysis and image provenance inspection | [FotoForensics tutorials](https://fotoforensics.com/tutorial.php) | Strong forensic-image inspection overlap | Decoder-isolated media normalization plus seven declared analytical lineages, source-stamped mundane checks, rigor adjudication, and a signed end-to-end case record. |
| Media metadata extraction | [ExifTool feature documentation](https://www.exiftool.org/) | Metadata and container inspection overlap | One of seven independently declared lineages; findings include provenance and validated claims. |
| Scene/video reconstruction and debunking tools | [Metabunk tools maintained by its administrator](https://www.metabunk.org/threads/metabunk-tools-list-and-links.13220/) | Analysis-method overlap | Case workflow combines media checks with aircraft, satellites, weather, artifacts, rigor adjudication, coverage, and an audit chain. |

The defensible differentiator is the combination: one intake accepts images
or video, preserves provenance, runs seven case-sandboxed lineages, evaluates
eleven mundane-explanation categories, records honest insufficient results,
adjudicates rigor, freezes an evidence/interpretation boundary, emits a signed
buyer package, and optionally projects labelled story material into a
non-evidence canon graph. No single comparison above documents that combined
workflow.

Known boundaries are intentional: external source coverage still depends on
operator credentials and geography; the ResNet lineage abstains until signed
weights and a certified mapping are installed; simulation is never presented
as live analysis; and speculative content never feeds forensic results.
