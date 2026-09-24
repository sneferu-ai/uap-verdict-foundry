// App shell: sidebar (brand mark + nav + system status + logout), mobile
// top bar, skip link, and the B13-mandated footer line.

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate } from "react-router";
import {
  ClipboardList,
  FlaskConical,
  Fingerprint,
  FolderInput,
  LogOut,
  Menu,
  X,
} from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { jsonGet } from "../lib/api";
import type { ReadinessResponse } from "../lib/types";
import { ModeBadge } from "./ui";
import { SystemStatusPopover } from "./SystemStatusPopover";

const NAV_ITEMS = [
  { to: "/ui/cases", label: "Cases", icon: ClipboardList, end: true },
  { to: "/ui/cases/new", label: "New Case", icon: FolderInput, end: false },
  { to: "/ui/benchmark", label: "Benchmark", icon: FlaskConical, end: true },
  { to: "/ui/why", label: "Why it’s different", icon: Fingerprint, end: true },
];

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Primary" className="flex flex-col gap-1">
      {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            [
              "relative flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm font-medium hit-6",
              "transition-colors duration-[var(--dur-fast)]",
              isActive
                ? "bg-raised text-ink"
                : "text-ink-2 hover:bg-hovered hover:text-ink",
            ].join(" ")
          }
        >
          {({ isActive }) => (
            <>
              {isActive && (
                <span
                  aria-hidden="true"
                  className="absolute left-0 h-4 w-0.5 rounded-full bg-accent"
                />
              )}
              <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
              {label}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function Wordmark() {
  return (
    <div className="flex items-center gap-3">
      {/* Production vector mark: readable at navigation and favicon scale. */}
      <img
        src="/ui/brand-mark.svg"
        alt="UAP Verdict Foundry mark"
        className="h-9 w-9 rounded-lg"
        width="32"
        height="32"
      />
      <div className="leading-none">
        <p className="font-display text-md font-semibold text-ink">
          Verdict Foundry
        </p>
        <p className="mt-1 font-mono text-xs text-ink-3">operator console</p>
      </div>
    </div>
  );
}

export function Layout() {
  const { authenticated, signOut, mode } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mobilePanelRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);
  const readiness = useQuery<ReadinessResponse>({
    queryKey: ["readyz"],
    queryFn: () => jsonGet<ReadinessResponse>("/readyz"),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    retry: 1,
  });
  const effectiveMode = readiness.data?.mode ?? mode;

  // Mobile nav focus management (spec §11): opening moves focus to the
  // first nav item; closing returns it to the toggle button.
  useEffect(() => {
    if (mobileOpen) {
      mobilePanelRef.current
        ?.querySelector<HTMLElement>("a[href], button:not([disabled])")
        ?.focus();
    } else if (wasOpen.current) {
      menuButtonRef.current?.focus();
    }
    wasOpen.current = mobileOpen;
  }, [mobileOpen]);

  const handleSignOut = async () => {
    await signOut();
    navigate("/ui/login");
  };

  const sidebarBody = (
    <div className="flex h-full flex-col gap-6">
      <Wordmark />
      <NavItems onNavigate={() => setMobileOpen(false)} />
      <div className="mt-auto flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <ModeBadge mode={effectiveMode} />
          <SystemStatusPopover />
        </div>
        {authenticated && (
          <button
            type="button"
            onClick={handleSignOut}
            className="relative flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm font-medium text-ink-2 transition-colors duration-[var(--dur-fast)] hover:bg-hovered hover:text-ink hit-6"
          >
            <LogOut aria-hidden="true" className="h-4 w-4" />
            Sign out
          </button>
        )}
        <p className="border-t border-line pt-3 text-xs leading-snug text-ink-3">
          No output from this service claims extraterrestrial origin.
        </p>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-base">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside
        className="fixed inset-y-0 left-0 hidden w-56 border-r border-line bg-base md:block"
        aria-label="Sidebar"
      >
        <div className="flex h-full flex-col px-4 py-6">{sidebarBody}</div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 flex h-14 items-center justify-between border-b border-line bg-base px-4 md:hidden" style={{ zIndex: "var(--z-sticky)" }}>
        <Wordmark />
        <button
          ref={menuButtonRef}
          type="button"
          aria-label={mobileOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={mobileOpen}
          aria-controls="mobile-nav"
          onClick={() => setMobileOpen((open) => !open)}
          className="flex h-11 w-11 items-center justify-center rounded-md text-ink-2 hover:bg-hovered"
        >
          {mobileOpen ? (
            <X aria-hidden="true" className="h-5 w-5" />
          ) : (
            <Menu aria-hidden="true" className="h-5 w-5" />
          )}
        </button>
      </header>
      {mobileOpen && (
        <div
          id="mobile-nav"
          ref={mobilePanelRef}
          onKeyDown={(event) => {
            if (event.key === "Escape") setMobileOpen(false);
          }}
          className="border-b border-line bg-base px-4 py-4 md:hidden"
        >
          {sidebarBody}
        </div>
      )}

      <main
        id="main-content"
        tabIndex={-1}
        className="mx-auto min-h-screen max-w-5xl px-4 pb-16 pt-6 focus:outline-none sm:px-6 md:ml-56 md:px-8 lg:max-w-6xl"
      >
        {readiness.data?.references?.banner && (
          <div
            role="status"
            className="mb-5 rounded-lg border border-st-run bg-st-run-tint px-4 py-3 text-sm text-st-run"
          >
            <strong className="font-semibold">Reference integrity degraded.</strong>{" "}
            New cases and deliveries are blocked in strict production mode until
            the frozen Phase 2 sources pass hash and excerpt verification.
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
