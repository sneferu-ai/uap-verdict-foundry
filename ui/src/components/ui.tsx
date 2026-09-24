// Shared UI primitives — one style throughout (DESIGN.md §6 component
// states). Every primitive consumes tokens only; no raw values here.

import { forwardRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { AlertTriangle, Check, Copy, TerminalSquare } from "lucide-react";
import { copyText } from "../lib/format";
import type { CaseStatus, Verdict } from "../lib/types";

/* ------------------------------------------------------------------ */
/* Buttons                                                             */
/* ------------------------------------------------------------------ */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  loading?: boolean;
  loadingText?: string;
  size?: "default" | "sm";
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-ink hover:bg-accent-hover active:bg-accent-pressed",
  secondary:
    "border border-line-2 bg-transparent text-ink hover:bg-hovered active:bg-inset",
  ghost: "bg-transparent text-ink-2 hover:bg-hovered hover:text-ink active:bg-inset",
  danger: "bg-danger text-danger-ink hover:bg-danger-hover active:bg-danger",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { variant = "primary", loading = false, loadingText, size = "default",
      children, className = "", disabled, type, "aria-label": ariaLabel,
      ...rest },
    ref,
  ) {
    // Visual height stays dense; the hit-* classes expand the pointer
    // target to --hit-target via ::after (tokens.css / DESIGN.md §6).
    const height =
      size === "sm" ? "h-7 px-3 text-xs hit-8" : "h-9 px-4 text-sm hit-4";
    return (
      <button
        ref={ref}
        type={type ?? "button"}
        disabled={disabled || loading}
        aria-busy={loading || undefined}
        aria-label={ariaLabel}
        className={[
          "inline-flex items-center justify-center gap-2 rounded-md font-medium",
          "transition-colors duration-[var(--dur-fast)]",
          "active:translate-y-px",
          "disabled:opacity-45 disabled:cursor-not-allowed",
          height,
          VARIANT_CLASSES[variant],
          className,
        ].join(" ")}
        {...rest}
      >
        {loading && (
          <svg
            className="h-3.5 w-3.5 animate-spin"
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden="true"
          >
            <circle
              cx="8"
              cy="8"
              r="6.5"
              stroke="currentColor"
              strokeOpacity="0.3"
              strokeWidth="2"
            />
            <path
              d="M14.5 8a6.5 6.5 0 0 0-6.5-6.5"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        )}
        {loading && loadingText ? loadingText : children}
      </button>
    );
  },
);

/* ------------------------------------------------------------------ */
/* Cards + layout primitives                                           */
/* ------------------------------------------------------------------ */

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-line bg-raised ${className}`}
    >
      {children}
    </div>
  );
}

/** Section heading (spec §11: h1 per page, h2 sections). Styled as the
    uppercase caps label, but semantically an h2 for heading navigation. */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h2 className={`text-xs font-medium uppercase text-ink-3 ${className ?? ""}`} style={{ letterSpacing: "var(--tracking-caps)" }}>
      {children}
    </h2>
  );
}

/* ------------------------------------------------------------------ */
/* Status + verdict badges (spec §6.4; hue + dot + label always)       */
/* ------------------------------------------------------------------ */

interface PillStyle {
  text: string;
  tint: string;
  label: string;
}

const STATUS_STYLES: Record<CaseStatus, PillStyle> = {
  queued: { text: "text-st-run", tint: "bg-st-run-tint", label: "Queued" },
  analyzing: { text: "text-st-run", tint: "bg-st-run-tint", label: "Analyzing" },
  verdict_ready: { text: "text-st-ok", tint: "bg-st-ok-tint", label: "Verdict ready" },
  complete: { text: "text-st-ok", tint: "bg-st-ok-tint", label: "Complete" },
  failed: { text: "text-st-bad", tint: "bg-st-bad-tint", label: "Failed" },
  spend_capped: { text: "text-st-cap", tint: "bg-st-cap-tint", label: "Spend capped" },
  rerun_requested: { text: "text-st-rerun", tint: "bg-st-rerun-tint", label: "Rerun requested" },
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  const style = STATUS_STYLES[status];
  return (
    <span
      className={`inline-flex h-5 items-center gap-1.5 rounded-full px-2 text-xs font-medium ${style.text} ${style.tint}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {style.label}
    </span>
  );
}

