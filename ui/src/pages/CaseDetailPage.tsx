// /ui/cases/:caseId — spec §9.4. Poster-like header (one oversized signal:
// the status), the evidence/interpretation material interruption, and
// cost-gated actions behind ConfirmDialogs.

import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Copy } from "lucide-react";
import {
  Button,
  Card,
  CliHint,
  EmptyState,
  ErrorCard,
  ModeBadge,
  PaymentBadge,
  QualityGateBadge,
  SectionLabel,
  Skeleton,
  StatusBadge,
  VerdictBadge,
} from "../components/ui";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EvidenceDivider } from "../components/EvidenceDivider";
import { SourceStampDisplay } from "../components/SourceStampDisplay";
import { useAuth } from "../auth/AuthContext";
import { usePolling, CASE_DETAIL_BANDS, CASE_DETAIL_STOP_MS } from "../lib/use-polling";
import { caseDetailKey, fetchCaseDetail } from "../lib/queries";
import {
  ApiError,
  AuthExpiredError,
  ConflictError,
  NotFoundError,
  downloadBlob,
  jsonGet,
} from "../lib/api";
import {
  copyText,
  formatAgreementPercent,
  formatCalibrated,
  formatScore,
  formatUncertainty,
  formatUsd,
  formatUtc,
  shortId,
} from "../lib/format";
import { ACTIVE_POLL_STATUSES, type CaseDetailResponse, type StageRun } from "../lib/types";

/* ------------------------------------------------------------------ */
/* Sub-components                                                      */
/* ------------------------------------------------------------------ */

// Round 4: the timeline mirrors the pipeline's real STAGES exactly — the
// removed phase-2 stages (xenoscience, story_assets) must not render as
// phantom rows.
const STAGE_ORDER = [
  "intake",
  "quality_gate",
  "lineage_analysis",
  "rigor_adjudication",
  "battery",
  "coverage_check",
  "verdict",
  "uncertainty",
  "report",
  "fiction",
];

