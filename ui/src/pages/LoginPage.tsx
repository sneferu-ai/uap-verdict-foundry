// /ui/login — spec §9.1. Centered dark plane, one field, inline errors.
// Uses loginPost (never jsonPost): a 401 renders inline, never the
// AuthExpired toast.

import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import {
  ApiError,
  loginPost,
  loginPostIsJson,
  NetworkError,
} from "../lib/api";
import { Button, Field, inputClass } from "../components/ui";

export function LoginPage() {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      let response: Response;
      try {
        response = await loginPost(
          "/login",
          new URLSearchParams({ token }),
        );
      } catch {
        // redirect: "error" surfaces a TypeError if the backend still
        // redirects on success (A-01 branch not active).
        setError(
          "Server returned an unexpected response. Try refreshing the page.",
        );
        return;
      }
      if (response.ok) {
        if (!loginPostIsJson(response)) {
          setError(
            "Server returned an unexpected response. Try refreshing the page.",
          );
          return;
        }
        // Success: {ok: true}. Hard-navigate so the fresh bootstrap (with
        // csrf + mode) is read from the served shell (spec §9.1).
        window.location.assign("/ui/cases");
        return;
      }
      if (response.status === 401) {
        setError("Invalid operator token.");
        return;
      }
      if (response.status === 403) {
        setError("Access denied. Check your operator token.");
        return;
      }
      setError("Sign in failed. Try again.");
    } catch (err) {
      if (err instanceof NetworkError) {
        setError("Cannot reach server. Check that `uapvf serve` is running.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Sign in failed. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-base px-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-3">
          <img
            src="/ui/brand-mark.svg"
            alt=""
            aria-hidden="true"
            className="h-12 w-12 rounded-lg"
            width="48"
            height="48"
          />
          <h1
            tabIndex={-1}
            className="font-display text-2xl font-semibold text-ink focus:outline-none"
          >
            Verdict Foundry
          </h1>
          <p className="text-xs text-ink-3">Operator token required</p>
        </div>
        <form
          onSubmit={onSubmit}
          noValidate
          className="mt-8 flex flex-col gap-4 rounded-lg border border-line bg-raised p-6"
        >
          <Field
            label="Operator token"
            htmlFor="login-token"
            required
            error={error}
          >
            <input
              id="login-token"
              name="token"
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              aria-labelledby="login-token-label"
              aria-required="true"
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "login-token-error" : undefined}
              className={inputClass(Boolean(error))}
            />
          </Field>
          <Button type="submit" loading={submitting} loadingText="Signing in…">
            Sign in
          </Button>
        </form>
        <p className="mt-6 text-center text-xs leading-relaxed text-ink-3">
          No output from this service claims extraterrestrial origin.{" "}
          <Link
            to="/ui/terms"
            className="text-ink-2 underline decoration-line-3 underline-offset-2 hover:text-ink"
          >
            Read the terms
          </Link>
          .
        </p>
      </div>
    </div>
  );
}