const VERDICT_STYLES: Record<string, { text: string; tint: string; label: string }> = {
  no_mundane_match: {
    text: "text-st-verdict",
    tint: "bg-st-verdict-tint",
    label: "No mundane match",
  },
  mundane_identified: {
    text: "text-st-verdict",
    tint: "bg-st-verdict-tint",
    label: "Mundane identified",
  },
  insufficient_data: {
    text: "text-st-verdict",
    tint: "bg-st-verdict-tint",
    label: "Insufficient data",
  },
};

/** Procedural calm (spec §6.1): every verdict wears identical weight. */
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  if (!verdict) {
    return (
      <span className="inline-flex h-5 items-center gap-1.5 rounded-full bg-st-neutral-tint px-2 text-xs font-medium text-st-neutral">
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
        Pending
      </span>
    );
  }
  const style = VERDICT_STYLES[verdict];
  if (!style) return <span className="text-xs text-ink-3">{verdict}</span>;
  return (
    <span
      className={`inline-flex h-5 items-center gap-1.5 rounded-full px-2 text-xs font-medium ${style.text} ${style.tint}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {style.label}
    </span>
  );
}

export function PaymentBadge({ status }: { status: string }) {
  const map: Record<string, PillStyle> = {
    unpaid: { text: "text-st-neutral", tint: "bg-st-neutral-tint", label: "Unpaid" },
    paid: { text: "text-st-ok", tint: "bg-st-ok-tint", label: "Paid" },
    comped: { text: "text-st-verdict", tint: "bg-st-verdict-tint", label: "Comped" },
  };
  const style = map[status] ?? map.unpaid;
  return (
    <span
      className={`inline-flex h-5 items-center gap-1.5 rounded-full px-2 text-xs font-medium ${style.text} ${style.tint}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {style.label}
    </span>
  );
}

/** Quality gate (spec §6.3): 1 = vision ran, 0 = vision skipped,
    null = pending. Same pill weight as every other badge. */
