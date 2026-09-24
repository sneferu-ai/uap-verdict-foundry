// /ui/cases — spec §9.2. The operator's morning page: one table, one
// spend bar, one New Case action. Dense, factual, no KPI hero band.

import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Copy,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import {
  Button,
  Card,
  EmptyState,
  ErrorCard,
  PaymentBadge,
  SectionLabel,
  SkeletonRows,
  StatusBadge,
  VerdictBadge,
} from "../components/ui";
import { fetchCaseList } from "../lib/queries";
import { usePolling } from "../lib/use-polling";
import { copyText, formatScore, formatUtc, formatUsd } from "../lib/format";
import type { CaseSummary, ReadinessResponse, SpendSummary } from "../lib/types";
import { jsonGet } from "../lib/api";

const PAGE_SIZE = 20;

const STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "queued", label: "Queued" },
  { value: "analyzing", label: "Analyzing" },
  { value: "verdict_ready", label: "Verdict ready" },
  { value: "complete", label: "Complete" },
  { value: "failed", label: "Failed" },
  { value: "spend_capped", label: "Spend capped" },
  { value: "rerun_requested", label: "Rerun requested" },
];

function useReadiness() {
  return useQuery<ReadinessResponse>({
    queryKey: ["readyz"],
    queryFn: () => jsonGet<ReadinessResponse>("/readyz"),
    refetchInterval: 30_000,
    retry: 1,
  });
}

function ActionMenu({ row }: { row: CaseSummary }) {
  // Action visibility per spec §9.2: Retry only for failed; Rerun only for
  // complete; rerun_requested shows a disabled Retry with its reason as
  // visible text (spec §11: disabled reasons are never tooltip-only);
  // Delete is always offered — the delete-confirm page is the gate that
  // refuses an in-flight case (§9.5), not the list row.
  const showRetry = row.status === "failed";
  const showRerun = row.status === "complete";
  const rerunInProgress = row.status === "rerun_requested";
  const linkClass =
    "relative inline-flex min-h-8 items-center rounded-md px-2 py-1 text-xs font-medium text-ink-2 hover:bg-hovered hover:text-ink hit-6";
  const deleteClass =
    "relative inline-flex min-h-8 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-st-bad hover:bg-hovered hit-6";
  return (
    <div className="flex items-center justify-end gap-1">
      <Link to={`/ui/cases/${row.case_id}`} className={linkClass}>
        View
      </Link>
      {showRetry && (
        <Link to={`/ui/cases/${row.case_id}`} className={linkClass}>
          Retry
        </Link>
      )}
      {rerunInProgress && (
        <span
          aria-disabled="true"
          className="inline-flex min-h-8 cursor-not-allowed items-center rounded-md px-2 py-1 text-xs font-medium text-ink-disabled"
        >
          Retry — rerun in progress
        </span>
      )}
      {showRerun && (
        <Link to={`/ui/cases/${row.case_id}`} className={linkClass}>
          Rerun
        </Link>
      )}
      <Link to={`/ui/cases/${row.case_id}/delete/confirm`} className={deleteClass}>
        <Trash2 aria-hidden="true" className="h-3 w-3" />
        Delete
      </Link>
    </div>
  );
}

function CaseCard({ row }: { row: CaseSummary }) {
  return (
    <div className="px-4 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Link
            to={`/ui/cases/${row.case_id}`}
            className="font-mono text-xs text-ink-2 underline-offset-2 hover:text-accent hover:underline"
          >
            {row.case_id.slice(0, 8)}
          </Link>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 px-0 text-ink-3"
            aria-label={`Copy case id ${row.case_id}`}
            onClick={() => void copyText(row.case_id)}
          >
            <Copy aria-hidden="true" className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <StatusBadge status={row.status} />
          {row.verdict && <VerdictBadge verdict={row.verdict} />}
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
        <div>
          <span className="text-ink-3">Created</span>
          <p className="mt-0.5 text-ink-2 tnum">{formatUtc(row.created_at)}</p>
        </div>
        <div>
          <span className="text-ink-3">Payment</span>
          <p className="mt-0.5">
            <PaymentBadge status={row.payment_status} />
          </p>
        </div>
        <div>
          <span className="text-ink-3">Buyer ref</span>
          <p className="mt-0.5 text-ink-2">{row.buyer_ref ?? "—"}</p>
        </div>
        <div>
          <span className="text-ink-3">Quality</span>
          <p className="mt-0.5 text-ink-2 tnum">{formatScore(row.quality_score)}</p>
        </div>
      </div>
      {row.retention_exceeded && (
        <div className="mt-3 flex items-center gap-2 text-xs text-st-cap">
          <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
          <span>Retention exceeded</span>
        </div>
      )}
      <div className="mt-3 flex items-center justify-end">
        <ActionMenu row={row} />
      </div>
    </div>
  );
}

