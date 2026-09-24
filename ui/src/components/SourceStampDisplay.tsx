import { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import { CliHint } from "./ui";

/**
 * Source stamp renderer (spec §6.8 / §9.4 / §10.2).
 *
 * Collapsed by default; expands to pretty-printed JSON in a bounded scrollable
 * container. Oversized (>100KB) raw strings render as a red-bordered alert
 * with the first 4KB React-escaped, never silently truncated.
 */
export function SourceStampDisplay({
  stamp,
  caseId,
}: {
  stamp: string | object | null | undefined;
  caseId?: string;
}) {
  const [open, setOpen] = useState(false);
  const summaryId = useId();

  if (stamp === null || stamp === undefined || stamp === "") {
    return <span className="text-ink-3">—</span>;
  }

  const rawString = typeof stamp === "string" ? stamp : JSON.stringify(stamp);
  const byteSize = new Blob([rawString]).size;
  const oversized = byteSize > 100 * 1024;

  let pretty: string | null = null;
  let parseError: string | null = null;
  let parsedStamp: Record<string, unknown> | null = null;

  if (typeof stamp === "string") {
    try {
      const parsed = JSON.parse(stamp);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        parsedStamp = parsed as Record<string, unknown>;
      }
      pretty = JSON.stringify(parsed, null, 2);
    } catch (err) {
      parseError = err instanceof Error ? err.message : "Invalid JSON";
      pretty = null;
    }
  } else {
    if (stamp && typeof stamp === "object" && !Array.isArray(stamp)) {
      parsedStamp = stamp as Record<string, unknown>;
    }
    pretty = JSON.stringify(stamp, null, 2);
  }

  const source = String(parsedStamp?.source_id ?? "Recorded source")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
  const timestampRaw = parsedStamp?.utc_timestamp;
  const timestamp = typeof timestampRaw === "string"
    ? new Date(timestampRaw).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : null;
  const coverage = parsedStamp?.geographic_coverage;
  const reason = parsedStamp?.reason;
  const summary = [
    timestamp,
    typeof coverage === "string" ? `coverage: ${coverage}` : null,
    typeof reason === "string" ? reason : null,
  ].filter(Boolean).join(" · ");

  if (oversized) {
    const preview = rawString.slice(0, 4096);
    return (
      <div className="rounded-md border border-st-bad bg-st-bad-tint p-3">
        <p className="text-xs font-medium text-st-bad">
          Source stamp is {byteSize.toLocaleString()} bytes and exceeds the
          expected size.
        </p>
        <pre className="mt-2 max-h-[var(--stamp-max-h)] overflow-y-auto whitespace-pre-wrap rounded border border-line bg-inset p-2 font-mono text-xs text-ink-2">
          {preview}
          {"\n"}... (truncated; view full stamp via CLI:{" "}
          <code className="text-ink">
            uapvf case show {caseId ?? "&lt;case_id&gt;"}
          </code>
          )
        </pre>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={open ? summaryId : undefined}
        className="group relative inline-flex min-h-8 items-center gap-1 rounded-md px-1.5 py-1 text-left text-xs font-medium text-ink-2 transition-colors hover:bg-hovered hover:text-ink hit-6"
      >
        <ChevronRight
          aria-hidden="true"
          className={`h-3.5 w-3.5 text-ink-3 transition-transform duration-[var(--dur-fast)] ${open ? "rotate-90" : ""}`}
        />
        <span>{source}</span>
        <span className="sr-only">{open ? "Collapse" : "Expand"}</span>
      </button>
      {summary && <span className="pl-6 text-xs leading-relaxed text-ink-3">{summary}</span>}
      {open && (
        <div id={summaryId} className="w-full">
          {parseError ? (
            <div className="rounded-md border border-st-run bg-st-run-tint p-3">
              <p className="text-xs font-medium text-st-run">
                Source stamp could not be parsed: {parseError}
              </p>
              <pre className="mt-2 max-h-[var(--stamp-max-h)] overflow-y-auto whitespace-pre-wrap rounded border border-line bg-inset p-2 font-mono text-xs text-ink-2">
                {rawString.slice(0, 4096)}
                {rawString.length > 4096 && (
                  <>
                    {"\n"}... (truncated; view full stamp via CLI:{" "}
                    <code className="text-ink">
                      uapvf case show {caseId ?? "&lt;case_id&gt;"}
                    </code>
                    )
                  </>
                )}
              </pre>
            </div>
          ) : (
            <pre className="max-h-[var(--stamp-max-h)] overflow-y-auto whitespace-pre-wrap rounded border border-line bg-inset p-2 font-mono text-xs text-ink-2">
              {pretty}
            </pre>
          )}
          {caseId && (
            <div className="mt-2">
              <CliHint
                command={`uapvf case show ${caseId}`}
                note="View the full source stamp in the terminal."
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
