export type User = { id: number; email: string; full_name: string; role: string; preferences: Record<string, unknown> };

export type Project = {
  id: number;
  name: string;
  description: string;
  status: "created" | "uploaded" | "analyzing" | "ready" | "failed";
  progress: number;
  stage: string;
  error: string | null;
  stats: Partial<Overview>;
  created_at: string;
  updated_at: string;
  analyzed_at: string | null;
};

export type Overview = {
  programs_analysed: number;
  copybooks: number;
  jobs: number;
  tables: number;
  procedures: number;
  datasets: number;
  critical_programs: number;
  unused_components: number;
  db_dependencies: number;
  business_rules: number;
  technical_rules: number;
  external_interfaces: number;
  lines_of_code: number;
  warnings: string[];
  analysis_seconds: number;
  files: number;
  languages: Record<string, number>;
};

export type ProgramStats = {
  program: string;
  file: string;
  type: string;
  purpose: string;
  loc: number;
  complexity: number;
  fan_in: number;
  fan_out: number;
  callers: string[];
  callees: string[];
  jobs: string[];
  apis: string[];
  tables_read: string[];
  tables_written: string[];
  datasets_in: string[];
  datasets_out: string[];
  business_rules: number;
  criticality: string;
  criticality_score: number;
  criticality_reasons: string[];
  unused: boolean;
};

export type Capability = { name: string; programs: string[]; tables: string[]; jobs: string[]; apis: string[]; business_rules: number; unused: boolean };

export type OverviewResponse = {
  overview: Overview;
  capabilities: Capability[];
  application_map: { order: string[]; edges: { source: string; target: string }[] };
  programs: ProgramStats[];
  unused_components: { type: string; name: string; file?: string; line?: number; reason: string }[];
  external_interfaces: { id: string; name: string; type: string; documented: boolean; description: string; used_by: string[] }[];
  decommission?: { programs: number; lines: number; share: number; hubs: number };
  sensitive_data?: SensitiveData;
  data_hotspots?: DataHotspot[];
  changes?: RunChanges | null;
};

export type SensitiveField = { field: string; where: string; kind: "table" | "copybook" | "program"; file?: string; line?: number; used_by: string[] };
export type SensitiveData = {
  fields: number; tables: string[]; programs: string[];
  categories: { key: string; label: string; count: number; fields: SensitiveField[] }[];
};
export type DataHotspot = { table: string; file?: string; line?: number; writers: string[]; readers: string[]; shared_write: boolean };
export type RunChanges = {
  baseline: boolean; unchanged?: boolean; compared_to?: string | null;
  files?: { added: string[]; removed: string[]; changed: string[] };
  components?: { kind: string; label: string; added: string[]; removed: string[] }[];
  rules?: { added: number; removed: number; added_sample: string[]; removed_sample: string[] };
};

export type Evidence = { file: string; line: number };
export type GraphNode = { id: string; type: string; label: string; file?: string; line?: number; [k: string]: unknown };
export type GraphEdge = { source: string; target: string; type: string; evidence?: Evidence[]; how?: string; file?: string; line?: number };
export type Graph = { nodes: GraphNode[]; edges: GraphEdge[] };
export type BlastRadius = {
  total: number; counts: Record<string, number>; max_depth: number; sample: { id: string; hops: number }[];
};

export type Rule = {
  id: string;
  program: string;
  file: string;
  paragraph: string | null;
  start_line: number;
  end_line: number;
  kind: string;
  category: string;
  is_technical: boolean;
  intent: string;
  description: string;
  condition: string | null;
  actions: string[];
  fields: string[];
};

export type Citation = { file: string; line_start: number; line_end: number; valid: boolean; reason: string | null };
export type Claim = { text: string; citations: Citation[]; supported: boolean };

