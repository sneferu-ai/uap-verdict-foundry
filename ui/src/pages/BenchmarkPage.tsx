// /ui/benchmark — spec §9.8. Preregistered thresholds, honest progress,
// cost-gated run button.

import { useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { Button, Card, CliHint, EmptyState, ErrorBanner, SectionLabel, Skeleton } from "../components/ui";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useAuth } from "../auth/AuthContext";
import { usePolling, BENCHMARK_BANDS, BENCHMARK_STOP_MS } from "../lib/use-polling";
import { benchmarkKey, fetchBenchmark } from "../lib/queries";
import { ApiError, AuthExpiredError, ConflictError } from "../lib/api";
import { copyText, formatPercent1, formatUsd, formatUtc } from "../lib/format";

export function BenchmarkPage() {
  const { postForm, onExpired } = useAuth();
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [copiedHash, setCopiedHash] = useState(false);

  const { data, error, isLoading, isStopped, resume } = usePolling({
    queryKey: benchmarkKey,
    queryFn: fetchBenchmark,
    bands: BENCHMARK_BANDS,
    stopAfter: BENCHMARK_STOP_MS,
  });

  const running = Boolean(data?.progress && !data.progress.done);

  const startRun = async () => {
    setBusy(true);
    try {
      await postForm("/benchmark/run", {});
      setConfirmOpen(false);
      toast("Benchmark started.");
      resume();
      void queryClient.invalidateQueries({ queryKey: benchmarkKey });
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        setConfirmOpen(false);
        onExpired();
        return;
      }
      if (err instanceof ConflictError) {
        setConfirmOpen(false);
        toast(err.message);
        return;
      }
      if (err instanceof ApiError) toast(err.message);
      else toast("Could not start the benchmark.");
    } finally {
      setBusy(false);
    }
  };

  const costEstimate = data?.estimated_benchmark_cost_usd ?? null;

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-ink">
            Benchmark
          </h1>
          <p className="mt-1 text-xs text-ink-3">
            Measures recall against the preregistered seed set. Costs real
            money in live mode.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <Button
            onClick={() => setConfirmOpen(true)}
            disabled={running}
          >
            Run benchmark
          </Button>
          {running && (
            <p className="text-xs text-ink-3">
              A benchmark run is already in progress.
            </p>
          )}
        </div>
      </header>

      {isStopped && running && (
        <p className="text-xs text-ink-3" role="status">
          Polling paused after 20 minutes.{" "}
          <button
            type="button"
            onClick={resume}
            className="underline decoration-line-3 underline-offset-2 hover:text-ink"
          >
            Resume polling
          </button>
        </p>
      )}

      {isLoading && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32 w-full rounded-lg" />
          <Skeleton className="h-40 w-full rounded-lg" />
        </div>
      )}

      {error && !data && (
        <ErrorBanner
          message="Benchmark status could not be loaded."
          hint={error.message}
        />
      )}

      {data && !isLoading && (
        <>
          {!data.latest_run && !data.progress?.done && !running && (
            <EmptyState
              title="No benchmark has been run."
              hint="Preregister thresholds from the CLI, then run it here."
            />
          )}

          {!data.prereg && (
            <Card className="p-5">
              <SectionLabel>Preregistration</SectionLabel>
              <p className="mt-2 text-xs text-ink-3">
                No preregistered thresholds found. The run needs them first.
              </p>
              <div className="mt-3">
                <CliHint command="uapvf benchmark prereg" />
              </div>
            </Card>
          )}

          {data.prereg && (
            <Card className="p-5">
              <SectionLabel>Preregistered thresholds</SectionLabel>
              <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
                <div>
                  <dt className="text-ink-3">Min per-category recall</dt>
                  <dd className="mt-0.5 text-ink-2 tnum">
                    {formatPercent1(data.prereg.min_per_category_recall)}
                  </dd>
                </div>
                <div>
                  <dt className="text-ink-3">Max false no-mundane-match</dt>
                  <dd className="mt-0.5 text-ink-2 tnum">
                    {formatPercent1(data.prereg.max_false_no_mundane_match_rate)}
                  </dd>
                </div>
                <div>
                  <dt className="text-ink-3">Min insufficient detection</dt>
                  <dd className="mt-0.5 text-ink-2 tnum">
                    {formatPercent1(data.prereg.min_insufficient_detection_rate)}
                  </dd>
                </div>
              </dl>
              {data.prereg.prereg_hash && (
                /* UI-56: prereg hash with copy. */
                <p className="mt-3 flex items-center gap-1.5 font-mono text-xs text-ink-3">
                  prereg hash {data.prereg.prereg_hash.slice(0, 16)}…
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 px-0 text-ink-3"
                    aria-label={
                      copiedHash
                        ? "Prereg hash copied"
                        : "Copy full prereg hash"
                    }
                    onClick={async () => {
                      const ok = await copyText(
                        data.prereg?.prereg_hash ?? "",
                      );
                      setCopiedHash(ok);
                      window.setTimeout(() => setCopiedHash(false), 2000);
                    }}
                  >
                    {copiedHash ? (
                      <Check aria-hidden="true" className="h-3.5 w-3.5" />
                    ) : (
                      <Copy aria-hidden="true" className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </p>
              )}
            </Card>
          )}

          {running && data.progress && (
            <Card className="p-5">
              <SectionLabel>Run in progress</SectionLabel>
              <div
                className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-inset"
                role="progressbar"
                aria-label="Benchmark progress"
                aria-valuenow={
                  data.progress.total > 0
                    ? Math.round(
                        (data.progress.completed / data.progress.total) * 100,
                      )
                    : 0
                }
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="h-full w-full origin-left rounded-full bg-accent transition-transform duration-[var(--dur-base)]"
                  style={{
                    transform: `scaleX(${
                      data.progress.total > 0
                        ? data.progress.completed / data.progress.total
                        : 0
                    })`,
                  }}
                />
              </div>
              <p className="mt-2 text-xs text-ink-3 tnum">
                {data.progress.total > 0
                  ? `${data.progress.completed} / ${data.progress.total} seeds`
                  : "Starting…"}
              </p>
            </Card>
          )}

          {data.latest_run && (
            <Card className="p-5">
              <SectionLabel>Latest run</SectionLabel>
              <p className="mt-2 text-xs text-ink-3 tnum">
                {formatUtc(data.latest_run.started_at)}
                {data.latest_run.completed_at
                  ? ` → ${formatUtc(data.latest_run.completed_at)}`
                  : " (running)"}{" "}
                · {data.latest_run.mode} mode
              </p>
              {data.latest_run.metrics ? (
                <div className="mt-3 overflow-x-auto lg:overflow-visible">
                  <table className="w-full border-collapse text-xs lg:table-min-xs">
                    <thead>
                      <tr className="border-b border-line text-left uppercase text-ink-3" style={{ letterSpacing: "var(--tracking-caps)" }}>
                        <th scope="col" className="py-2 pr-4 font-medium">Category</th>
                        <th scope="col" className="py-2 text-right font-medium">Recall</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(
                        data.latest_run.metrics.per_category_recall,
                      ).map(([category, recall]) => (
                        <tr key={category} className="border-b border-line last:border-b-0">
                          <td className="py-2 pr-4 font-mono text-ink-2">
                            {category}
                          </td>
                          <td className="py-2 text-right text-ink-2 tnum">
                            {formatPercent1(recall)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <dl className="mt-4 grid grid-cols-2 gap-4 text-xs">
                    <div>
                      <dt className="text-ink-3">False no-mundane-match rate</dt>
                      <dd className="mt-0.5 text-ink-2 tnum">
                        {formatPercent1(
                          data.latest_run.metrics.false_no_mundane_match_rate,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-ink-3">Insufficient detection rate</dt>
                      <dd className="mt-0.5 text-ink-2 tnum">
                        {formatPercent1(
                          data.latest_run.metrics.insufficient_detection_rate,
                        )}
                      </dd>
                    </div>
                  </dl>
                </div>
              ) : data.latest_run.error ? (
                <p className="mt-3 text-xs text-st-bad">
                  {data.latest_run.error}
                </p>
              ) : (
                <p className="mt-3 text-xs text-ink-3">No metrics yet.</p>
              )}
            </Card>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Run the benchmark?"
        description={`Runs every seed case through the full pipeline (${data?.prereg ? "against the preregistered thresholds" : "thresholds not yet preregistered"}).`}
        costText={
          costEstimate !== null
            ? `Estimated cost: up to ${formatUsd(costEstimate)}.`
            : undefined
        }
        unavailableText="Cost estimate unavailable. The run will still cost money."
        confirmLabel="Run benchmark"
        busy={busy}
        busyText="Starting…"
        onConfirm={() => void startRun()}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}
