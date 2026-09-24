// Preview-launcher bootstrap (orchestrator acceptance contract).
//
// Sneferu's accepted-product launcher may establish ONE versioned client
// bootstrap record at localStorage["sneferu.preview.bootstrap.v1"]. When
// present, the app initializes the same authenticated client state the
// normal login flow uses (username, role, optional session_token). When
// absent, normal deployed login is untouched.

import type { PreviewBootstrap } from "./types";

export const PREVIEW_BOOTSTRAP_KEY = "sneferu.preview.bootstrap.v1";

export function readPreviewBootstrap(): PreviewBootstrap | null {
  try {
    // Literal key on purpose: the preview-bootstrap contract is this exact
    // read, consumed verbatim (the launcher writes it; we never invent it).
    const raw = window.localStorage.getItem("sneferu.preview.bootstrap.v1");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PreviewBootstrap;
    if (typeof parsed !== "object" || parsed === null) return null;
    return {
      username:
        typeof parsed.username === "string" && parsed.username.length > 0
          ? parsed.username
          : undefined,
      role:
        typeof parsed.role === "string" && parsed.role.length > 0
          ? parsed.role
          : undefined,
      session_token:
        typeof parsed.session_token === "string"
          ? parsed.session_token
          : undefined,
    };
  } catch {
    return null;
  }
}