function SpendPanel({ spend }: { spend: SpendSummary }) {
  const [open, setOpen] = useState(true);
  const cap = spend.spend_cap_usd;
  const ratio = cap > 0 ? Math.min(1, spend.total_usd / cap) : 0;
  const overEighty = cap > 0 && spend.total_usd / cap > 0.8;
  const capped = cap > 0 && spend.total_usd >= cap;
  const barTone = capped
    ? "bg-st-bad"
    : overEighty
      ? "bg-st-cap"
      : "bg-accent";
  return (
    <Card className="p-4">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="spend-detail"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 text-left"
      >
        {open ? (
          <ChevronDown aria-hidden="true" className="h-4 w-4 text-ink-3" />
        ) : (
          <ChevronRight aria-hidden="true" className="h-4 w-4 text-ink-3" />
        )}
        <span className="text-sm font-medium text-ink">Monthly spend</span>
        <span className="ml-auto text-sm text-ink-2 tnum">
          {formatUsd(spend.total_usd)} / {formatUsd(cap)}
        </span>
      </button>
      <div
        className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-inset"
        role="progressbar"
        aria-label="Monthly spend against cap"
        aria-valuenow={Math.round(ratio * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`h-full w-full origin-left rounded-full transition-transform duration-[var(--dur-base)] ${barTone}`}
          style={{ transform: `scaleX(${ratio})` }}
        />
      </div>
      <p className={`mt-2 text-xs ${capped ? "text-st-bad" : overEighty ? "text-st-cap" : "text-ink-3"}`}>
        {capped
          ? "Spend cap reached — new cases will queue until budget frees."
          : overEighty
            ? "Approaching the monthly spend cap."
            : `${formatUsd(spend.remaining_usd)} remaining this month (${spend.month}).`}
      </p>
      {/* Kept mounted (hidden when collapsed) so the toggle's
          aria-controls always references a real element. */}
      <dl
        id="spend-detail"
        className={
          open
            ? "mt-4 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-line pt-4 text-xs sm:grid-cols-4"
            : "hidden"
        }
      >
        <div>
          <dt className="text-ink-3">Lineage calls</dt>
          <dd className="mt-0.5 text-ink-2 tnum">{spend.lineage_calls}</dd>
        </div>
        <div>
          <dt className="text-ink-3">Fiction calls</dt>
          <dd className="mt-0.5 text-ink-2 tnum">{spend.fiction_calls}</dd>
        </div>
        <div>
          <dt className="text-ink-3">Lineage cost</dt>
          <dd className="mt-0.5 text-ink-2 tnum">
            {formatUsd(spend.lineage_cost_usd)}
          </dd>
        </div>
        <div>
          <dt className="text-ink-3">Fiction cost</dt>
          <dd className="mt-0.5 text-ink-2 tnum">
            {formatUsd(spend.fiction_cost_usd)}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

export function CasesDashboardPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get("status") ?? "";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1"));
  const offset = (page - 1) * PAGE_SIZE;

  const { data, error, isLoading, isStopped, resume } = usePolling({
    queryKey: ["cases", { status: statusFilter, offset, limit: PAGE_SIZE }],
    queryFn: () =>
      fetchCaseList({ status: statusFilter || undefined, offset, limit: PAGE_SIZE }),
    bands: [
      { maxElapsed: 60_000, interval: 15_000 },
      { maxElapsed: 300_000, interval: 30_000 },
    ],
    stopAfter: 600_000,
  });

  const readiness = useReadiness();

  const setPage = (next: number) => {
    const params = new URLSearchParams(searchParams);
    if (next <= 1) params.delete("page");
    else params.set("page", String(next));
    setSearchParams(params);
  };

  const setFilter = (value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value) params.set("status", value);
    else params.delete("status");
    params.delete("page");
    setSearchParams(params);
  };

  const readyState = useMemo(() => {
    if (readiness.isError) return { tone: "text-st-run", label: "readiness unknown" };
    const ready = readiness.data?.ready;
    if (ready === undefined) return { tone: "text-ink-3", label: "checking readiness…" };
    return ready
      ? { tone: "text-st-ok", label: "ready" }
      : { tone: "text-st-run", label: "not ready" };
  }, [readiness]);

  const cases = data?.cases ?? [];
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-ink">Cases</h1>
          <p className={`mt-1 text-xs ${readyState.tone}`} role="status">
            {readyState.label}
            {readiness.data?.mode ? ` · ${readiness.data.mode} mode` : ""}
          </p>
        </div>
        <Button onClick={() => navigate("/ui/cases/new")}>
          <Plus aria-hidden="true" className="h-4 w-4" />
          New Case
        </Button>
      </header>

      {error && !data && (
        <ErrorCard
          message="Cannot load cases."
          hint={error.message}
          action={
            <Button variant="secondary" onClick={() => window.location.reload()}>
              <RotateCcw aria-hidden="true" className="h-4 w-4" />
              Retry
            </Button>
          }
        />
      )}

      {data && <SpendPanel spend={data.spend} />}

      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SectionLabel>
            {isLoading ? "Loading cases…" : `${total} case${total === 1 ? "" : "s"}`}
          </SectionLabel>
          <label htmlFor="case-status-filter" className="sr-only">
            Filter by status
          </label>
          <select
            id="case-status-filter"
            value={statusFilter}
            onChange={(event) => setFilter(event.target.value)}
            className="h-11 rounded-md border border-line-2 bg-inset px-2 text-[var(--text-base-mobile)] text-ink sm:h-8 sm:text-xs"
          >
            {STATUS_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {isStopped && (
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

        <Card className="overflow-x-auto lg:overflow-visible">
          {isLoading && (
            <div className="p-6">
              <SkeletonRows rows={6} />
            </div>
          )}
          {!isLoading && cases.length === 0 && (
            <div className="p-6">
              <EmptyState
                title={
                  statusFilter
                    ? `No cases with status "${statusFilter}".`
                    : "No cases yet."
                }
                hint={
                  statusFilter
                    ? "Clear the filter to see every case."
                    : "Submit the first capture to start a pipeline."
                }
                action={
                  statusFilter ? (
                    <Button variant="secondary" onClick={() => setFilter("")}>
                      Clear filter
                    </Button>
                  ) : (
                    <Button onClick={() => navigate("/ui/cases/new")}>
                      New Case
                    </Button>
                  )
                }
              />
            </div>
          )}
          {!isLoading && cases.length > 0 && (
            <>
              <table className="hidden w-full border-collapse text-sm lg:table">
                <caption className="sr-only">
                  Submitted cases with status, verdict, payment, and actions
                </caption>
                <thead>
                  <tr className="border-b border-line text-left text-xs uppercase text-ink-3" style={{ letterSpacing: "var(--tracking-caps)" }}>
                    <th scope="col" className="px-4 py-2.5 font-medium">Case</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Created (UTC)</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Verdict</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Buyer Ref</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Payment</th>
                    <th scope="col" className="px-4 py-2.5 text-right font-medium">Quality</th>
                    <th scope="col" className="px-4 py-2.5 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map((row) => (
                    <tr
                      key={row.case_id}
                      className="h-14 border-b border-line transition-colors duration-[var(--dur-fast)] last:border-b-0 hover:bg-hovered"
                    >
                      <td className="px-4 font-mono text-xs">
                        <div className="flex items-center gap-2">
                          <Link
                            to={`/ui/cases/${row.case_id}`}
                            className="text-ink-2 underline-offset-2 hover:text-accent hover:underline"
                          >
                            {row.case_id.slice(0, 8)}
                          </Link>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 w-7 px-0 text-ink-3"
                            aria-label={`Copy case id ${row.case_id}`}
                            onClick={() => void copyText(row.case_id)}
                          >
                            <Copy aria-hidden="true" className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </td>
                      <td className="px-4 text-xs text-ink-2 tnum">
                        <div className="flex items-center gap-2">
                          {formatUtc(row.created_at)}
                          {row.retention_exceeded && (
                            <>
                              <AlertTriangle
                                aria-hidden="true"
                                className="h-3.5 w-3.5 text-st-cap"
                              />
                              <span className="sr-only">retention exceeded</span>
                            </>
                          )}
                        </div>
                      </td>
                      <td className="px-4">
                        <StatusBadge status={row.status} />
                      </td>
                      <td className="px-4">
                        <VerdictBadge verdict={row.verdict} />
                      </td>
                      <td className="px-4 text-xs text-ink-2">
                        {row.buyer_ref ?? "—"}
                      </td>
                      <td className="px-4">
                        <PaymentBadge status={row.payment_status} />
                      </td>
                      <td className="px-4 text-right text-xs text-ink-2 tnum">
                        {formatScore(row.quality_score)}
                      </td>
                      <td className="px-4">
                        <div className="flex items-center justify-end gap-1">
                          <ActionMenu row={row} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="lg:hidden">
                {cases.map((row, index) => (
                  <div
                    key={row.case_id}
                    className={`${index > 0 ? "border-t border-line" : ""}`}
                  >
                    <CaseCard row={row} />
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>

        {!isLoading && total > 0 && (
          <div className="flex items-center justify-between text-xs text-ink-3">
            <p className="tnum">
              Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
                title={page <= 1 ? "Already on the first page" : undefined}
              >
                Previous
              </Button>
              <span className="tnum">
                {page} / {pageCount}
              </span>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= pageCount}
                onClick={() => setPage(page + 1)}
                title={page >= pageCount ? "No more pages" : undefined}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
