// UI spec §7 data contracts — the wire shapes the backend JSON branches
// actually return. Honest deviations from the spec's idealized contract
// (documented in the round summary): LineageOutput carries
// artifact_detected/hypothesis (the real lineage_outputs columns) rather
// than the spec's artifact_group_fraction/detectors, which do not exist in
// the schema; cases.agreement_fraction carries the group fraction instead.

export type CaseStatus =
  | "queued"
  | "analyzing"
  | "verdict_ready"
  | "complete"
  | "failed"
  | "spend_capped"
  | "rerun_requested";

export type Verdict =
  | "no_mundane_match"
  | "mundane_identified"
  | "insufficient_data"
  | null;

export type PaymentStatus = "unpaid" | "paid" | "comped";

export type CategoryResult = "positive" | "negative" | "insufficient";

export type UapvMode = "mock" | "simulation" | "live";

export type ErrorCode =
  | "auth_failed"
  | "auth_expired"
  | "csrf_expired"
  | "spend_cap_reached"
  | "conflict"
  | "validation_error"
  | "file_too_large"
  | "unsupported_type"
  | "rate_limited"
  | "insufficient_storage"
  | "bad_request"
  | "not_found"
  | "server_error";

export interface Bootstrap {
  authenticated: boolean;
  csrf_token: string | null;
  mode: UapvMode;
}

// Sneferu accepted-product launcher bootstrap record (optional preview
// identity; consumed when present, never required).
export interface PreviewBootstrap {
  username?: string;
  role?: string;
  session_token?: string | null;
}

export interface CaseSummary {
  case_id: string;
  created_at: string;
  status: CaseStatus;
  verdict: Verdict;
  buyer_ref: string | null;
  payment_status: PaymentStatus;
  quality_score: number | null;
  agreement_fraction: number | null;
  retention_exceeded: boolean;
}

export interface SpendSummary {
  month: string;
  total_usd: number;
  spend_cap_usd: number;
  remaining_usd: number;
  lineage_calls: number;
  fiction_calls: number;
  lineage_cost_usd: number;
  fiction_cost_usd: number;
}

export interface CaseListResponse {
  cases: CaseSummary[];
  total: number;
  retention_days: number;
  spend: SpendSummary;
  estimated_cost_usd: number;
  estimated_benchmark_cost_usd: number | null;
}

export interface CaseDetail extends CaseSummary {
  observed_at: string;
  latitude: number;
  longitude: number;
  location_text: string | null;
  verdict_text: string | null;
  uncertainty: number | null;
  uncertainty_calibrated: 0 | 1;
  uncertainty_reason: string | null;
  calibration_source: string | null;
  quality_gate_pass: 0 | 1 | null;
  lineage_count: number | null;
  retry_count: number;
  run_version: number;
  current_stage: string | null;
  error_detail: string | null;
  report_available: boolean;
  fiction_available: boolean;
  fiction_withheld: boolean;
  // Backend ground truth (round 6, per reviewer P1-polish): the case row
  // is always served `settings.estimated_case_cost_usd()` — an
  // unconditional float (config.py:85-90, no DB column, no null path).
  // The honest null lives on `estimated_benchmark_cost_usd`
  // (web_json.py:124-128, D-41), not here; typing a state the backend
  // cannot produce was the same invented-capability class as the
  // round-5 direction select (DESIGN.md §11.6).
  estimated_cost_usd: number;
}

export interface StageRun {
  stage_name: string;
  run_version: number;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  error_detail: string | null;
}

export interface BatteryResult {
  category: string;
  result: CategoryResult;
  evidence_citation: string | null;
  source_stamp_json: string;
  recorded_at: string;
}

export interface LineageOutput {
  lineage_id: string;
  classification: string | null;
  artifact_detected: number | null;
  hypothesis: string | null;
  status: string;
  confidence: number | null;
  evidence_claims: Array<Record<string, unknown> | string> | Record<string, unknown>;
  provenance: Record<string, unknown>;
  abstention_reason: string | null;
}

export interface AnalysisContract {
  lineage_runtime: "live" | "simulation" | "mock" | string;
  expected_lineages: number;
  observed_lineages?: number;
  sandbox_verified: boolean;
  coverage: {
    expected_categories: string[];
    tested_categories: string[];
    insufficient_categories: string[];
    complete: boolean;
  } | null;
  rigor: {
    runtime: string;
    agreement_fraction: number;
    population_variance: number;
    plurality: string | null;
    plurality_count: number;
    conditions: Record<string, boolean>;
    refutation: Record<string, unknown>;
    passed: boolean;
    reason: string;
  } | null;
}

export interface Warning {
  code: string;
  message: string;
}

export interface AuditEvent {
  entry_hash: string;
  case_id: string | null;
  actor: string;
  action: string;
  detail: string;
  recorded_at: string;
}

