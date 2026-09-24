// /ui/why — product differentiation page (nav item only; no spec endpoint).

export function WhyPage() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-12">
      <h1
        tabIndex={-1}
        className="font-display text-2xl font-semibold text-ink focus:outline-none"
      >
        Why it’s different
      </h1>
      <div className="mt-6 space-y-4 text-sm leading-relaxed text-ink-2">
        <p>
          UAP Verdict Foundry does not sell certainty. It runs a closed-set
          mundane-explanation battery against every case, reports what each
          category found, and returns a tri-state verdict with calibrated
          uncertainty.
        </p>
        <p>
          When no mundane category matches, the tool says so honestly and emits
          a speculative fiction seed that is permanently labelled as
          non-evidence. It never asserts extraterrestrial origin and never cites
          story content as forensic proof.
        </p>
        <p>
          Every verdict is fully autonomous: no per-case analyst sign-off, no
          hidden manual step, and every pipeline event is written to an
          auditable, tamper-detectable chain.
        </p>
      </div>
    </div>
  );
}
