// /ui/terms — public page (no auth, no 401; spec §7.10).

import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { ErrorCard, Skeleton } from "../components/ui";
import { fetchTerms, termsKey } from "../lib/queries";

export function TermsPage() {
  const { data, isError, isLoading } = useQuery({
    queryKey: termsKey,
    queryFn: fetchTerms,
  });

  return (
    <div className="mx-auto max-w-2xl px-4 py-12">
      <h1
        tabIndex={-1}
        className="font-display text-2xl font-semibold text-ink focus:outline-none"
      >
        Terms of service
      </h1>
      <div className="mt-6">
        {isLoading && (
          <div className="flex flex-col gap-3" role="status" aria-label="Loading terms">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-4/5" />
            <Skeleton className="h-4 w-10/12" />
          </div>
        )}
        {isError && (
          <ErrorCard
            message="Terms could not be loaded."
            hint="Cannot reach server. Check that `uapvf serve` is running."
          />
        )}
        {data && (
          <pre className="whitespace-pre-wrap rounded-lg border border-line bg-raised p-6 font-body text-sm leading-relaxed text-ink-2">
            {data.terms_text}
          </pre>
        )}
      </div>
      <p className="mt-8 text-xs text-ink-3">
        <Link
          to="/ui/login"
          className="underline decoration-line-3 underline-offset-2 hover:text-ink"
        >
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
