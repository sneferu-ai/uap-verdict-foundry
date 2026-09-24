// /ui/cases/:caseId/fiction — spec §9.7. Quarantined, byte-faithful,
// labelled. No markdown rendering, ever.

import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button, Card, CliHint, ErrorCard, SectionLabel, SizedContent, Skeleton, StatusBadge, VerdictBadge } from "../components/ui";
import { caseDetailKey, fetchCaseDetail, fetchFiction, fictionKey } from "../lib/queries";
import { NotFoundError, downloadBlob } from "../lib/api";

const QUARANTINE_LABEL = "[SPECULATIVE FICTION — NOT FORENSIC EVIDENCE]";

export function FictionPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();

  const guard = useQuery({
    queryKey: caseDetailKey(caseId),
    queryFn: () => fetchCaseDetail(caseId),
    retry: 0,
  });

  const guardCase = guard.data?.case;

  const fiction = useQuery({
    queryKey: fictionKey(caseId),
    queryFn: () => fetchFiction(caseId),
    enabled: Boolean(guardCase),
    retry: 0,
  });

  if (guard.isLoading || (guardCase && fiction.isLoading)) {
    return (
      <div className="flex flex-col gap-4" role="status" aria-label="Loading fiction seed">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-10 w-full rounded-md" />
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
  if (fiction.error) {
    return (
      <ErrorCard
        message="Fiction seed unavailable."
        hint={fiction.error.message}
        action={
          <Button variant="secondary" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        }
      />
    );
  }
  const data = fiction.data;
  if (!data) return null;

  // mundane_identified: no fiction seed, by construction.
  if (!data.available && !data.withheld && data.verdict === "mundane_identified") {
    return (
      <div className="mx-auto max-w-lg">
        <Card className="flex flex-col items-start gap-3 p-6">
          <VerdictBadge verdict={data.verdict} />
          <h1 className="font-display text-xl font-semibold text-ink">
            No fiction seed for this case.
          </h1>
          <p className="text-sm text-ink-2">
            Fiction seeds are generated only for unresolved cases
            (no_mundane_match or insufficient_data). This case matched a
            mundane explanation.
          </p>
          <Button variant="secondary" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        </Card>
      </div>
    );
  }
  if (!data.available && !data.withheld) {
    return (
      <div className="mx-auto max-w-lg">
        <Card className="flex flex-col items-start gap-3 p-6">
          <StatusBadge status={guardCase.status} />
          <h1 className="font-display text-xl font-semibold text-ink">
            Fiction not ready yet.
          </h1>
          <p className="text-sm text-ink-2">
            The fiction stage has not completed for this case.
          </p>
          <Button variant="secondary" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div>
          <p className="font-mono text-xs text-ink-3">
            case {caseId.slice(0, 8)}
          </p>
          <h1 className="mt-1 font-display text-2xl font-semibold text-ink">
            Fiction seed
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            disabled={data.content === null}
            title={data.content === null ? "No content to download" : undefined}
            onClick={() => {
              if (data.content === null) {
                toast("No fiction content available to download.");
                return;
              }
              downloadBlob(
                data.content,
                `fiction_${caseId}.txt`,
                "text/plain",
              );
            }}
          >
            Download text
          </Button>
          <Button variant="ghost" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        </div>
      </header>

      {data.withheld ? (
        <Card className="flex flex-col items-start gap-3 border-st-bad p-6">
          <h2 className="text-md font-semibold text-ink">
            Fiction seed withheld.
          </h2>
          <p className="text-sm text-ink-2">
            The fiction stage failed validation for this case, so its output
            was withheld. The case verdict and report are unaffected.
          </p>
        </Card>
      ) : (
        <>
          {/* Quarantine banner — a label, not a surface treatment. */}
          <div
            role="note"
            aria-label="Speculative fiction warning"
            className="flex items-center gap-3 rounded-md bg-quarantine px-4 py-3"
          >
            <p className="text-sm font-semibold text-quarantine-ink">
              {QUARANTINE_LABEL}
            </p>
          </div>
          <Card className="p-6">
            <SectionLabel>Byte-faithful content</SectionLabel>
            <p className="mt-2 text-xs text-ink-3">
              Rendered verbatim in a monospace block. No markdown, no
              formatting — what you see is exactly what was generated.
            </p>
            <SizedContent content={data.content} as="pre" caseId={caseId} />
          </Card>
        </>
      )}

      {/* No CLI hint in the withheld state: `uapvf case fiction` has no
          content to print here, so claiming it prints "the same content"
          would be a lie on the product's most brutal surface. */}
      {!data.withheld && (
        <Card className="p-5">
          <SectionLabel>CLI</SectionLabel>
          <div className="mt-3">
            <CliHint
              command={`uapvf case fiction ${caseId}`}
              note="The CLI prints the same byte-faithful content."
            />
          </div>
        </Card>
      )}
    </div>
  );
}
