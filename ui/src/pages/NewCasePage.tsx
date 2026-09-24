// /ui/cases/new — spec §9.3. Client-side media validation runs BEFORE any
// upload; the server-side estimate is read-only; the terms checkbox gates
// submit. Optional metadata lives behind progressive disclosure so the
// visible control count stays ≤ 12.

import { useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button, Card, Disclosure, ErrorBanner, Field, ModeBadge, Skeleton, inputClass } from "../components/ui";
import { fetchIntakeConfig, intakeConfigKey } from "../lib/queries";
import { useAuth } from "../auth/AuthContext";
import {
  ApiError,
  SpendCapError,
  ValidationError,
  multipartPost,
} from "../lib/api";
import type { MutationOk } from "../lib/types";

const ACCEPTED_IMAGE = ["image/jpeg", "image/png", "image/webp"];
const ACCEPTED_VIDEO = ["video/mp4", "video/quicktime", "video/webm"];

const ACCEPT_ATTR = [...ACCEPTED_IMAGE, ...ACCEPTED_VIDEO].join(",");

/** Backend cap (intake.py NOTES_MAX_CHARS). */
const BEHAVIOR_MAX_CHARS = 4000;

/** Mirror of the backend's parse_viewing_direction (intake.py): "az" or
    "az,el" in degrees — azimuth in [0,360), elevation in [-90,90]
    (elevation defaults to 45 when omitted). Returns an error message or
    null. The console never sends a value the backend will 422. */
function validateViewingDirection(value: string): string | null {
  const text = value.trim();
  if (!text) return null;
  const parts = text.split(",").map((part) => part.trim());
  const azimuth = Number(parts[0]);
  if (parts[0] === "" || !Number.isFinite(azimuth)) {
    return "Azimuth must be a number of degrees, e.g. 45 or 45,30.";
  }
  if (!(azimuth >= 0 && azimuth < 360)) {
    return "Azimuth must be in [0, 360).";
  }
  if (parts.length > 1 && parts[1] !== "") {
    const elevation = Number(parts[1]);
    if (!Number.isFinite(elevation)) {
      return "Elevation must be a number of degrees.";
    }
    if (!(elevation >= -90 && elevation <= 90)) {
      return "Elevation must be in [-90, 90].";
    }
  }
  return null;
}

/** Human labels for the inline disabled-submit hint (spec §11: a disabled
    submit shows its reason as visible text, never a tooltip). */
const REQUIRED_LABELS: Record<string, string> = {
  media: "capture media",
  observed_at: "observed at",
  latitude: "latitude",
  longitude: "longitude",
  terms_accepted: "terms acceptance",
};

