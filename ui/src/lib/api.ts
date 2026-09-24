// UI spec §8.3 API wrapper contracts. Every call: Accept: application/json,
// credentials: "include", redirect: "error". Error classification uses the
// typed `code` field from the JSON body — never substring matching (D-20).

import type { Bootstrap, ErrorCode, FieldError } from "./types";

export const UPLOAD_TIMEOUT_MS = 600_000; // 10 minutes (spec §8.3)

export class ApiError extends Error {
  code: ErrorCode | null;
  status: number;
  constructor(message: string, status = 0, code: ErrorCode | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export class AuthExpiredError extends ApiError {
  constructor(message = "Session expired") {
    super(message, 401, "auth_expired");
    this.name = "AuthExpiredError";
  }
}

export class CsrfExpiredError extends ApiError {
  constructor(message = "CSRF token expired") {
    super(message, 403, "csrf_expired");
    this.name = "CsrfExpiredError";
  }
}

export class SpendCapError extends ApiError {
  constructor(
    message = "Monthly spend cap reached. Cases will queue when budget allows.",
  ) {
    super(message, 403, "spend_cap_reached");
    this.name = "SpendCapError";
  }
}

export class ConflictError extends ApiError {
  constructor(message: string) {
    super(message, 409, "conflict");
    this.name = "ConflictError";
  }
}

export class NotFoundError extends ApiError {
  constructor(message = "Not found") {
    super(message, 404, "not_found");
    this.name = "NotFoundError";
  }
}

export class ValidationError extends ApiError {
  errors: FieldError[];
  constructor(message: string, errors: FieldError[] = []) {
    super(message, 422, "validation_error");
    this.name = "ValidationError";
    this.errors = errors;
  }
}

export class NetworkError extends ApiError {
  constructor(message = "Cannot reach server. Check that `uapvf serve` is running.") {
    super(message, 0, null);
    this.name = "NetworkError";
  }
}

export class TimeoutError extends ApiError {
  constructor(
    message = "Upload timed out after 10 minutes. For very large files, use the CLI: uapvf case create",
  ) {
    super(message, 0, null);
    this.name = "TimeoutError";
  }
}

const A01_MESSAGE =
  "Server returned an unexpected response. The JSON branch may not be active. Try refreshing the page.";

function isJsonContentType(response: Response): boolean {
  const ct = response.headers.get("Content-Type") || "";
  return ct.startsWith("application/json");
}

async function parseErrorBody(response: Response): Promise<{
  error?: string;
  code?: ErrorCode;
  errors?: FieldError[];
}> {
  try {
    if (isJsonContentType(response)) {
      return await response.json();
    }
  } catch {
    // fall through
  }
  return {};
}

function throwForStatus(
  response: Response,
  body: { error?: string; code?: ErrorCode; errors?: FieldError[] },
): never {
  const message = body.error || response.statusText || "Request failed";
  switch (response.status) {
    case 401:
      throw new AuthExpiredError(
        body.code === "auth_failed" ? "Invalid operator token." : message,
      );
    case 403:
      if (body.code === "csrf_expired") throw new CsrfExpiredError();
      if (body.code === "spend_cap_reached") {
        throw new SpendCapError(message);
      }
      throw new ApiError(message, 403, body.code ?? null);
    case 404:
      throw new NotFoundError(message);
    case 409:
      throw new ConflictError(message);
    case 413:
      throw new ApiError("File exceeds the maximum allowed size.", 413, "file_too_large");
    case 415:
      throw new ApiError("Unsupported file type or executable file.", 415, "unsupported_type");
    case 422:
      throw new ValidationError(message, body.errors ?? []);
    case 429:
      throw new ApiError(
        "Rate limit reached. Please wait before submitting again.",
        429,
        "rate_limited",
      );
    case 500:
    case 502:
    case 503:
      throw new ApiError("Server error. Check health status.", response.status, "server_error");
    case 507:
      throw new ApiError("Insufficient storage on server.", 507, "insufficient_storage");
    default:
      throw new ApiError(message, response.status, body.code ?? null);
  }
}

export async function jsonGet<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "include",
      redirect: "error",
    });
  } catch {
    // redirect: "error" turns a 303 into a TypeError — the A-01 fallback.
    throw new ApiError(A01_MESSAGE);
  }
  if (response.ok) {
    if (!isJsonContentType(response)) {
      throw new ApiError(
        "Expected JSON, received HTML. The server may not support JSON responses for this route.",
        response.status,
        null,
      );
    }
    return (await response.json()) as T;
  }
  const body = await parseErrorBody(response);
  throwForStatus(response, body);
}

export async function jsonPost<T>(
  path: string,
  body: URLSearchParams,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body,
      credentials: "include",
      redirect: "error",
    });
  } catch {
    throw new ApiError(A01_MESSAGE);
  }
  if (response.ok) {
    if (!isJsonContentType(response)) {
      throw new ApiError(A01_MESSAGE, response.status, null);
    }
    return (await response.json()) as T;
  }
  const parsed = await parseErrorBody(response);
  throwForStatus(response, parsed);
}

export async function loginPost(
  path: string,
  body: URLSearchParams,
): Promise<Response> {
  // Returns the raw Response: the login page renders 401 inline and never
  // throws AuthExpiredError (spec §8.3 loginPost contract).
  return fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
    credentials: "include",
    redirect: "error",
  });
}