export type Answer = {
  id?: number;
  question: string;
  mode: "llm" | "evidence_only" | "abstained" | "graph";
  answer: string;
  not_found: boolean;
  abstain_reason?: string | null;
  confidence: number;
  confidence_label: string;
  confidence_breakdown: { name: string; weight: number; value: number }[];
  claims: Claim[];
  evidence: { file: string; start_line: number; end_line: number; title: string; program?: string; score: number | null;
    similarity?: number | null; found_by?: "keyword" | "semantic" | "both"; snippet: string }[];
  facts: { text: string; file: string; line: number }[];
  entities: { name: string; type: string }[];
  validation: { citations_total?: number; citations_valid?: number; unknown_identifiers?: string[]; claims_total?: number; claims_supported?: number };
  retrieval?: { mode?: "hybrid" | "keyword" | "graph"; chunks_sent: number; chars_sent: number; top_score?: number;
    top_similarity?: number | null; strength?: number; found_by?: Record<string, number>; kind?: string };
  model?: string;
  usage?: { input_tokens: number; output_tokens: number; latency_ms: number; cached?: boolean; redactions?: number };
  warning?: string;
  created_at?: string;
  conversation_id?: number | null;
  context?: AnswerContext;
};

/** How a question used its conversation: follow-ups are read against the earlier turns. */
export type AnswerContext = { follow_up: boolean; resolved_question: string | null; turns_used: number };

export type Conversation = { id: number; title: string; turns: number; created_at: string; updated_at: string };

export type SymbolHit = { name: string; type: string; id: string; file?: string; line?: number; program?: string };

/* ---------------- Document-based role-specific Q&A ---------------- */

export type DocRole = "end_user" | "tester" | "developer" | "business";

export type DocRoleOption = { id: DocRole; label: string };

export type DocDocument = {
  id: number;
  filename: string;
  format: "pdf" | "docx" | "txt" | "md";
  size_bytes: number;
  status: "ready" | "failed";
  error: string | null;
  char_count: number;
  chunk_count: number;
  page_count: number;
  truncated: boolean;
  embedded: boolean;
  search_mode: "hybrid" | "keyword";
  created_at: string;
  updated_at: string;
};

export type DocCitation = {
  source: number;
  valid: boolean;
  page: number | null;
  section: string | null;
  title: string | null;
  note: string;
  snippet: string | null;
  reason: string | null;
};

export type DocEvidence = {
  source: number;
  page: number | null;
  section: string | null;
  title: string;
  score: number | null;
  similarity: number | null;
  found_by: "keyword" | "semantic" | "both";
  snippet: string;
};

export type DocAnswer = {
  id?: number;
  question: string;
  role: DocRole;
  role_label: string;
  mode: "llm" | "evidence_only" | "abstained";
  answer: string;
  not_found: boolean;
  abstain_reason?: string | null;
  missing?: string | null;
  confidence: number;
  confidence_label: string;
  confidence_breakdown: { name: string; weight: number; value: number }[];
  citations: DocCitation[];
  validation: { citations_total?: number; citations_valid?: number };
  evidence: DocEvidence[];
  retrieval: { mode: "hybrid" | "keyword"; chunks_sent: number; chars_sent: number; top_score: number;
    top_similarity?: number | null; strength?: number; found_by?: Record<string, number> };
  model?: string;
  usage?: { input_tokens: number; output_tokens: number; latency_ms: number };
  warning?: string;
  created_at?: string;
  conversation_id?: number | null;
  context?: AnswerContext;
};

export type ImpactItem = {
  id: string;
  label: string;
  type: string;
  file?: string;
  line?: number;
  distance: number;
  impact_type: "data" | "control" | "structural";
  severity: "High" | "Medium" | "Low";
  path: { id: string; label: string; type: string }[];
  evidence: { from: string; to: string; how: string; file?: string; line?: number }[];
};

export type ImpactExplanation = {
  summary: string; business_effect: string[]; risks: string[]; test_first: string[]; model: string | null; cached: boolean;
};

export type ImpactResult = {
  ambiguous: boolean;
  file?: string;
  options?: { id: string; label: string; type: string; program?: string }[];
  start?: { id: string; label: string; type: string }[];
  impacted?: ImpactItem[];
  summary?: Record<string, number>;
  severity_counts?: Record<string, number>;
  graph?: Graph;
  representative_chain?: { id: string; label: string; type: string }[];
};
