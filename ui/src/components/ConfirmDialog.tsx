// ConfirmDialog — every cost-bearing action names its maximum cost first
// (spec §8.8, UI-32). Initial focus lands on the confirm button; Esc and
// backdrop click close; focus returns to the trigger on close.
//
// Rendered in a portal at the end of <body> (spec §11). Route content
// enters with a transform animation, and any non-none transform makes the
// ancestor the containing block for position:fixed — in-tree, a scrolled
// tall page would pin the backdrop to the document, not the viewport, and
// center the panel out of sight. The portal escapes that ancestor.

import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "./ui";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  costText?: string;
  unavailableText?: string;
  confirmLabel: string;
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  busyText?: string;
  children?: ReactNode;
}

export function ConfirmDialog({
  open,
  title,
  description,
  costText,
  unavailableText,
  confirmLabel,
  variant = "primary",
  onConfirm,
  onCancel,
  busy,
  busyText,
  children,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    return () => {
      restoreRef.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.stopPropagation();
        onCancel();
        return;
      }
      if (event.key === "Tab" && panelRef.current) {
        // Minimal focus trap across the panel's focusable elements.
        const focusables = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0"
      style={{ zIndex: "var(--z-modal-bg)" }}
    >
      <button
        type="button"
        aria-label="Close dialog"
        tabIndex={-1}
        className="absolute inset-0 cursor-default"
        style={{
          backgroundColor: "var(--overlay-backdrop)",
          backdropFilter: "blur(var(--blur-overlay))",
          WebkitBackdropFilter: "blur(var(--blur-overlay))",
        }}
        onClick={() => {
          if (!busy) onCancel();
        }}
      />
      <div className="flex min-h-full items-center justify-center p-4">
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={descId}
          className="relative w-full rounded-lg border border-line-2 bg-overlay shadow-3"
          style={{ zIndex: "var(--z-modal)", maxWidth: "var(--modal-max-w)" }}
        >
          <div className="flex flex-col gap-3 p-6">
            <h2 id={titleId} className="text-md font-semibold text-ink">
              {title}
            </h2>
            <p id={descId} className="text-sm text-ink-2">{description}</p>
            {children}
            <p
              className={`text-xs ${costText ? "text-ink-3" : "text-st-run"}`}
              aria-live="polite"
            >
              {costText ??
                unavailableText ??
                "Cost estimate unavailable. The action will still run."}
            </p>
            <div className="mt-1 flex justify-end gap-2">
              <Button variant="ghost" onClick={onCancel} disabled={busy}>
                Cancel
              </Button>
              <Button
                ref={confirmRef}
                variant={variant}
                onClick={onConfirm}
                loading={busy}
                loadingText={busyText}
              >
                {confirmLabel}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
