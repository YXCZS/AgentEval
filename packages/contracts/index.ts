export type JsonValue =
  | Record<string, unknown>
  | JsonValue[]
  | string
  | number
  | boolean
  | null;

export type AgentType = "rag" | "tool" | "custom";
export type RunStatus =
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";
export type ScoreStatus = "passed" | "failed" | "missing" | "error" | "not_run";
export type AnnotationStatus = "pending" | "in_review" | "completed" | "skipped";

export interface HealthResponse {
  status: "ok";
  environment: "development" | "test" | "production";
}

export interface AccessCheckResponse {
  project_id: string;
  principal_type: "browser" | "agent" | "ci";
}

export interface DatasetCase {
  id: string;
  input: JsonValue;
  variables: Record<string, unknown>;
  expected_output?: JsonValue;
  output_schema?: Record<string, unknown>;
  criteria: string[];
  expected_tools: Array<{
    name: string;
    arguments: Record<string, unknown>;
    order?: number;
  }>;
  expected_state?: Record<string, unknown>;
  retrieval_context: Array<{
    content: string;
    document_id?: string;
    metadata: Record<string, unknown>;
  }>;
  messages: Array<{
    role: "system" | "user" | "assistant" | "tool";
    content: JsonValue;
    name?: string;
  }>;
  metadata: Record<string, unknown>;
  source_trace_id?: string;
}

export interface Score {
  id: string;
  run_id: string;
  case_id: string;
  metric_name: string;
  evaluator_version_id: string;
  trace_id?: string | null;
  status: ScoreStatus;
  value?: number;
  label?: string;
  passed?: boolean;
  explanation?: string;
  evidence: Array<Record<string, unknown>>;
  direction: "higher_is_better" | "lower_is_better";
}

export interface AnnotationQueue {
  id: string;
  project_id: string;
  name: string;
  description?: string | null;
  evaluator_version_id: string;
  created_at: string;
}

export interface AnnotationQueueItem {
  id: string;
  queue_id: string;
  run_id: string;
  case_id: string;
  trace_id?: string | null;
  status: AnnotationStatus;
  created_at: string;
  completed_at?: string | null;
}

export interface HumanScoreAudit {
  id: string;
  score_id: string;
  action: "created" | "updated";
  reviewer: string;
  previous_value?: Record<string, JsonValue> | null;
  new_value: Record<string, JsonValue>;
  created_at: string;
}

export interface AggregateMetric {
  metric_name: string;
  evaluator_version_id: string;
  valid_count: number;
  missing_count: number;
  error_count: number;
  passed_count: number;
  average?: number | null;
  pass_rate?: number | null;
  aggregation: "mean" | "pass_rate" | "sum" | "min" | "max";
  threshold?: number | null;
  direction: "higher_is_better" | "lower_is_better";
}

export interface EvaluationReportCase {
  case_id: string;
  metadata: Record<string, JsonValue>;
  execution_status: "queued" | "running" | "completed" | "failed" | "cancelled";
  error_type?: string | null;
  error_message?: string | null;
  output?: JsonValue;
  trace_id?: string | null;
  scores: Score[];
}

export interface EvaluationReport {
  run_id: string;
  status: RunStatus;
  total_cases: number;
  matched_cases: number;
  filters: Record<string, JsonValue>;
  metrics: AggregateMetric[];
  cases: EvaluationReportCase[];
  generated_at: string;
}
