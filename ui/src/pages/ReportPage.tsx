// /ui/cases/:caseId/report — spec §9.6. Guard-then-fetch (D-34), the
// evidence/interpretation divider, and fetch-to-blob JSON download.

import { useEffect } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button, Card, ErrorCard, SectionLabel, SizedContent, Skeleton, VerdictBadge } from "../components/ui";
import { EvidenceDivider } from "../components/EvidenceDivider";
import { SourceStampDisplay } from "../components/SourceStampDisplay";
import { caseDetailKey, fetchCaseDetail, fetchReport, reportKey } from "../lib/queries";
import { NotFoundError, downloadBlob } from "../lib/api";
import { formatUtc } from "../lib/format";

export function ReportPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();

  // Guard first: the case's report_available flag decides whether the
  // report fetch is allowed at all (spec §9.6).
  const guard = useQuery({
    queryKey: caseDetailKey(caseId),
    queryFn: () => fetchCaseDetail(caseId),
    retry: 0,
  });

  const guardCase = guard.data?.case;
  const ready = guardCase?.report_available === true;

  useEffect(() => {
    if (guardCase && !ready) {
      toast("Report not yet available.");
      navigate(`/ui/cases/${caseId}`, { replace: true });
    }
  }, [guardCase, ready, caseId, navigate]);

  const report = useQuery({
    queryKey: reportKey(caseId),
    queryFn: () => fetchReport(caseId),
    enabled: ready,
    retry: 0,
  });

  if (guard.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-72 w-full rounded-lg" />
      </div>
    );
  }
  if (guard.error instanceof NotFoundError || !guardCase) {
    return (
      <ErrorCard
        message="Case not found."
        hint="It may have been deleted."
        action={
          <Button variant="secondary" onClick={() => navigate("/ui/cases")}>
            Back to cases
          </Button>
        }
      />
    );
  }
  if (!ready) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Redirecting to case">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }

  if (report.isLoading) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Loading report">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-40 w-full rounded-lg" />
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }
  if (report.error) {
    // §3.9 mismatch state: flag true but GET failed/404.
    return (
      <ErrorCard
        message="Report unavailable."
        hint="The report is marked available but could not be fetched. It may have been regenerated or removed."
        action={
          <Button variant="secondary" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        }
      />
    );
  }
  if (!report.data) return null;
  const data = report.data;
  // FR-005: the plurality fraction is defined once 2+ engine lineages
  // reported — rigor certification is a live-mode gate, not a
  // precondition for displaying the computed concordance.
  const observedLineages =
    data.analysis_contract?.observed_lineages ?? data.lineage_outputs.length;
  const concordanceComparable = observedLineages >= 2;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div>
          <p className="font-mono text-xs text-ink-3">
            case {caseId.slice(0, 8)} · generated {formatUtc(data.generated_at)}
          </p>
          <h1 className="mt-1 font-display text-2xl font-semibold text-ink">
            Case report
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            onClick={() =>
              downloadBlob(
                JSON.stringify(data, null, 2),
                `report_${caseId}.json`,
                "application/json",
              )
            }
          >
            Download JSON
          </Button>
          <Button variant="ghost" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        </div>
      </header>

      <Card className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <VerdictBadge verdict={data.verdict} />
          {data.uncertainty.value !== null && (
            <span className="text-xs text-ink-3 tnum">
              uncertainty {data.uncertainty.value.toFixed(2)}
              {data.uncertainty.calibrated ? " (calibrated)" : " (unadjusted)"}
            </span>
          )}
        </div>
        {data.warnings.length > 0 && (
          <ul className="mt-4 flex flex-col gap-1 border-t border-line pt-3">
            {data.warnings.map((warning, index) => (
              <li key={index} className="text-xs text-st-run">
                {warning.message}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {data.analysis_contract && (
        <Card className="overflow-hidden p-0">
          <div className="border-b border-line bg-inset px-6 py-4">
            <SectionLabel>Analysis contract</SectionLabel>
            <p className="mt-1 text-sm text-ink-2">
              Four mundane-explanation battery categories plus vision
              lineages driven by the Sneferu engine; concordance is the
              plurality fraction of lineage classifications. Case-scoped OS
              sandboxing is deferred to phase 2.
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-px bg-line sm:grid-cols-5">
            {[
              ["Runtime", data.analysis_contract.lineage_runtime],
              ["Lineages", `${observedLineages}/${data.analysis_contract.expected_lineages}`],
              ["Sandbox", data.analysis_contract.sandbox_verified ? "Verified" : "Not verified (phase 2)"],
              ["Rigor", data.analysis_contract.rigor?.runtime
                ? data.analysis_contract.rigor.passed ? "Passed" : "Not passed"
                : "Not run"],
              ["Coverage", data.analysis_contract.coverage?.complete ? "Complete" : "Qualified"],
            ].map(([label, value]) => (
              <div key={label} className="bg-raised p-4">
                <dt className="text-xs text-ink-3">{label}</dt>
                <dd className="mt-1 text-sm font-semibold text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>
      )}

      {/* Evidence findings — above */}
      <Card className="p-6">
        <SectionLabel>Battery results</SectionLabel>
        <div className="mt-3 overflow-x-auto lg:overflow-visible">
          <table className="w-full border-collapse text-xs lg:table-min-md">
            <thead>
              <tr className="border-b border-line text-left uppercase text-ink-3" style={{ letterSpacing: "var(--tracking-caps)" }}>
                <th scope="col" className="py-2 pr-4 font-medium">Category</th>
                <th scope="col" className="py-2 pr-4 font-medium">Result</th>
                <th scope="col" className="py-2 pr-4 font-medium">Evidence citation</th>
                <th scope="col" className="py-2 font-medium">Source stamp</th>
              </tr>
            </thead>
            <tbody>
              {data.battery_results.map((row) => (
                <tr key={row.category} className="border-b border-line align-top last:border-b-0">
                  <td className="py-2 pr-4 font-mono text-ink-2">{row.category}</td>
                  <td
                    className={`py-2 pr-4 ${
                      row.result === "positive"
                        ? "text-st-ok"
                        : row.result === "negative"
                          ? "text-ink-2"
                          : "text-st-run"
                    }`}
                  >
                    {row.result}
                  </td>
                  <td className="py-2 pr-4 text-ink-3">{row.evidence_citation ?? "—"}</td>
                  <td className="py-2 pr-2">
                    <SourceStampDisplay
                      stamp={row.source_stamp}
                      caseId={caseId}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="p-6">
        <SectionLabel>Lineage outputs</SectionLabel>
        <div className="mt-3 overflow-x-auto lg:overflow-visible">
          <table className="w-full border-collapse text-xs lg:table-min-sm">
            <thead>
              <tr className="border-b border-line text-left uppercase text-ink-3" style={{ letterSpacing: "var(--tracking-caps)" }}>
                <th scope="col" className="py-2 pr-4 font-medium">Lineage</th>
                <th scope="col" className="py-2 pr-4 font-medium">Classification</th>
                <th scope="col" className="py-2 font-medium">Artifact detected</th>
                <th scope="col" className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.lineage_outputs.map((row) => (
                <tr key={row.lineage_id} className="border-b border-line last:border-b-0">
                  <td className="py-2 pr-4 font-mono text-ink-2">{row.lineage_id}</td>
                  <td className="py-2 pr-4 text-ink-2">{row.classification ?? "—"}</td>
                  <td className="py-2 text-ink-2">
                    {row.artifact_detected ? "Yes" : "No"}
                  </td>
                  <td className="py-2 text-ink-2">{row.status ?? "unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* ONE controlled material interruption — shared component, so the
          case detail and report surfaces can never drift. */}
      <EvidenceDivider />

      {/* Round 4: the xenoscience/story-asset interpretation card was part
          of the removed phase-2 speculative surface — the divider plus the
          verdict text below are the report's only interpretation material. */}

      {/* Interpretive commentary — below */}
      <Card className="p-6">
        <SectionLabel>Verdict text</SectionLabel>
        <SizedContent content={data.verdict_text} as="div" caseId={caseId} />
        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-line pt-4 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-ink-3">Uncertainty</dt>
            <dd className="mt-0.5 text-ink-2 tnum">
              {data.uncertainty.value === null
                ? "Not computed"
                : data.uncertainty.value.toFixed(2)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Calibrated</dt>
            <dd className="mt-0.5 text-ink-2">
              {data.uncertainty.calibrated ? "Yes" : "No"}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Reason</dt>
            <dd className="mt-0.5 text-ink-2">
              {data.uncertainty.uncertainty_reason ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Lineage concordance</dt>
            <dd className="mt-0.5 text-ink-2 tnum">
              {!concordanceComparable
                ? "Not comparable"
                : data.agreement_fraction === null
                  ? "—"
                : `${Math.round(data.agreement_fraction * 100)}%`}
            </dd>
          </div>
        </dl>
        <p className="mt-4 font-mono text-xs text-ink-3">
          media sha256 {(data.provenance.media_sha256 ?? "—").slice(0, 16)}… ·
          software {data.software_version}
        </p>
      </Card>
    </div>
  );
}
