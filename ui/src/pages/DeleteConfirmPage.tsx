// /ui/cases/:caseId/delete/confirm — spec §9.5. A dedicated page, not a toast:
// the deletion contract gets its own surface with explicit consent.

import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle } from "lucide-react";
import { Button, Card, EmptyState, ErrorCard, SectionLabel, Skeleton, StatusBadge, VerdictBadge } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { deleteContextKey, fetchDeleteContext } from "../lib/queries";
import { ApiError, AuthExpiredError, ConflictError, NotFoundError } from "../lib/api";
import { formatUtc } from "../lib/format";

export function DeleteConfirmPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const { postForm, onExpired } = useAuth();
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);

  const { data, error, isLoading } = useQuery({
    queryKey: deleteContextKey(caseId),
    queryFn: () => fetchDeleteContext(caseId),
    retry: 0,
  });

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!consent || busy) return;
    setBusy(true);
    try {
      await postForm(`/cases/${caseId}/delete`, {});
      toast("Case deleted.");
      navigate("/ui/cases");
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        onExpired();
        return;
      }
      if (err instanceof ConflictError) {
        toast(err.message);
        return;
      }
      if (err instanceof ApiError) toast(err.message);
      else toast("Deletion failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  if (error instanceof NotFoundError) {
    return (
      <EmptyState
        title="Case not found."
        hint="It may already be deleted."
        action={
          <Button variant="secondary" onClick={() => navigate("/ui/cases")}>
            Back to cases
          </Button>
        }
      />
    );
  }
  if (error) {
    return (
      <ErrorCard
        message="Cannot load the deletion context."
        hint={error.message}
        action={
          <Button variant="secondary" onClick={() => navigate(`/ui/cases/${caseId}`)}>
            Back to case
          </Button>
        }
      />
    );
  }
  if (isLoading || !data) {
    return (
      <div className="mx-auto max-w-lg">
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  const analyzing = data.status === "analyzing";

  return (
    <div className="mx-auto max-w-lg">
      <header className="border-b border-line pb-4">
        <h1 className="font-display text-2xl font-semibold text-ink">
          Delete this case permanently?
        </h1>
      </header>
      <Card className="mt-6 flex flex-col gap-4 p-6">
        <div className="flex items-center gap-2 text-st-bad">
          <AlertTriangle aria-hidden="true" className="h-5 w-5" />
          <SectionLabel className="text-st-bad">This will permanently delete</SectionLabel>
        </div>
        <ul className="flex flex-col gap-1.5 text-sm text-ink-2">
          <li>· the case row</li>
          <li>· all child rows (battery results, lineage outputs, pipeline stage runs)</li>
          <li>· all files in the case directory (media, reports, fiction seeds)</li>
          <li>· recoverable copies in product backups</li>
        </ul>
        <p className="text-xs text-ink-3">
          Audit entries preserve only the fact of deletion. An active legal
          hold blocks this action; successful deletion includes a restore proof.
        </p>
        <div className="flex items-center gap-2 border-t border-line pt-4">
          <StatusBadge status={data.status} />
          <VerdictBadge verdict={data.verdict} />
          <span className="ml-auto text-xs text-ink-3 tnum">
            created {formatUtc(data.created_at)}
          </span>
        </div>
        {analyzing && (
          <p role="alert" className="text-xs text-st-run">
            This case is being analyzed. Wait for the run to finish before
            deleting.
          </p>
        )}
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <div className="flex items-start gap-2.5">
            <input
              id="delete-consent"
              type="checkbox"
              checked={consent}
              disabled={analyzing}
              onChange={(event) => setConsent(event.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
            />
            <label htmlFor="delete-consent" className="text-sm text-ink-2">
              I understand what will be deleted and that it cannot be undone.
            </label>
          </div>
          <div className="flex items-center gap-3">
            <Button
              type="submit"
              variant="danger"
              disabled={!consent || analyzing}
              loading={busy}
              loadingText="Deleting…"
              title={analyzing ? "Case is being analyzed" : !consent ? "Confirm the checkbox first" : undefined}
            >
              Delete permanently
            </Button>
            <Button
              variant="secondary"
              onClick={() => navigate(`/ui/cases/${caseId}`)}
            >
              Cancel
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