/** datetime-local value → ISO-8601 WITH offset (backend requires one). */
function toIsoWithOffset(localValue: string): string {
  const date = new Date(localValue);
  if (Number.isNaN(date.getTime())) return localValue;
  const pad = (n: number) => String(Math.abs(n)).padStart(2, "0");
  const offsetMin = -date.getTimezoneOffset();
  const sign = offsetMin >= 0 ? "+" : "-";
  const yyyy = date.getFullYear();
  const mm = pad(date.getMonth() + 1);
  const dd = pad(date.getDate());
  const hh = pad(date.getHours());
  const mi = pad(date.getMinutes());
  const ss = pad(date.getSeconds());
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}${sign}${pad(
    Math.floor(Math.abs(offsetMin) / 60),
  )}:${pad(Math.abs(offsetMin) % 60)}`;
}

export function NewCasePage() {
  const navigate = useNavigate();
  const { csrfToken } = useAuth();
  const { data: config, isError, isLoading } = useQuery({
    queryKey: intakeConfigKey,
    queryFn: fetchIntakeConfig,
  });

  const [file, setFile] = useState<File | null>(null);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const recentHashes = useRef<Set<string>>(new Set());

  const [observedAt, setObservedAt] = useState("");
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [locationText, setLocationText] = useState("");
  const [viewingDirection, setViewingDirection] = useState("");
  const [shape, setShape] = useState("");
  const [count, setCount] = useState("");
  const [durationSeconds, setDurationSeconds] = useState("");
  const [weather, setWeather] = useState("");
  const [behaviorNotes, setBehaviorNotes] = useState("");
  const [buyerRef, setBuyerRef] = useState("");
  const [paymentStatus, setPaymentStatus] = useState("unpaid");
  const [termsAccepted, setTermsAccepted] = useState(false);

  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const abortRef = useRef<(() => void) | null>(null);

  const caps = config?.media_caps;

  const validateAndSetFile = async (candidate: File | null) => {
    setMediaError(null);
    setFile(null);
    if (!candidate || !caps) return;
    const isVideo = ACCEPTED_VIDEO.includes(candidate.type);
    const isImage = ACCEPTED_IMAGE.includes(candidate.type);
    if (!isVideo && !isImage) {
      setMediaError("Unsupported file type. Accepted: JPEG/PNG/WebP, MP4/MOV/WebM.");
      return;
    }
    const sizeMb = candidate.size / (1024 * 1024);
    if (isImage && sizeMb > caps.image_mb) {
      setMediaError(`Image exceeds the ${caps.image_mb} MB cap.`);
      return;
    }
    if (isVideo && sizeMb > caps.video_mb) {
      setMediaError(`Video exceeds the ${caps.video_mb} MB cap.`);
      return;
    }
    if (isImage) {
      try {
        const bitmap = await createImageBitmap(candidate);
        if (bitmap.width * bitmap.height > caps.image_mp * 1_000_000) {
          bitmap.close();
          setMediaError(
            `Image exceeds the ${caps.image_mp} megapixel cap (${bitmap.width}×${bitmap.height}).`,
          );
          return;
        }
        bitmap.close();
      } catch {
        setMediaError("Cannot decode this image file.");
        return;
      }
    }
    if (isVideo) {
      const duration = await new Promise<number | null>((resolve) => {
        const url = URL.createObjectURL(candidate);
        const video = document.createElement("video");
        video.preload = "metadata";
        video.onloadedmetadata = () => {
          const seconds = video.duration;
          URL.revokeObjectURL(url);
          resolve(Number.isFinite(seconds) ? seconds : null);
        };
        video.onerror = () => {
          URL.revokeObjectURL(url);
          resolve(null);
        };
        video.src = url;
      });
      if (duration === null) {
        setMediaError("Cannot read this video file's metadata.");
        return;
      }
      if (duration > caps.video_max_s) {
        setMediaError(
          `Video is ${duration.toFixed(0)}s — the cap is ${caps.video_max_s}s.`,
        );
        return;
      }
    }
    // Duplicate pre-check (session-local). Skip for large files — reading
    // a 250 MB video into memory just to hash it is not worth it; the
    // server still deduplicates by content hash, which is authoritative.
    if (candidate.size <= 20 * 1024 * 1024) {
      setChecking(true);
      try {
        const digest = await crypto.subtle.digest(
          "SHA-256",
          await candidate.arrayBuffer(),
        );
        const hash = Array.from(new Uint8Array(digest))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        if (recentHashes.current.has(hash)) {
          setMediaError("This exact file was already submitted in this session.");
          return;
        }
        recentHashes.current.add(hash);
      } catch {
        // crypto.subtle unavailable (non-secure context): skip the
        // duplicate pre-check; the server still deduplicates by hash.
      } finally {
        setChecking(false);
      }
    }
    setFile(candidate);
  };

  const requiredMissing = useMemo(() => {
    const missing: string[] = [];
    if (!file) missing.push("media");
    if (!observedAt) missing.push("observed_at");
    if (latitude === "") missing.push("latitude");
    if (longitude === "") missing.push("longitude");
    if (!termsAccepted) missing.push("terms_accepted");
    return missing;
  }, [file, observedAt, latitude, longitude, termsAccepted]);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    if (!file) {
      setMediaError("Choose a media file first.");
      return;
    }
    setFieldErrors({});
    const directionError = validateViewingDirection(viewingDirection);
    if (directionError) {
      setFieldErrors({ viewing_direction: directionError });
      return;
    }
    setSubmitting(true);
    setProgress(0);
    const form = new FormData();
    form.append("csrf_token", csrfToken ?? "");
    form.append("media", file);
    form.append("observed_at", toIsoWithOffset(observedAt));
    form.append("latitude", latitude);
    form.append("longitude", longitude);
    if (locationText) form.append("location_text", locationText);
    if (viewingDirection) form.append("viewing_direction", viewingDirection);
    if (shape) form.append("shape", shape);
    if (count) form.append("count", count);
    if (durationSeconds) form.append("duration_seconds", durationSeconds);
    if (weather) form.append("weather", weather);
    if (behaviorNotes) form.append("behavior_notes", behaviorNotes);
    if (buyerRef) form.append("buyer_ref", buyerRef);
    form.append("payment_status", paymentStatus);
    form.append("terms_accepted", "true");
    try {
      const result = await multipartPost<MutationOk>("/cases/new", form, {
        onProgress: (fraction) => setProgress(Math.round(fraction * 100)),
        abortRef,
      });
      const caseId = String(result.case_id ?? "");
      if (!caseId) {
        toast("Submission accepted, but no case ID was returned.");
        navigate("/ui/cases");
        return;
      }
      navigate(`/ui/cases/${caseId}`);
    } catch (error) {
      if (error instanceof SpendCapError) {
        toast(error.message);
      } else if (error instanceof ValidationError) {
        const mapped: Record<string, string> = {};
        for (const item of error.errors) mapped[item.field] = item.message;
        setFieldErrors(mapped);
        if (mapped.media) setMediaError(mapped.media);
      } else if (error instanceof ApiError) {
        setMediaError(null);
        toast(error.message);
      } else {
        toast("Submission failed. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex max-w-2xl flex-col gap-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }
  if (isError || !config) {
    return (
      <ErrorBanner
        message="Intake configuration could not be loaded."
        hint="Cannot reach server. Check that `uapvf serve` is running, then reload."
      />
    );
  }

  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <header className="border-b border-line pb-4">
        <h1 className="font-display text-2xl font-semibold text-ink">New Case</h1>
        <p className="mt-1 text-xs text-ink-3">
          Every case is billed. The estimate below is an upper bound.
        </p>
      </header>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5">
        {/* Section 1 — media */}
        <Card className="flex flex-col gap-3 p-5">
          <Field
            label="Capture media"
            htmlFor="intake-media"
            required
            hint={`JPEG/PNG/WebP up to ${caps?.image_mb ?? 50} MB and ${caps?.image_mp ?? 20} MP, or MP4/MOV/WebM up to ${caps?.video_mb ?? 250} MB and ${caps?.video_max_s ?? 60}s.`}
            error={mediaError}
          >
            {/* Drag-and-drop zone wrapping the native input (spec §9.3 —
                no drag-and-drop library, §12). A drop runs the same
                pre-upload validation as the chooser before any network
                transfer. Drag-over state: dashed border steps to accent. */}
            <div
              onDragEnter={(event) => {
                event.preventDefault();
                dragDepth.current += 1;
                setDragOver(true);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => {
                event.preventDefault();
                dragDepth.current = Math.max(0, dragDepth.current - 1);
                if (dragDepth.current === 0) setDragOver(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                dragDepth.current = 0;
                setDragOver(false);
                void validateAndSetFile(event.dataTransfer.files?.[0] ?? null);
              }}
              className={`flex flex-col gap-2 rounded-md border border-dashed p-3 transition-colors duration-[var(--dur-fast)] ${
                dragOver ? "border-accent bg-inset" : "border-line-2"
              }`}
            >
              <input
                id="intake-media"
                type="file"
                accept={ACCEPT_ATTR}
                onChange={(event) => {
                  void validateAndSetFile(event.target.files?.[0] ?? null);
                  event.target.value = "";
                }}
                aria-labelledby="intake-media-label"
                aria-required="true"
                aria-invalid={mediaError ? true : undefined}
                aria-describedby={mediaError ? "intake-media-error" : undefined}
                className="block w-full cursor-pointer rounded-md border border-line-2 bg-inset text-xs text-ink-2 file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-accent file:px-3 file:py-2 file:text-xs file:font-medium file:text-accent-ink hover:file:bg-accent-hover"
              />
              <p className="text-xs text-ink-3">
                Drop one file here, or use the chooser. One media file per case.
              </p>
            </div>
          </Field>
          {checking && <p className="text-xs text-ink-3">Checking file…</p>}
          {file && !mediaError && (
            <p className="text-xs text-ink-2">
              Selected: <span className="font-mono">{file.name}</span>{" "}
              <span className="text-ink-3 tnum">
                ({(file.size / (1024 * 1024)).toFixed(1)} MB)
              </span>
            </p>
          )}
        </Card>

        {/* Section 2 — required metadata */}
        <Card className="flex flex-col gap-4 p-5">
          <Field
            label="Observed at"
            htmlFor="observed-at"
            required
            hint="Local capture time; sent with your UTC offset."
            error={fieldErrors.observed_at}
          >
            <input
              id="observed-at"
              type="datetime-local"
              value={observedAt}
              onChange={(event) => setObservedAt(event.target.value)}
              aria-labelledby="observed-at-label"
              aria-required="true"
              aria-invalid={fieldErrors.observed_at ? true : undefined}
              aria-describedby={fieldErrors.observed_at ? "observed-at-error" : undefined}
              className={inputClass(Boolean(fieldErrors.observed_at))}
            />
          </Field>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Latitude"
              htmlFor="latitude"
              required
              error={fieldErrors.latitude}
            >
              <input
                id="latitude"
                type="number"
                inputMode="decimal"
                step="any"
                min={-90}
                max={90}
                placeholder="32.2217"
                value={latitude}
                onChange={(event) => setLatitude(event.target.value)}
                aria-labelledby="latitude-label"
                aria-required="true"
                aria-invalid={fieldErrors.latitude ? true : undefined}
                aria-describedby={fieldErrors.latitude ? "latitude-error" : undefined}
                className={inputClass(Boolean(fieldErrors.latitude))}
              />
            </Field>
            <Field
              label="Longitude"
              htmlFor="longitude"
              required
              error={fieldErrors.longitude}
            >
              <input
                id="longitude"
                type="number"
                inputMode="decimal"
                step="any"
                min={-180}
                max={180}
                placeholder="-110.9265"
                value={longitude}
                onChange={(event) => setLongitude(event.target.value)}
                aria-labelledby="longitude-label"
                aria-required="true"
                aria-invalid={fieldErrors.longitude ? true : undefined}
                aria-describedby={fieldErrors.longitude ? "longitude-error" : undefined}
                className={inputClass(Boolean(fieldErrors.longitude))}
              />
            </Field>
          </div>
          <Field
            label="Viewing direction"
            htmlFor="viewing-direction"
            optional
            hint="Azimuth, or azimuth,elevation in degrees — e.g. 45,30. This is strongly recommended."
            error={fieldErrors.viewing_direction}
          >
            <input
              id="viewing-direction"
              type="text"
              placeholder="e.g. 45,30"
              value={viewingDirection}
              onChange={(event) => {
                setViewingDirection(event.target.value);
                if (fieldErrors.viewing_direction) {
                  setFieldErrors((prev) => {
                    const next = { ...prev };
                    delete next.viewing_direction;
                    return next;
                  });
                }
              }}
              aria-labelledby="viewing-direction-label"
              aria-invalid={fieldErrors.viewing_direction ? true : undefined}
              aria-describedby={
                fieldErrors.viewing_direction
                  ? "viewing-direction-error"
                  : "viewing-direction-impact"
              }
              className={inputClass(Boolean(fieldErrors.viewing_direction))}
            />
          </Field>
          {!viewingDirection.trim() && (
            <div
              id="viewing-direction-impact"
              role="status"
              className="rounded-md border border-st-run bg-st-run-tint px-3 py-2 text-xs leading-relaxed text-st-run"
            >
              Without a viewing direction, satellite and astronomical matching
              will be marked insufficient. You can still submit the case; the
              report will show those coverage gaps explicitly.
            </div>
          )}
        </Card>

        {/* Section 3 — optional metadata (progressive disclosure) */}
        <Disclosure summary="Optional observation details">
          <div className="flex flex-col gap-4">
            <Field label="Location description" htmlFor="location-text" optional>
              <input
                id="location-text"
                type="text"
                value={locationText}
                onChange={(event) => setLocationText(event.target.value)}
                aria-labelledby="location-text-label"
                className={inputClass()}
              />
            </Field>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {/* DESIGN.md §11.6: the backend consumes exactly one direction
                  input — `viewing_direction` (intake.py:180-184). There is NO
                  `direction` field server-side, so no direction select ships:
                  a control whose value the backend silently drops is a lying
                  control (SOUL ¶1). Spec §9.3's idealized select was
                  adjudicated against the live backend and lost. */}
              {/* Reported shape — backend contract (web_json.py:413-415):
                  `field_options` ships empty lists, and "empty option lists
                  tell the SPA to render plain inputs (no invented enums)".
                  The select exists ONLY when the server supplies options;
                  today that list is empty, so Elias gets free text ("disc",
                  "orb") — which intake.py:195 accepts verbatim. */}
              {(config.field_options.shape ?? []).length > 0 ? (
                <Field label="Reported shape" htmlFor="shape" optional>
                  <select
                    id="shape"
                    value={shape}
                    onChange={(event) => setShape(event.target.value)}
                    aria-labelledby="shape-label"
                    className={`${inputClass()} cursor-pointer`}
                  >
                    <option value="">—</option>
                    {config.field_options.shape.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : (
                <Field label="Reported shape" htmlFor="shape" optional>
                  <input
                    id="shape"
                    type="text"
                    placeholder="e.g. disc, orb"
                    value={shape}
                    onChange={(event) => setShape(event.target.value)}
                    aria-labelledby="shape-label"
                    className={inputClass()}
                  />
                </Field>
              )}
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Object count" htmlFor="count" optional>
                <input
                  id="count"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  value={count}
                  onChange={(event) => setCount(event.target.value)}
                  aria-labelledby="count-label"
                  className={inputClass()}
                />
              </Field>
              <Field label="Duration (seconds)" htmlFor="duration-seconds" optional>
                <input
                  id="duration-seconds"
                  type="number"
                  inputMode="decimal"
                  step="any"
                  min={0}
                  value={durationSeconds}
                  onChange={(event) => setDurationSeconds(event.target.value)}
                  aria-labelledby="duration-seconds-label"
                  className={inputClass()}
                />
              </Field>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Weather" htmlFor="weather" optional>
                <input
                  id="weather"
                  type="text"
                  value={weather}
                  onChange={(event) => setWeather(event.target.value)}
                  aria-labelledby="weather-label"
                  className={inputClass()}
                />
              </Field>
              <Field label="Buyer reference" htmlFor="buyer-ref" optional>
                <input
                  id="buyer-ref"
                  type="text"
                  value={buyerRef}
                  onChange={(event) => setBuyerRef(event.target.value)}
                  aria-labelledby="buyer-ref-label"
                  className={inputClass()}
                />
              </Field>
            </div>
            <Field label="Payment status" htmlFor="payment-status" optional>
              <select
                id="payment-status"
                value={paymentStatus}
                onChange={(event) => setPaymentStatus(event.target.value)}
                aria-labelledby="payment-status-label"
                className={inputClass()}
              >
                <option value="unpaid">Unpaid</option>
                <option value="paid">Paid</option>
                <option value="comped">Comped</option>
              </select>
            </Field>
          </div>
        </Disclosure>

        {/* Section 4 — terms + cost + submit */}
        <Card className="flex flex-col gap-4 p-5">
          <Field
            label="Behavior notes"
            htmlFor="behavior-notes"
            optional
            hint="Free text; carried into the report verbatim."
            error={fieldErrors.behavior_notes}
          >
            <textarea
              id="behavior-notes"
              rows={3}
              maxLength={BEHAVIOR_MAX_CHARS}
              value={behaviorNotes}
              onChange={(event) => setBehaviorNotes(event.target.value)}
              aria-labelledby="behavior-notes-label"
              aria-invalid={fieldErrors.behavior_notes ? true : undefined}
              aria-describedby={
                fieldErrors.behavior_notes ? "behavior-notes-error" : undefined
              }
              className="w-full rounded-md border border-line-2 bg-inset px-3 py-2 text-[var(--text-base-mobile)] text-ink placeholder:text-ink-disabled hover:border-line-3 focus:border-focus sm:text-sm"
            />
            {/* Char count (spec §9.3) — same cap the backend enforces. */}
            <span className="self-end text-xs text-ink-3 tnum" aria-live="polite">
              {behaviorNotes.length} / {BEHAVIOR_MAX_CHARS} characters
            </span>
          </Field>
          <div className="rounded-md bg-inset px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-ink-3">
                Estimated cost — server-provided, not editable
              </p>
              {/* UI-58 / spec §7.5: which cost schedule is active. */}
              <ModeBadge mode={config.cost_mode} />
            </div>
            <p className="mt-1 text-sm font-medium text-ink tnum">
              {config.estimate_text}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <div className="flex items-start gap-2.5">
              <input
                id="terms-checkbox"
                type="checkbox"
                checked={termsAccepted}
                onChange={(event) => setTermsAccepted(event.target.checked)}
                aria-required="true"
                aria-invalid={fieldErrors.terms_accepted ? true : undefined}
                aria-describedby="terms-checkbox-hint"
                className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
              />
              <label htmlFor="terms-checkbox" className="text-xs leading-relaxed text-ink-2">
                I accept the terms of service. {config.terms_short}{" "}
                <Link
                  to="/ui/terms"
                  className="underline decoration-line-3 underline-offset-2 hover:text-ink"
                >
                  Full terms
                </Link>
                .
              </label>
            </div>
            <p id="terms-checkbox-hint" className="text-xs text-ink-3">
              Submission is blocked until the terms are accepted.
            </p>
          </div>
          {fieldErrors.terms_accepted && (
            <p role="alert" className="text-xs text-st-bad">
              {fieldErrors.terms_accepted}
            </p>
          )}

          {submitting && (
            <div className="flex flex-col gap-1.5">
              <div
                className="h-1.5 w-full overflow-hidden rounded-full bg-inset"
                role="progressbar"
                aria-label="Upload progress"
                aria-valuenow={progress}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="h-full w-full origin-left rounded-full bg-accent transition-transform duration-[var(--dur-base)]"
                  style={{ transform: `scaleX(${progress / 100})` }}
                />
              </div>
              <p className="text-xs text-ink-3 tnum">Uploading… {progress}%</p>
            </div>
          )}

          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <Button
                type="submit"
                loading={submitting}
                loadingText="Submitting…"
                disabled={requiredMissing.length > 0}
              >
                Submit case
              </Button>
              {submitting && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    abortRef.current?.();
                  }}
                >
                  Cancel
                </Button>
              )}
            </div>
            {requiredMissing.length > 0 && !submitting && (
              <p className="text-xs text-ink-3">
                Required before submit:{" "}
                {requiredMissing
                  .map((name) => REQUIRED_LABELS[name] ?? name)
                  .join(", ")}
                .
              </p>
            )}
          </div>
        </Card>
      </form>
    </div>
  );
}
