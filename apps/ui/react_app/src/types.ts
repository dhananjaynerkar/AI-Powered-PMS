export type CaseState =
  | "draft"
  | "submitted_to_no"
  | "returned_to_do"
  | "verified_by_no"
  | "submitted_to_hod"
  | "returned_to_no"
  | "approved"
  | "rejected"
  | "escalated"
  | "closed";

export interface CaseRecord {
  case_id: string;
  thread_id: string;
  title: string;
  objective: string;
  state: CaseState;
  current_owner_subject: string;
  current_owner_role: string;
  updated_at: string;
}

export interface CaseMessage {
  message_id: string;
  sequence_number: number;
  author_subject: string;
  author_role: string;
  body: string;
  supersedes_message_id: string | null;
  created_at: string;
}

export interface CaseTransition {
  transition_id: string;
  from_state: CaseState;
  to_state: CaseState;
  actor_subject: string;
  actor_role: string;
  remarks: string;
  occurred_at: string;
}

export interface ContextCapsule {
  version: number;
  current_state: CaseState;
  objective: string;
  rolling_summary: string;
  verified_facts: string[];
  unresolved_issues: string[];
  decisions: Array<{
    decision_id: string;
    outcome: string;
    rationale: string;
  }>;
  open_tasks: Array<{
    task_id: string;
    title: string;
    status: string;
  }>;
  evidence: Array<{
    reference_type: string;
    reference_id: string;
    version: string | null;
  }>;
  artifact_versions: Array<{
    artifact_id: string;
    version: number;
    review_status: string;
  }>;
  required_next_action: string;
  state_hash: string;
}

export interface CaseTimeline {
  case: CaseRecord;
  messages: CaseMessage[];
  transitions: CaseTransition[];
  capsules: ContextCapsule[];
}

export interface Me {
  subject: string;
  roles: string[];
  tenant_id: string | null;
  department_id: string | null;
  unit_id: string | null;
  classification: string;
}

export interface SourceCitation {
  source_id: string;
  document_id: string;
  document_version_id: string;
  document_title: string;
  page_numbers: number[];
  section_number: string | null;
  clause_number: string | null;
  citations: Array<{
    block_id: string;
    page_number: number;
    bounding_box: unknown;
  }>;
}

export interface GroundedAnswer {
  answer: string;
  route: "DOCUMENT";
  sources: SourceCitation[];
  warnings: string[];
  confidence: string;
  review_required: boolean;
  model: string | null;
}

export interface StructuredAnswer {
  answer: string;
  route: string;
  template_id: string | null;
  records: Array<{
    values: Record<string, unknown>;
    provenance: {
      source_schema: string;
      source_table: string;
      source_record_id: string;
      freshness_at: string | null;
    };
  }>;
  confidence: string;
  warnings: string[];
  review_required: boolean;
  correlation_id: string;
}

export interface AuditEvent {
  event_id: string;
  occurred_at: string;
  query_category: string;
  entity_scope: Record<string, unknown>;
  source_ids: string[];
  result_status: string;
  correlation_id: string;
}

export interface DemoStatus {
  enabled: boolean;
  label: string;
  warning: string;
}

export type DemoIdentity = "demo.do" | "demo.no" | "demo.hod";

export type LocalLoginRole =
  | "Data Entry Operator"
  | "Nodal/Regional Officer"
  | "HOD"
  | "Tenant";

export interface LocalAuthStatus {
  enabled: boolean;
  roles: LocalLoginRole[];
}

export interface DemoAnswer {
  answer: string;
  route: "DOCUMENT_RAG" | "STRUCTURED_SQL" | "COMBINED" | "REVIEW_REQUIRED" | "REQUEST_REFUSED";
  principal: {
    username: string;
    role: string;
    department: string;
    unit_id: string;
    classification: string;
  };
  structured: {
    query_id: string;
    database_objects: string[];
    rows: Array<Record<string, unknown>>;
    row_count: number;
    freshness_at: string | null;
    filters: string[];
    read_only: boolean;
  } | null;
  document: GroundedAnswer | null;
  warnings: string[];
  review_required: boolean;
  correlation_id: string;
  duration_ms: number;
  evidence_extracted: boolean;
}