export interface CaseDetailResponse {
  case: CaseDetail;
  stages: StageRun[];
  battery_results: BatteryResult[];
  lineage_outputs: LineageOutput[];
  analysis_contract: AnalysisContract;
  // Round 4: the removed phase-2 surfaces (xenoscience / story assets /
  // buyer-token delivery) no longer project into the detail payload —
  // the console must not ship dead controls for deleted endpoints.
  warnings: Warning[];
  audit_events: AuditEvent[];
  total_lineage_outputs: number;
  total_warnings: number;
  total_audit_events: number;
  downloads: {
    report_available: boolean;
    fiction_available: boolean;
    fiction_withheld: boolean;
  };
}

export interface IntakeConfig {
  estimate_text: string;
  estimated_cost_usd: number;
  cost_mode: UapvMode;
  expected_lineage_count: number;
  terms_short: string;
  media_caps: {
    image_mb: number;
    video_mb: number;
    image_mp: number;
    video_max_s: number;
  };
  field_options: {
    direction: string[];
    shape: string[];
  };
  estimated_benchmark_cost_usd: number | null;
}

export interface SourceStamp {
  source_id: string;
  source_version: string;
  utc_timestamp: string;
  query_params: string;
  content_hash: string;
  reason?: string;
}

export interface ReportBatteryResult {
  category: string;
  result: CategoryResult;
  evidence_citation: string | null;
  source_stamp: SourceStamp;
}

export interface ReportLineageOutput {
  lineage_id: string;
  classification: string | null;
  artifact_detected: boolean;
  hypothesis: string | null;
  status?: string;
  confidence?: number | null;
  evidence_claims?: Array<Record<string, unknown> | string> | Record<string, unknown>;
  provenance?: Record<string, unknown>;
  abstention_reason?: string | null;
}

export interface ReportUncertainty {
  value: number | null;
  raw_confidence: number | null;
  calibrated: boolean;
  calibration_source: string | null;
  uncertainty_reason: string | null;
  low_confidence: boolean;
  penalty: number;
}

// The backend's report.json sidecar shape (verified against the live
// server). The spec §7.6 idealized fields evidence_section /
// interpretation_section / disclaimer / report_sha256 do not exist in the
// real sidecar; the report page renders verdict_text as the interpretive
// block and media_sha256 from provenance as the integrity reference.
export interface ReportJson {
  case_id: string;
  verdict: Verdict;
  verdict_text: string;
  uncertainty: ReportUncertainty;
  battery_results: ReportBatteryResult[];
  lineage_outputs: ReportLineageOutput[];
  warnings: Warning[];
  quality_score: number | null;
  quality_gate_pass: 0 | 1 | null;
  agreement_fraction: number | null;
  provenance: {
    media_sha256: string | null;
    latitude: number;
    longitude: number;
    observed_at: string;
  };
  software_version: string;
  generated_at: string;
  analysis_contract?: AnalysisContract & {
    reference_integrity?: {
      mode: string;
      valid: boolean;
      failures: string[];
      banner: string | null;
    };
  };
}

export interface FictionResponse {
  content: string | null;
  available: boolean;
  withheld: boolean;
  verdict: Verdict;
}

export interface BenchmarkRun {
  run_id: string;
  started_at: string;
  completed_at: string | null;
  mode: UapvMode;
  metrics: {
    per_category_recall: Record<string, number>;
    false_no_mundane_match_rate: number | null;
    insufficient_detection_rate: number | null;
  } | null;
  error: string | null;
}

export interface BenchmarkProgress {
  completed: number;
  total: number;
  done: boolean;
  error: string | null;
}

export interface BenchmarkResponse {
  latest_run: BenchmarkRun | null;
  progress: BenchmarkProgress | null;
  prereg: {
    min_per_category_recall: number;
    max_false_no_mundane_match_rate: number;
    min_insufficient_detection_rate: number;
    prereg_hash: string | null;
  } | null;
  estimated_benchmark_cost_usd: number | null;
}

export interface DeleteConfirmContext {
  case_id: string;
  status: CaseStatus;
  verdict: Verdict;
  created_at: string;
  buyer_ref: string | null;
}

export interface TermsResponse {
  terms_text: string;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  [key: string]: unknown;
}

export interface ReadinessResponse {
  ready: boolean;
  mode?: UapvMode;
  references?: {
    mode: string;
    valid: boolean;
    failures: string[];
    banner: string | null;
  };
  [key: string]: unknown;
}

export interface MutationOk {
  ok: true;
  [key: string]: unknown;
}

export interface FieldError {
  field: string;
  message: string;
}

export const ACTIVE_POLL_STATUSES: ReadonlySet<CaseStatus> = new Set([
  "queued",
  "analyzing",
  "verdict_ready",
  "rerun_requested",
]);
