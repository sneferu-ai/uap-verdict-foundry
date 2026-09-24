// Query keys + fetch functions (spec §8.2 key naming).

import { jsonGet } from "./api";
import type {
  BenchmarkResponse,
  CaseDetailResponse,
  CaseListResponse,
  DeleteConfirmContext,
  FictionResponse,
  IntakeConfig,
  ReportJson,
  TermsResponse,
} from "./types";

export const caseListKey = (params: {
  status?: string;
  offset?: number;
  limit?: number;
}) => ["cases", params];

export function fetchCaseList(params: {
  status?: string;
  offset?: number;
  limit?: number;
}): Promise<CaseListResponse> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  search.set("limit", String(params.limit ?? 20));
  if (params.offset) search.set("offset", String(params.offset));
  return jsonGet<CaseListResponse>(`/cases?${search.toString()}`);
}

export const caseDetailKey = (caseId: string) => ["case", caseId];

export function fetchCaseDetail(caseId: string): Promise<CaseDetailResponse> {
  return jsonGet<CaseDetailResponse>(`/cases/${encodeURIComponent(caseId)}`);
}

export const reportKey = (caseId: string) => ["report", caseId];

export function fetchReport(caseId: string): Promise<ReportJson> {
  return jsonGet<ReportJson>(
    `/cases/${encodeURIComponent(caseId)}/report`,
  );
}

export const fictionKey = (caseId: string) => ["fiction", caseId];

export function fetchFiction(caseId: string): Promise<FictionResponse> {
  return jsonGet<FictionResponse>(
    `/cases/${encodeURIComponent(caseId)}/fiction`,
  );
}

export const deleteContextKey = (caseId: string) => [
  "delete-context",
  caseId,
];

export function fetchDeleteContext(
  caseId: string,
): Promise<DeleteConfirmContext> {
  return jsonGet<DeleteConfirmContext>(
    `/cases/${encodeURIComponent(caseId)}/delete/confirm`,
  );
}

export const intakeConfigKey = ["intake-config"];

export function fetchIntakeConfig(): Promise<IntakeConfig> {
  return jsonGet<IntakeConfig>("/cases/new");
}

export const benchmarkKey = ["benchmark"];

export function fetchBenchmark(): Promise<BenchmarkResponse> {
  return jsonGet<BenchmarkResponse>("/benchmark");
}

export const termsKey = ["terms"];

export function fetchTerms(): Promise<TermsResponse> {
  return jsonGet<TermsResponse>("/terms");
}