export function QualityGateBadge({ pass }: { pass: 0 | 1 | null }) {
  const style =
    pass === 1
      ? { text: "text-st-ok", tint: "bg-st-ok-tint", label: "Pass" }
      : pass === 0
        ? { text: "text-st-run", tint: "bg-st-run-tint", label: "Skipped" }
        : { text: "text-st-neutral", tint: "bg-st-neutral-tint", label: "Pending" };
  return (
    <span
      className={`inline-flex h-5 items-center gap-1.5 rounded-full px-2 text-xs font-medium ${style.text} ${style.tint}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      Quality gate: {style.label}
    </span>
  );
}

export function ModeBadge({ mode }: { mode: "mock" | "simulation" | "live" }) {
  const style = mode === "live"
    ? { classes: "bg-st-ok-tint text-st-ok", label: "LIVE",
        title: "Live mode — production gates enforced" }
    : mode === "simulation"
      ? { classes: "bg-st-run-tint text-st-run", label: "SIMULATION",
          title: "Simulation — real local analysis without production certification" }
      : { classes: "bg-st-bad-tint text-st-bad", label: "MOCK",
          title: "Mock mode — deterministic fixtures" };
  return (
    <span
      className={`inline-flex h-5 items-center rounded-full px-2 font-mono text-xs ${style.classes}`}
      title={style.title}
    >
      {style.label}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Lifecycle states                                                    */
/* ------------------------------------------------------------------ */

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-line-2 px-6 py-10">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="text-xs text-ink-3">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({
  message,
  hint,
}: {
  message: string;
  hint?: string;
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-line-2 bg-st-bad-tint px-4 py-3"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-st-bad" />
      <div>
        <p className="text-sm text-ink">{message}</p>
        {hint && <p className="mt-1 text-xs text-ink-3">{hint}</p>}
      </div>
    </div>
  );
}

export function ErrorCard({
  message,
  hint,
  action,
}: {
  message: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <Card className="p-6">
      <div className="flex items-start gap-3">
        <AlertTriangle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-st-bad" />
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm font-medium text-ink">{message}</p>
          {hint && <p className="text-xs text-ink-3">{hint}</p>}
          {action}
        </div>
      </div>
    </Card>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`skeleton ${className}`} />;
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-3" role="status" aria-label="Loading">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex items-center gap-4">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Copyable CLI hint (spec §5.5 — never a fake button)                 */
/* ------------------------------------------------------------------ */

export function CliHint({ command, note }: { command: string; note?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 rounded-md border border-line bg-inset px-3 py-2">
        <TerminalSquare aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-ink-3" />
        <code className="flex-1 overflow-x-auto font-mono text-xs text-ink-2">
          {command}
        </code>
        <Button
          variant="ghost"
          size="sm"
          aria-label={copied ? "Copied" : `Copy command: ${command}`}
          onClick={async () => {
            const ok = await copyText(command);
            setCopied(ok);
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? (
            <Check aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <Copy aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          <span className="sr-only">{copied ? "Copied" : "Copy"}</span>
        </Button>
      </div>
      {note && <p className="text-xs text-ink-3">{note}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Forms                                                               */
/* ------------------------------------------------------------------ */

export function Field({
  label,
  htmlFor,
  required,
  optional,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  required?: boolean;
  optional?: boolean;
  hint?: string;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={htmlFor}
        id={`${htmlFor}-label`}
        className="text-xs font-medium text-ink-2"
      >
        {label}
        {required && (
          <span aria-hidden="true" className="ml-1 text-st-bad">
            *
          </span>
        )}
        {optional && <span className="ml-1 font-normal text-ink-3">optional</span>}
      </label>
      {children}
      {hint && !error && <p className="text-xs text-ink-3">{hint}</p>}
      {error && (
        <p id={`${htmlFor}-error`} role="alert" className="text-xs text-st-bad">
          {error}
        </p>
      )}
    </div>
  );
}

export const inputClass = (invalid = false) =>
  [
    "h-11 w-full rounded-md border bg-inset px-3 text-[var(--text-base-mobile)] text-ink sm:h-9 sm:text-sm",
    "placeholder:text-ink-disabled",
    "transition-colors duration-[var(--dur-fast)]",
    "hover:border-line-3 focus:border-focus",
    "disabled:opacity-45 disabled:cursor-not-allowed",
    invalid ? "border-st-bad" : "border-line-2",
  ].join(" ");

/* ------------------------------------------------------------------ */
/* Progressive disclosure (keeps intake ≤12 visible controls)          */
/* ------------------------------------------------------------------ */

export function Disclosure({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="group rounded-md border border-line bg-raised" open={defaultOpen}>
      <summary className="flex cursor-pointer items-center gap-2 rounded-md px-4 py-3 text-sm font-medium text-ink-2 transition-colors hover:bg-hovered [&::-webkit-details-marker]:hidden">
        <span
          aria-hidden="true"
          className="inline-block text-ink-3 transition-transform duration-[var(--dur-fast)] group-open:rotate-90"
        >
          ▸
        </span>
        {summary}
      </summary>
      <div className="border-t border-line px-4 py-4">{children}</div>
    </details>
  );
}

/* ------------------------------------------------------------------ */
/* Large content guard (spec §6.8)                                    */
/* ------------------------------------------------------------------ */

export function SizedContent({
  content,
  as = "pre",
  caseId,
}: {
  content: string | null | undefined;
  as?: "pre" | "div";
  caseId?: string;
}) {
  if (content === null || content === undefined || content === "") {
    return <span className="text-ink-3">—</span>;
  }
  const bytes = new Blob([content]).size;
  if (bytes > 500 * 1024) {
    return (
      <div className="rounded-md border border-st-bad bg-st-bad-tint p-4">
        <p className="text-sm font-medium text-st-bad">
          Content exceeds expected size ({bytes.toLocaleString()} bytes).
        </p>
        <p className="mt-1 text-xs text-ink-3">
          Export via CLI:{" "}
          <code className="font-mono text-ink">
            uapvf case export {caseId ?? "<case_id>"}
          </code>
        </p>
      </div>
    );
  }
  const scrollable = bytes > 64 * 1024;
  const baseClass = scrollable
    ? "max-h-[var(--content-max-h)] overflow-y-auto"
    : "";
  if (as === "pre") {
    return (
      <pre
        className={`mt-4 whitespace-pre-wrap rounded-md bg-inset p-4 font-mono text-xs leading-relaxed text-ink-2 ${baseClass}`}
      >
        {content}
      </pre>
    );
  }
  return (
    <div
      className={`mt-3 whitespace-pre-wrap text-sm leading-relaxed text-ink-2 ${baseClass}`}
    >
      {content}
    </div>
  );
}
