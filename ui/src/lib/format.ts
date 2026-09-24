// UI spec §6.6 numeric display formats + §6.7 UTC timestamp policy.

export function formatUncertainty(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Not computed";
  return value.toFixed(2);
}

export function formatCalibrated(value: 0 | 1 | null | undefined): string {
  if (value === null || value === undefined) return "Not computed";
  return value === 1 ? "Yes" : "No";
}

export function formatAgreementPercent(
  value: number | null | undefined,
): string | null {
  if (value === null || value === undefined) return null;
  return `${Math.round(value * 100)}%`;
}

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(2);
}

export function formatPercent1(
  value: number | null | undefined,
): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(2)}`;
}

/** §6.7: render UTC timestamps as `YYYY-MM-DD HH:MM UTC`. No local time. */
export function formatUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = iso.match(
    /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?/,
  );
  if (match) return `${match[1]} ${match[2]} UTC`;
  return iso;
}

export function shortId(caseId: string): string {
  return caseId.slice(0, 8);
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API can be unavailable on non-secure contexts; fall back
    // to a transient textarea so copy still works.
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    } catch {
      return false;
    }
  }
}
