// Fallback route (spec §9.10): factual message, no apology copy.

import { Link } from "react-router";
import { Card } from "../components/ui";

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md px-4 py-16">
      <Card className="flex flex-col items-start gap-3 p-8">
        <p className="font-mono text-xs uppercase text-ink-3" style={{ letterSpacing: "var(--tracking-caps)" }}>
          404
        </p>
        <h1 className="font-display text-xl font-semibold text-ink">
          That route does not exist.
        </h1>
        <p className="text-sm text-ink-2">
          The console serves cases, benchmark, and terms under{" "}
          <code className="font-mono text-xs">/ui/</code>.
        </p>
        <Link
          to="/ui/cases"
          className="mt-2 text-sm font-medium text-accent underline decoration-line-3 underline-offset-2 hover:text-accent-hover"
        >
          Go to cases
        </Link>
      </Card>
    </div>
  );
}