function StageTimeline({ stages }: { stages: StageRun[] }) {
  const byName = new Map(stages.map((s) => [s.stage_name, s]));
  return (
    <ol className="relative flex flex-col gap-0 pl-5">
      {/* The interlocking path: one continuous rule behind the nodes. */}
      <span
        aria-hidden="true"
        className="absolute bottom-2 left-1.5 top-2 w-0.5 rounded bg-line-2"
      />
      {STAGE_ORDER.map((name, index) => {
        const stage = byName.get(name);
        const status = stage?.status ?? "pending";
        const isJunction = index === STAGE_ORDER.length - 1;
        const dotClass =
          status === "completed"
            ? isJunction
              ? "bg-accent"
              : "bg-st-ok"
            : status === "running"
              ? "bg-st-run"
              : status === "failed"
                ? "bg-st-bad"
                : "bg-line-3";
        return (
          <li key={name} className="relative flex items-start gap-3 py-1.5">
            <span
              aria-hidden="true"
              className={`absolute -left-5 top-3 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--bg-base)] ${dotClass}`}
            />
            <span className="w-28 shrink-0 font-mono text-xs text-ink-2">
              {name}
            </span>
            <span
              className={`text-xs ${
                status === "completed"
                  ? "text-ink"
                  : status === "running"
                    ? "text-st-run"
                    : status === "failed"
                      ? "text-st-bad"
                      : "text-ink-3"
              }`}
            >
              {status}
              {stage?.completed_at ? (
                <span className="ml-2 text-ink-3 tnum">
                  {formatUtc(stage.completed_at)}
                </span>
              ) : null}
              {stage?.error_detail ? (
                <span className="ml-2 text-st-bad">{stage.error_detail}</span>
              ) : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { postForm, onExpired, mode } = useAuth();
  const [dialog, setDialog] = useState<
    "retry" | "rerun" | "payment" | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [copiedId, setCopiedId] = useState(false);
  // Spec §9.4 progressive rows: 20 lineage + 10 warnings + 10 audit
  // initially, expandable from the initial server response.
  const [allLineage, setAllLineage] = useState(false);
  const [allWarnings, setAllWarnings] = useState(false);
  const [auditShown, setAuditShown] = useState(10);
  // Spec §9.4: payment select + explicit Update button (no mutate-on-change).
  const [paymentChoice, setPaymentChoice] = useState<string | null>(null);

  const polling = usePolling<CaseDetailResponse>({
    queryKey: caseDetailKey(caseId),
    queryFn: () => fetchCaseDetail(caseId),
    bands: CASE_DETAIL_BANDS,
    stopAfter: CASE_DETAIL_STOP_MS,
  });
  const { data, error, isLoading, isStopped, resume } = polling;

  const caseRow = data?.case;
  const isActive = caseRow ? ACTIVE_POLL_STATUSES.has(caseRow.status) : false;
  // The per-case estimate is an unconditional float from the backend
  // (types.ts CaseDetail.estimated_cost_usd) — no unavailable branch
  // exists for it. The "Cost estimate unavailable" seam (D-41) belongs
  // to the benchmark dialog, whose estimate genuinely can be null.
  const costText = caseRow
    ? `Maximum cost: ${formatUsd(caseRow.estimated_cost_usd)}.`
    : undefined;

  const runAction = async (
    path: string,
    fields: Record<string, string>,
    successMessage: string,
  ) => {
    setBusy(true);
    try {
      await postForm(path, fields);
      setDialog(null);
      toast(successMessage);
      resume();
      void queryClient.invalidateQueries({ queryKey: caseDetailKey(caseId) });
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        setDialog(null);
        onExpired();
        return;
      }
      if (err instanceof ConflictError) {
        setDialog(null);
        toast(err.message);
        return;
      }
      if (err instanceof ApiError) toast(err.message);
      else toast("Action failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  if (error instanceof NotFoundError) {
    return (
      <EmptyState
        title="Case not found."
        hint="It may have been deleted, or the URL is wrong."
        action={
          <Button variant="secondary" onClick={() => navigate("/ui/cases")}>
            Back to cases
          </Button>
        }
      />
    );
  }
  if (error && !data) {
    return (
      <ErrorCard
        message="Cannot load this case."
        hint={error.message}
        action={
          <Button variant="secondary" onClick={() => window.location.reload()}>
            Retry
          </Button>
        }
      />
    );
  }
  if (isLoading || !caseRow) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-5 w-40 rounded-full" />
        <Skeleton className="h-40 w-full rounded-lg" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  const reportReady = data.downloads.report_available;
  const fictionReady = data.downloads.fiction_available;
  const fictionWithheld = data.downloads.fiction_withheld;

  return (
    <div className="flex flex-col gap-6">
      {/* Poster-like header: one oversized signal (status + verdict). */}
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-5">
        <div className="flex flex-col gap-2">
          <p className="flex items-center gap-1.5 font-mono text-xs text-ink-3">
            {shortId(caseRow.case_id)} · created {formatUtc(caseRow.created_at)}
            {/* Spec §9.4: case ID is copyable. */}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 px-0 text-ink-3"
              aria-label={
                copiedId
                  ? "Case id copied"
                  : `Copy case id ${caseRow.case_id}`
              }
              onClick={async () => {
                const ok = await copyText(caseRow.case_id);
                setCopiedId(ok);
                window.setTimeout(() => setCopiedId(false), 2000);
              }}
            >
              {copiedId ? (
                <Check aria-hidden="true" className="h-3.5 w-3.5" />
              ) : (
                <Copy aria-hidden="true" className="h-3.5 w-3.5" />
              )}
            </Button>
          </p>
          <h1 className="font-display text-2xl font-semibold text-ink">
            Case {shortId(caseRow.case_id)}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={caseRow.status} />
            <VerdictBadge verdict={caseRow.verdict} />
            <PaymentBadge status={caseRow.payment_status} />
            <ModeBadge mode={mode} />
            {caseRow.retention_exceeded && (
              <span className="inline-flex h-5 items-center rounded-full bg-st-run-tint px-2 text-xs font-medium text-st-run">
                Retention exceeded
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-2">
          {caseRow.status === "failed" && (
            <Button onClick={() => setDialog("retry")}>Retry case</Button>
          )}
          {caseRow.status === "spend_capped" && (
            <Button onClick={() => setDialog("retry")}>Queue retry</Button>
          )}
          {caseRow.status === "verdict_ready" && (
            <Button
              onClick={() => {
                if (caseRow.payment_status === "unpaid") setDialog("payment");
                else navigate(`/ui/cases/${caseRow.case_id}/fiction`);
              }}
            >
              Approve &amp; fiction
            </Button>
          )}
          {caseRow.status === "complete" && (
            <Button onClick={() => setDialog("rerun")} variant="secondary">
              Request rerun
            </Button>
          )}
          {(caseRow.status === "queued" || caseRow.status === "analyzing" ||
            caseRow.status === "rerun_requested") && (
            <p className="text-xs text-st-run" role="status">
              Pipeline running — polling live.
            </p>
          )}
        </div>
      </header>

      {isStopped && isActive && (
        <p className="text-xs text-ink-3" role="status">
          Analysis is taking longer than expected.{" "}
          <button
            type="button"
            onClick={resume}
            className="underline decoration-line-3 underline-offset-2 hover:text-ink"
          >
            Resume polling
          </button>
        </p>
      )}

      {caseRow.status === "failed" && caseRow.error_detail && (
        <ErrorCard message="Pipeline failed." hint={caseRow.error_detail} />
      )}
      {caseRow.status === "spend_capped" && (
        <Card className="p-4">
          <p className="text-sm text-ink-2">
            This case is waiting on budget. It will run automatically when the
            monthly spend cap frees.
          </p>
        </Card>
      )}

      {data.warnings.length > 0 && (
        <Card className="border-st-run p-4">
          <SectionLabel>Intake warnings</SectionLabel>
          <ul className="mt-2 flex flex-col gap-1">
            {(allWarnings
              ? data.warnings.slice(0, 100)
              : data.warnings.slice(0, 10)
            ).map((warning, index) => (
              <li key={index} className="text-xs text-st-run">
                {warning.message}
              </li>
            ))}
          </ul>
          {!allWarnings && data.warnings.length > 10 && (
            <button
              type="button"
              onClick={() => setAllWarnings(true)}
              className="relative mt-2 inline-flex min-h-8 items-center rounded-md px-2 py-1 text-xs font-medium text-ink-2 hover:bg-hovered hover:text-ink hit-6"
            >
              Show all ({Math.min(data.warnings.length, 100)})
            </button>
          )}
        </Card>
      )}

      {/* Case information */}
      <Card className="p-5">
        <SectionLabel>Case information</SectionLabel>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
          <div>
            <dt className="text-ink-3">Observed at</dt>
            <dd className="mt-0.5 text-ink-2 tnum">{formatUtc(caseRow.observed_at)}</dd>
          </div>
          <div>
            <dt className="text-ink-3">Coordinates</dt>
            <dd className="mt-0.5 font-mono text-ink-2">
              {caseRow.latitude.toFixed(4)}, {caseRow.longitude.toFixed(4)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Location</dt>
            <dd className="mt-0.5 text-ink-2">{caseRow.location_text ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-ink-3">Quality score</dt>
            <dd className="mt-0.5 text-ink-2 tnum">{formatScore(caseRow.quality_score)}</dd>
          </div>
          <div>
            <dt className="text-ink-3">Lineage concordance</dt>
            <dd className="mt-0.5 text-ink-2 tnum">
              {/* FR-005: the plurality fraction is only defined once 2+
                  engine lineages reported. With 0/1 lineages the stored
                  0.0 is a sentinel, not measured disagreement, and a
                  skipped analysis has no fraction at all. */}
              {caseRow.agreement_fraction === null ||
              (caseRow.lineage_count ?? data.lineage_outputs.length) < 2
                ? "Not comparable"
                : formatAgreementPercent(caseRow.agreement_fraction) ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Buyer ref</dt>
            <dd className="mt-0.5 text-ink-2">{caseRow.buyer_ref ?? "—"}</dd>
          </div>
        </dl>
      </Card>

      {/* Pipeline stages */}
      <Card className="p-5">
        <SectionLabel>Pipeline stages</SectionLabel>
        <div className="mt-3">
          <StageTimeline stages={data.stages} />
        </div>
      </Card>

      <Card className="overflow-hidden p-0">
        <div className="border-b border-line bg-inset px-5 py-4">
          <SectionLabel>Analysis contract</SectionLabel>
          <p className="mt-1 text-sm text-ink-2">
            Four mundane-explanation battery categories plus vision lineages
            driven by the Sneferu engine; concordance is the plurality
            fraction of lineage classifications. Case-scoped OS sandboxing
            is deferred to phase 2.
          </p>
        </div>
        <dl className="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
          <div className="bg-raised p-4">
            <dt className="text-xs text-ink-3">Runtime</dt>
            <dd className="mt-1 font-mono text-sm text-ink">
              {data.analysis_contract.lineage_runtime}
            </dd>
          </div>
          <div className="bg-raised p-4">
            <dt className="text-xs text-ink-3">OS sandbox</dt>
            <dd className={`mt-1 text-sm font-semibold ${data.analysis_contract.sandbox_verified ? "text-st-ok" : "text-st-run"}`}>
              {data.analysis_contract.sandbox_verified ? "Verified" : "Not verified (phase 2)"}
            </dd>
          </div>
          <div className="bg-raised p-4">
            <dt className="text-xs text-ink-3">Rigor gate</dt>
            <dd className={`mt-1 text-sm font-semibold ${data.analysis_contract.rigor?.passed ? "text-st-ok" : "text-st-run"}`}>
              {data.analysis_contract.rigor
                ? data.analysis_contract.rigor.passed ? "Passed" : "Not passed"
                : "Not run"}
            </dd>
          </div>
          <div className="bg-raised p-4">
            <dt className="text-xs text-ink-3">Battery coverage</dt>
            <dd className={`mt-1 text-sm font-semibold ${data.analysis_contract.coverage?.complete ? "text-st-ok" : "text-st-run"}`}>
              {data.analysis_contract.coverage?.complete ? "Complete" : "Qualified"}
            </dd>
          </div>
        </dl>
        {data.analysis_contract.coverage?.insufficient_categories.length ? (
          <p className="px-5 py-3 text-xs text-ink-3">
            Insufficient inputs: {data.analysis_contract.coverage.insufficient_categories.join(", ")}.
          </p>
        ) : null}
      </Card>

      {/* EVIDENCE — above the divider */}
      <Card className="p-5">
        <SectionLabel>Battery results</SectionLabel>
        {data.battery_results.length === 0 ? (
          <p className="mt-3 text-xs text-ink-3">Battery not run yet.</p>
        ) : (
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
                    <td className="py-2 pr-4">
                      <span
                        className={
                          row.result === "positive"
                            ? "text-st-ok"
                            : row.result === "negative"
                              ? "text-ink-2"
                              : "text-st-run"
                        }
                      >
                        {row.result}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-ink-3">
                      {row.evidence_citation ?? "—"}
                    </td>
                    <td className="py-2 pr-2">
                      <SourceStampDisplay
                        stamp={row.source_stamp_json}
                        caseId={caseRow.case_id}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="p-5">
        <SectionLabel>Lineage outputs</SectionLabel>
        {data.lineage_outputs.length === 0 ? (
          <p className="mt-3 text-xs text-ink-3">No lineage outputs recorded.</p>
        ) : (
          <>
            <div className="mt-3 overflow-x-auto lg:overflow-visible">
              <table className="w-full border-collapse text-xs lg:table-min-sm">
                <thead>
                  <tr className="border-b border-line text-left uppercase text-ink-3" style={{ letterSpacing: "var(--tracking-caps)" }}>
                    <th scope="col" className="py-2 pr-4 font-medium">Lineage</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Classification</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Artifact detected</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Status</th>
                    <th scope="col" className="py-2 font-medium">Confidence / reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(allLineage
                    ? data.lineage_outputs.slice(0, 100)
                    : data.lineage_outputs.slice(0, 20)
                  ).map((row) => (
                    <tr key={row.lineage_id} className="border-b border-line last:border-b-0">
                      <td className="py-2 pr-4 font-mono text-ink-2">{row.lineage_id}</td>
                      <td className="py-2 pr-4 text-ink-2">{row.classification ?? "—"}</td>
                      <td className="py-2 pr-4 text-ink-2">
                        {row.artifact_detected === null
                          ? "—"
                          : row.artifact_detected
                            ? "Yes"
                            : "No"}
                      </td>
                      <td className="py-2 pr-4 text-ink-2">{row.status}</td>
                      <td className="py-2 text-ink-3">
                        {row.confidence !== null
                          ? row.confidence.toFixed(2)
                          : row.abstention_reason ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!allLineage && data.lineage_outputs.length > 20 && (
              <button
                type="button"
                onClick={() => setAllLineage(true)}
                className="relative mt-3 inline-flex min-h-8 items-center rounded-md px-2 py-1 text-xs font-medium text-ink-2 hover:bg-hovered hover:text-ink hit-6"
              >
                Show all ({Math.min(data.lineage_outputs.length, 100)})
              </button>
            )}
            {data.total_lineage_outputs > 100 && (
              <div className="mt-3">
                <CliHint
                  command={`uapvf case lineages ${caseRow.case_id}`}
                  note={`Showing the first 100 of ${data.total_lineage_outputs} lineage outputs.`}
                />
              </div>
            )}
          </>
        )}
      </Card>

      <EvidenceDivider />

      {/* INTERPRETATION — below the divider */}
      <Card className="p-5">
        <SectionLabel>Verdict text</SectionLabel>
        {/* UI-28 (spec §9.4 / FR-005): low lineage agreement wears a
            caution banner — factual, same voice, never alarmist. The
            fraction is only meaningful once 2+ engine lineages reported
            (the stored 0.0 sentinel for 0/1 lineages never triggers it). */}
        {caseRow.agreement_fraction !== null &&
          caseRow.agreement_fraction < 0.6 &&
          (caseRow.lineage_count ?? data.lineage_outputs.length) >= 2 && (
            <div
              role="note"
              className="mt-3 flex items-start gap-3 rounded-md border border-st-run bg-st-run-tint px-4 py-3"
            >
              <p className="text-xs text-st-run">
                Vision lineages showed low agreement (
                {formatAgreementPercent(caseRow.agreement_fraction)}).
                Findings should be treated with caution.
              </p>
            </div>
          )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {/* UI-50 (spec §6.3): quality gate state, identical pill weight. */}
          <QualityGateBadge pass={caseRow.quality_gate_pass} />
        </div>
        <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-ink-2">
          {caseRow.verdict_text ?? "No verdict text yet."}
        </p>
        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-line pt-4 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-ink-3">Uncertainty</dt>
            <dd className="mt-0.5 text-ink-2 tnum">
              {formatUncertainty(caseRow.uncertainty)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Calibrated</dt>
            <dd className="mt-0.5 text-ink-2">
              {formatCalibrated(caseRow.uncertainty_calibrated)}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Reason</dt>
            <dd className="mt-0.5 text-ink-2">
              {caseRow.uncertainty_reason ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-ink-3">Calibration source</dt>
            <dd className="mt-0.5 text-ink-2">
              {caseRow.calibration_source ?? "—"}
            </dd>
          </div>
        </dl>
      </Card>

      {/* Deliverables */}
      {(caseRow.status === "verdict_ready" || caseRow.status === "complete") && (
        <Card className="p-5">
          <SectionLabel>Deliverables</SectionLabel>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              disabled={!reportReady}
              onClick={() => navigate(`/ui/cases/${caseRow.case_id}/report`)}
            >
              Report
            </Button>
            {reportReady ? (
              /* UI-30 (spec §9.4): direct anchor — browser-default Accept
                 gets the server's HTML with Content-Disposition. */
              <a
                href={`/cases/${caseRow.case_id}/report`}
                download
                className="relative inline-flex h-9 items-center justify-center gap-2 rounded-md border border-line-2 bg-transparent px-4 text-sm font-medium text-ink transition-colors duration-[var(--dur-fast)] hover:bg-hovered active:translate-y-px hit-4"
              >
                Report HTML
              </a>
            ) : (
              <Button variant="secondary" disabled>
                Report HTML
              </Button>
            )}
            <Button
              variant="secondary"
              disabled={!reportReady}
              onClick={async () => {
                try {
                  const raw = await jsonGet<unknown>(
                    `/cases/${caseRow.case_id}/report`,
                  );
                  downloadBlob(
                    JSON.stringify(raw, null, 2),
                    `report_${caseRow.case_id}.json`,
                    "application/json",
                  );
                } catch (err) {
                  toast(err instanceof ApiError ? err.message : "Download failed.");
                }
              }}
            >
              Report JSON
            </Button>
            <Button
              variant="secondary"
              disabled={!fictionReady && !fictionWithheld}
              onClick={() => navigate(`/ui/cases/${caseRow.case_id}/fiction`)}
            >
              Fiction seed
            </Button>
          </div>
          {/* Disabled/state reasons as visible text (spec §11 — never
              tooltip-only). A withheld seed keeps its button enabled
              (the fiction page names what happened); the line below
              states the terminal fact beside it. */}
          {(!reportReady || !fictionReady) && (
            <p className="mt-3 text-xs text-ink-3">
              {!reportReady && "Report not generated yet."}
              {!reportReady && !fictionReady && " "}
              {!fictionReady &&
                (fictionWithheld
                  ? "Fiction seed withheld after label validation failure."
                  : caseRow.verdict === "mundane_identified"
                    ? "Fiction seed: not generated for mundane_identified."
                    : "Fiction not generated yet.")}
            </p>
          )}
        </Card>
      )}

      {caseRow.status === "complete" && (
        <Card className="border-accent p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <SectionLabel>Buyer delivery</SectionLabel>
              <p className="mt-2 max-w-2xl text-sm text-ink-2">
                The MVP has no buyer-facing delivery surface (spec §1 OUT;
                deferred to phase 2). Export the report below and deliver it
                to the buyer manually.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Audit trail */}
      <Card className="p-5">
        <SectionLabel>
          Audit trail ({data.total_audit_events} event
          {data.total_audit_events === 1 ? "" : "s"})
        </SectionLabel>
        {data.audit_events.length === 0 ? (
          <p className="mt-3 text-xs text-ink-3">No audit events recorded for this case yet.</p>
        ) : (
          <ol className="mt-3 flex flex-col gap-2">
            {data.audit_events.slice(0, auditShown).map((event) => (
              <li key={event.entry_hash} className="text-xs">
                <span className="font-mono text-ink-3 tnum">
                  {formatUtc(event.recorded_at)}
                </span>{" "}
                <span className="text-ink-2">{event.actor}</span>{" "}
                <span className="font-mono text-st-verdict">{event.action}</span>
                <span className="ml-2 font-mono text-ink-3">
                  {event.entry_hash.slice(0, 12)}
                </span>
              </li>
            ))}
          </ol>
        )}
        {auditShown < Math.min(data.audit_events.length, 100) && (
          <button
            type="button"
            onClick={() => setAuditShown((shown) => shown + 10)}
            className="relative mt-3 inline-flex min-h-8 items-center rounded-md px-2 py-1 text-xs font-medium text-ink-2 hover:bg-hovered hover:text-ink hit-6"
          >
            Show 10 more
          </button>
        )}
        <div className="mt-4 flex flex-col gap-3">
          <CliHint
            command={`uapvf case export ${caseRow.case_id}`}
            note="Export this case: media, artifacts, and the audit chain."
          />
          <CliHint
            command="uapvf audit verify"
            note="Recompute the hash chain locally — the console does not verify it."
          />
        </div>
      </Card>

      {/* Danger zone */}
      <Card className="border-st-bad p-5">
        <SectionLabel>Danger</SectionLabel>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-ink-3">
            Deleting removes the media, every artifact, and the audit rows for
            this case. The audit chain itself is append-only and is never
            rewritten.
          </p>
          <div className="flex flex-col items-end gap-1.5">
            <Button
              variant="danger"
              disabled={caseRow.status === "analyzing"}
              onClick={() => navigate(`/ui/cases/${caseRow.case_id}/delete/confirm`)}
            >
              Delete case
            </Button>
            {caseRow.status === "analyzing" && (
              <p className="text-xs text-ink-3">
                Wait for the run to finish before deleting.
              </p>
            )}
          </div>
        </div>
      </Card>

      {/* Cost-gated dialogs (spec §8.8) */}
      <ConfirmDialog
        open={dialog === "retry"}
        title="Retry this case?"
        description="The failed pipeline stages will run again from the stored media."
        costText={costText}
        confirmLabel="Retry case"
        busy={busy}
        busyText="Retrying…"
        onConfirm={() =>
          runAction(
            `/cases/${caseRow.case_id}/retry`,
            {},
            "Retry queued.",
          )
        }
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "rerun"}
        title="Request a full rerun?"
        description="All stages run again on the stored media. Existing artifacts stay downloadable until the new report lands."
        costText={costText}
        confirmLabel="Request rerun"
        busy={busy}
        busyText="Requesting…"
        onConfirm={() =>
          runAction(
            `/cases/${caseRow.case_id}/rerun`,
            {},
            "Rerun requested.",
          )
        }
        onCancel={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "payment"}
        title="Approve and mark paid?"
        description="Marks this case paid, then opens the fiction seed page."
        costText={`This case cost ${formatUsd(caseRow.estimated_cost_usd)} (estimate).`}
        confirmLabel="Approve & fiction"
        busy={busy}
        busyText="Approving…"
        onConfirm={async () => {
          setBusy(true);
          try {
            await postForm(`/cases/${caseRow.case_id}/payment`, {
              payment_status: "paid",
            });
            setDialog(null);
            navigate(`/ui/cases/${caseRow.case_id}/fiction`);
          } catch (err) {
            if (err instanceof AuthExpiredError) {
              setDialog(null);
              onExpired();
            } else if (err instanceof ApiError) toast(err.message);
            else toast("Approval failed. Try again.");
          } finally {
            setBusy(false);
          }
        }}
        onCancel={() => setDialog(null)}
      />

      {/* Secondary controls: payment select + explicit Update (spec §9.4 —
          no mutate-on-change, so keyboard arrow-through cannot fire
          repeated mutations). */}
      <Card className="p-5">
        <SectionLabel>Payment tracking</SectionLabel>
        <p className="mt-2 text-xs text-ink-3">
          Delivery and payment are the operator's responsibility. Current:{" "}
          <PaymentBadge status={caseRow.payment_status} />
        </p>
        <div className="mt-3 flex max-w-sm items-center gap-2">
          <label htmlFor="payment-select" className="sr-only">
            Set payment status
          </label>
          <select
            id="payment-select"
            value={paymentChoice ?? caseRow.payment_status}
            onChange={(event) => setPaymentChoice(event.target.value)}
            className="h-11 w-full rounded-md border border-line-2 bg-inset px-3 text-[var(--text-base-mobile)] text-ink sm:h-9 sm:text-sm"
          >
            <option value="unpaid">Unpaid</option>
            <option value="paid">Paid</option>
            <option value="comped">Comped</option>
          </select>
          <Button
            variant="secondary"
            disabled={paymentChoice === null || paymentChoice === caseRow.payment_status}
            loading={busy && dialog === null}
            loadingText="Updating…"
            onClick={() => {
              if (paymentChoice === null) return;
              void (async () => {
                setBusy(true);
                try {
                  await postForm(`/cases/${caseRow.case_id}/payment`, {
                    payment_status: paymentChoice,
                  });
                  setPaymentChoice(null);
                  toast("Payment status updated.");
                  void queryClient.invalidateQueries({
                    queryKey: caseDetailKey(caseId),
                  });
                } catch (err) {
                  if (err instanceof AuthExpiredError) onExpired();
                  else if (err instanceof ApiError) toast(err.message);
                  else toast("Payment update failed. Try again.");
                } finally {
                  setBusy(false);
                }
              })();
            }}
          >
            Update
          </Button>
        </div>
      </Card>

      {isActive && (
        <p className="text-xs text-ink-3">
          Polling: 5s → 10s → 30s bands while this tab is visible.
        </p>
      )}
    </div>
  );
}
