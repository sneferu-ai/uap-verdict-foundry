// The ONE controlled material interruption (BRAND_DIRECTION.md): the
// epistemic seam between findings and commentary. Shared by case detail
// and report so the two surfaces can never drift (DESIGN.md §1 Surface).

export function EvidenceDivider() {
  return (
    <div className="flex items-center gap-4 py-2" role="separator">
      <span aria-hidden="true" className="h-px flex-1 bg-line-3" />
      <p className="text-xs font-medium uppercase text-brand-muted" style={{ letterSpacing: "var(--tracking-caps)" }}>
        Evidence findings — above · Interpretive commentary — below
      </p>
      <span aria-hidden="true" className="h-px flex-1 bg-line-3" />
    </div>
  );
}