export function loginPostIsJson(response: Response): boolean {
  return isJsonContentType(response);
}

interface MultipartOptions {
  onProgress?: (fraction: number) => void;
  abortRef?: { current: (() => void) | null };
}

export function multipartPost<T>(
  path: string,
  formData: FormData,
  options: MultipartOptions = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.withCredentials = true;
    xhr.timeout = UPLOAD_TIMEOUT_MS;
    if (options.abortRef) {
      options.abortRef.current = () => xhr.abort();
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && options.onProgress) {
        options.onProgress(event.loaded / event.total);
      }
    };
    xhr.onload = () => {
      const status = xhr.status;
      // A-01 fallback (D-36): XHR follows redirects silently. Detect a
      // 303-followed or HTML response after completion.
      const requestUrl = new URL(path, window.location.origin);
      let respondedUrl: URL | null = null;
      try {
        if (xhr.responseURL) respondedUrl = new URL(xhr.responseURL);
      } catch {
        respondedUrl = null;
      }
      const ct = xhr.getResponseHeader("Content-Type") || "";
      const redirected =
        respondedUrl !== null && respondedUrl.pathname !== requestUrl.pathname;
      const parseBody = (): {
        error?: string;
        code?: ErrorCode;
        errors?: FieldError[];
        ok?: boolean;
        [key: string]: unknown;
      } => {
        try {
          return JSON.parse(xhr.responseText);
        } catch {
          return {};
        }
      };
      if (status >= 200 && status < 300) {
        if (redirected || !ct.startsWith("application/json")) {
          reject(new ApiError(A01_MESSAGE, status, null));
          return;
        }
        try {
          resolve(JSON.parse(xhr.responseText) as T);
        } catch {
          reject(new ApiError(A01_MESSAGE, status, null));
        }
        return;
      }
      const body = parseBody();
      const message = body.error || xhr.statusText || "Request failed";
      switch (status) {
        case 400:
          reject(new ApiError(message, 400, body.code ?? "bad_request"));
          return;
        case 401:
          reject(new AuthExpiredError(message));
          return;
        case 403:
          if (body.code === "csrf_expired") {
            reject(new CsrfExpiredError());
          } else if (body.code === "spend_cap_reached") {
            reject(new SpendCapError(message));
          } else {
            reject(new ApiError(message, 403, body.code ?? null));
          }
          return;
        case 413:
          reject(new ApiError("File exceeds the maximum allowed size.", 413, "file_too_large"));
          return;
        case 415:
          reject(new ApiError("Unsupported file type or executable file.", 415, "unsupported_type"));
          return;
        case 422:
          reject(new ValidationError(message, body.errors ?? []));
          return;
        case 429: {
          const retryAfter = xhr.getResponseHeader("Retry-After");
          reject(
            new ApiError(
              retryAfter
                ? `Rate limit reached. Retry after ${retryAfter} seconds.`
                : "Rate limit reached. Please wait before submitting again.",
              429,
              "rate_limited",
            ),
          );
          return;
        }
        case 507:
          reject(new ApiError("Insufficient storage on server.", 507, "insufficient_storage"));
          return;
        default:
          reject(new ApiError(message, status, body.code ?? "server_error"));
      }
    };
    xhr.onerror = () => reject(new NetworkError());
    xhr.ontimeout = () => reject(new TimeoutError());
    xhr.onabort = () => reject(new ApiError("Upload cancelled.", 0, null));
    xhr.send(formData);
  });
}

export function readBootstrapFromDom(): Bootstrap {
  const fallback: Bootstrap = {
    authenticated: false,
    csrf_token: null,
    mode: "mock",
  };
  const el = document.getElementById("uapv-bootstrap");
  if (!el || !el.textContent) return fallback;
  try {
    const parsed = JSON.parse(el.textContent) as Partial<Bootstrap>;
    return {
      authenticated: Boolean(parsed.authenticated),
      csrf_token: typeof parsed.csrf_token === "string" ? parsed.csrf_token : null,
      mode: parsed.mode === "live" || parsed.mode === "simulation"
        ? parsed.mode
        : "mock",
    };
  } catch {
    return fallback;
  }
}

// CSRF refresh (spec §3.7): fetch the current URL as a browser-style
// navigation, parse the fresh bootstrap out of the served shell.
export async function refreshCsrfFromShell(): Promise<Bootstrap | null> {
  try {
    const response = await fetch(window.location.href, {
      method: "GET",
      credentials: "include",
    });
    const text = await response.text();
    const doc = new DOMParser().parseFromString(text, "text/html");
    const el = doc.getElementById("uapv-bootstrap");
    if (!el || !el.textContent) return null;
    const parsed = JSON.parse(el.textContent) as Partial<Bootstrap>;
    return {
      authenticated: Boolean(parsed.authenticated),
      csrf_token: typeof parsed.csrf_token === "string" ? parsed.csrf_token : null,
      mode: parsed.mode === "live" || parsed.mode === "simulation"
        ? parsed.mode
        : "mock",
    };
  } catch {
    return null;
  }
}

export function downloadBlob(
  content: string,
  filename: string,
  mimeType: string,
): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
