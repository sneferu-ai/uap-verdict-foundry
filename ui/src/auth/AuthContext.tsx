// Auth state + CSRF lifecycle (spec §8.2).
//
// Authenticated = the server bootstrap says so, OR a preview-launcher
// bootstrap record is present (consumed verbatim — no invented identities).
// AuthExpiredError anywhere in the tree flips the client to signed-out and
// ProtectedRoute sends the operator to /ui/login with a toast.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react";
import { Navigate, useLocation } from "react-router";
import { Toaster, toast } from "sonner";
import { useBootstrap } from "../bootstrap/BootstrapContext";
import {
  AuthExpiredError,
  CsrfExpiredError,
  jsonPost,
  refreshCsrfFromShell,
} from "../lib/api";
import type { UapvMode } from "../lib/types";

export interface AuthContextValue {
  authenticated: boolean;
  csrfToken: string | null;
  mode: UapvMode;
  username: string | null;
  signOut: () => Promise<void>;
  onExpired: () => void;
  /** CSRF-guarded form POST with the one-shot refresh ladder (§3.7). */
  postForm: <T>(path: string, fields: Record<string, string>) => Promise<T>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { bootstrap, preview, setBootstrap } = useBootstrap();
  const { pathname } = useLocation();

  const authenticated = bootstrap.authenticated || preview !== null;
  const username = preview?.username ?? null;

  // True once this client has actually held a session. A cold logged-out
  // bookmark is not an expiry — nothing expired — so it gets a silent
  // redirect, never the "Session expired" copy.
  const wasAuthenticated = useRef(false);
  useEffect(() => {
    if (authenticated) wasAuthenticated.current = true;
  }, [authenticated]);

  const onExpired = useCallback(() => {
    setBootstrap((prev) => ({ ...prev, authenticated: false, csrf_token: null }));
    if (wasAuthenticated.current && pathname !== "/ui/login") {
      toast("Session expired. Sign in again to continue.");
    }
    // Reset after firing: a real expiry touches onExpired from both the
    // failing request handler and the ProtectedRoute redirect — the
    // operator gets exactly one toast, never one per call site.
    wasAuthenticated.current = false;
  }, [pathname, setBootstrap]);

  const signOut = useCallback(async () => {
    const csrf = bootstrap.csrf_token ?? "";
    try {
      await jsonPost("/logout", new URLSearchParams({ csrf_token: csrf }));
    } catch (error) {
      // A dead session still signs the client out locally.
      if (!(error instanceof AuthExpiredError)) {
        toast("Logout failed. Session cleared locally.");
      }
    }
    setBootstrap((prev) => ({ ...prev, authenticated: false, csrf_token: null }));
  }, [bootstrap.csrf_token, setBootstrap]);

  const postForm = useCallback(
    async <T,>(path: string, fields: Record<string, string>): Promise<T> => {
      const params = new URLSearchParams(fields);
      try {
        return await jsonPost<T>(path, params);
      } catch (error) {
        if (!(error instanceof CsrfExpiredError)) throw error;
        // §3.7: refresh once. A fresh bootstrap with no csrf means the
        // session died server-side.
        const fresh = await refreshCsrfFromShell();
        if (!fresh || !fresh.authenticated) {
          throw new AuthExpiredError();
        }
        setBootstrap(fresh);
        if (!fresh.csrf_token) throw new AuthExpiredError();
        params.set("csrf_token", fresh.csrf_token);
        return jsonPost<T>(path, params);
      }
    },
    [setBootstrap],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      authenticated,
      csrfToken: bootstrap.csrf_token,
      mode: bootstrap.mode,
      username,
      signOut,
      onExpired,
      postForm,
    }),
    [authenticated, bootstrap, username, signOut, onExpired, postForm],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { authenticated, onExpired } = useAuth();
  // Side effects live in an effect, never in the render body: a
  // setState-on-ancestor + toast during render double-fires under
  // StrictMode and warns in dev.
  useEffect(() => {
    if (!authenticated) onExpired();
  }, [authenticated, onExpired]);
  if (!authenticated) {
    return <Navigate to="/ui/login" replace />;
  }
  return <>{children}</>;
}

/** Login page redirect when already authenticated (spec §9.1). */
export function LoginRedirect({ children }: { children: ReactNode }) {
  const { authenticated } = useAuth();
  if (authenticated) return <Navigate to="/ui/cases" replace />;
  return <>{children}</>;
}

/** The console renders a single polite toaster (§6.1 calm). */
export function ToastViewport() {
  return <Toaster position="bottom-right" />;
}
