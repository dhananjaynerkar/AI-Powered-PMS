"""Typed, environment-aware configuration with production secret enforcement."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _secret_is_blank(value: SecretStr | None) -> bool:
    return value is None or not value.get_secret_value().strip()


class Settings(BaseSettings):
    """Phase 02 application settings loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Powered Port Management System"
    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    api_prefix: str = "/api/v1"
    timezone: str = "Asia/Kolkata"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json"] = "json"
    debug: bool = False
    enable_docs: bool = True
    request_id_header: str = "X-Request-ID"
    max_request_body_mb: int = Field(default=50, ge=1, le=1024)
    default_page_size: int = Field(default=50, ge=1)
    max_page_size: int = Field(default=500, ge=1)

    app_secret_key: SecretStr | None = None
    field_encryption_key: SecretStr | None = None
    password_pepper: SecretStr | None = None
    pii_log_redaction_enabled: bool = True
    log_sql_parameters: bool = False
    log_retrieved_text: bool = False
    log_model_prompts: bool = False

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_database: str = "postgres"
    postgres_user: str = "postgres"
    postgres_password: SecretStr | None = None
    database_url: SecretStr | None = None
    db_ssl_mode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "prefer"
    )
    db_connect_timeout_seconds: int = Field(default=10, ge=1, le=300)
    db_command_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_recycle_seconds: int = Field(default=1800, ge=60)
    db_echo: bool = False
    source_schema: str = "public"
    extract_schema: str = "pms_extract_2010_2023"
    app_schema: str = "pms_app"
    document_schema: str = "pms_doc"
    vector_schema: str = "pms_vector"
    rule_schema: str = "pms_rules"
    forecast_schema: str = "pms_forecast"
    graph_schema: str = "pms_graph"
    audit_schema: str = "pms_audit"
    rls_enabled: bool = True
    rule_calculation_version: str = "1.0"
    rule_rounding_method: Literal["ROUND_HALF_UP"] = "ROUND_HALF_UP"
    rule_money_scale: int = Field(default=2, ge=0, le=6)
    rule_max_segments: int = Field(default=500, ge=1, le=2000)
    rule_candidate_batch_size: int = Field(default=500, ge=1, le=5000)
    rule_require_dual_approval: bool = True
    rule_require_document_evidence: bool = True
    forecast_feature_version: str = "1.0"
    forecast_source_table: Literal["cash_revenue_data"] = "cash_revenue_data"
    forecast_default_cutoff: str = "2024-01-01T00:00:00+00:00"
    forecast_min_train_periods: int = Field(default=36, ge=24, le=240)
    forecast_default_horizon_months: int = Field(default=12, ge=1, le=60)
    forecast_backtest_folds: int = Field(default=5, ge=1, le=20)
    forecast_backtest_step_months: int = Field(default=12, ge=1, le=60)
    forecast_season_length: int = Field(default=12, ge=2, le=24)

    keycloak_enabled: bool = True
    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "pms"
    keycloak_client_id: str = "pms-api"
    keycloak_client_secret: SecretStr | None = None
    keycloak_issuer: str = "http://localhost:8080/realms/pms"
    keycloak_jwks_url: str = "http://localhost:8080/realms/pms/protocol/openid-connect/certs"
    keycloak_audience: str = "pms-api"
    keycloak_verify_ssl: bool = False
    jwt_algorithm: Literal["RS256"] = "RS256"
    jwt_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    jwt_tenant_claim: str = "tenant_id"
    jwt_role_claim: str = "realm_access.roles"
    jwt_department_claim: str = "department"
    jwt_classification_claim: str = "classification"
    authz_default_deny: bool = True
    tenant_scope_required: bool = True
    default_document_classification: Literal[
        "public", "internal", "confidential", "restricted"
    ] = "internal"
    audit_log_enabled: bool = True
    pms_demo_mode: bool = False
    pms_demo_database_url: SecretStr | None = None
    pms_demo_database_role: Literal["pms_demo_runtime"] = "pms_demo_runtime"
    pms_demo_max_rows: int = Field(default=10, ge=1, le=20)
    pms_demo_statement_timeout_seconds: int = Field(default=5, ge=1, le=15)
    local_password_auth_enabled: bool = False
    pms_auth_mode: Literal["environment", "local_database_demo"] = "environment"
    pms_business_schema: str = "public"
    local_auth_token_ttl_minutes: int = Field(default=60, ge=5, le=120)
    local_auth_data_entry_operator_username: str | None = None
    local_auth_data_entry_operator_password_hash: SecretStr | None = None
    local_auth_nodal_regional_officer_username: str | None = None
    local_auth_nodal_regional_officer_password_hash: SecretStr | None = None
    local_auth_hod_username: str | None = None
    local_auth_hod_password_hash: SecretStr | None = None
    local_auth_tenant_username: str | None = None
    local_auth_tenant_password_hash: SecretStr | None = None
    local_auth_tenant_id: str | None = None

    minio_enabled: bool = True
    minio_endpoint: str = "127.0.0.1:9000"
    minio_console_url: str = "http://127.0.0.1:9001"
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    minio_bucket_raw: str = "pms-raw-documents"
    minio_bucket_canonical: str = "pms-canonical-documents"
    minio_bucket_derived: str = "pms-derived-artifacts"
    minio_bucket_models: str = "pms-model-artifacts"
    minio_bucket_evaluation: str = "pms-evaluation-artifacts"
    minio_object_lock_enabled: bool = False
    minio_presigned_url_expiry_seconds: int = Field(default=300, ge=60, le=3600)

    file_hash_algorithm: Literal["sha256"] = "sha256"
    upload_mime_allowlist: str = (
        "application/pdf,text/csv,application/json,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    upload_extension_allowlist: str = ".pdf,.csv,.json,.xlsx"
    upload_max_mb: int = Field(default=200, ge=1, le=1024)
    clamav_enabled: bool = False
    clamav_host: str = "127.0.0.1"
    clamav_port: int = Field(default=3310, ge=1, le=65535)

    pdf_primary_parser: Literal["opendataloader"] = "opendataloader"
    pdf_primary_mode: Literal["deterministic"] = "deterministic"
    pdf_output_formats: str = "json,markdown"
    pdf_use_struct_tree: bool = True
    pdf_image_output: Literal["off", "embedded", "external"] = "external"
    pdf_threads: int = Field(default=1, ge=1, le=16)
    java_home: str | None = None
    java_min_major_version: int = Field(default=17, ge=11, le=30)
    opendataloader_enabled: bool = True
    opendataloader_sanitize: bool = False
    opendataloader_content_safety: bool = True
    opendataloader_hybrid_enabled: bool = True
    opendataloader_hybrid_backend: Literal["docling-fast"] = "docling-fast"
    docling_enabled: bool = True
    paddleocr_enabled: bool = True
    pymupdf_enabled: bool = True
    pdfplumber_enabled: bool = True
    ocr_enabled: bool = True
    ocr_languages: str = "eng,hin,mar"
    ocr_render_dpi: int = Field(default=300, ge=72, le=600)
    ocr_confidence_review_threshold: float = Field(default=0.90, ge=0, le=1)
    pdf_quality_gate_enabled: bool = True
    pdf_max_unexpected_blank_page_ratio: float = Field(default=0.02, ge=0, le=1)
    pdf_numeric_token_exact_match_required: bool = True
    pdf_citation_bbox_required: bool = True
    pdf_human_review_on_critical_failure: bool = True
    pdf_batch_size: int = Field(default=5, ge=1, le=100)
    pdf_max_retries: int = Field(default=2, ge=0, le=2)
    pdf_parse_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    canonical_schema_version: str = "1.0"

    chunk_strategy: Literal["structure_aware_parent_child"] = (
        "structure_aware_parent_child"
    )
    chunk_target_tokens: int = Field(default=420, ge=32, le=4096)
    chunk_max_tokens: int = Field(default=700, ge=64, le=8192)
    chunk_overlap_tokens: int = Field(default=60, ge=0, le=1024)
    parent_chunk_max_tokens: int = Field(default=1600, ge=128, le=16384)
    preserve_table_headers: bool = True
    preserve_legal_provisos: bool = True
    preserve_formula_context: bool = True
    chunk_hash_algorithm: Literal["sha256"] = "sha256"
    reembed_changed_only: bool = True

    embedding_provider: Literal["flagembedding"] = "flagembedding"
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_revision: str | None = None
    embedding_dimension: int = Field(default=1024, ge=1024, le=1024)
    embedding_device: Literal["cpu", "cuda"] = "cpu"
    embedding_batch_size: int = Field(default=4, ge=1, le=128)
    embedding_normalize: bool = True
    embedding_max_sequence_length: int = Field(default=1024, ge=128, le=8192)
    embedding_workers: int = Field(default=1, ge=1, le=8)
    embedding_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    embedding_cache_dir: str = "artifacts/model_cache/embeddings"
    embedding_store_text: bool = True
    embedding_fail_fast: bool = False
    embedding_max_retries: int = Field(default=2, ge=0, le=2)

    vector_backend: Literal["pgvector"] = "pgvector"
    vector_distance: Literal["cosine"] = "cosine"
    vector_index_mode: Literal["exact"] = "exact"
    lexical_backend: Literal["postgres_fts"] = "postgres_fts"
    lexical_language_config: Literal["simple"] = "simple"
    lexical_top_k: int = Field(default=30, ge=1, le=100)
    dense_top_k: int = Field(default=30, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    rerank_input_top_k: int = Field(default=30, ge=1, le=100)
    rerank_output_top_k: int = Field(default=8, ge=1, le=30)
    final_context_max_chunks: int = Field(default=8, ge=1, le=30)
    final_context_max_tokens: int = Field(default=5000, ge=256, le=32768)
    retrieval_min_score: float | None = Field(default=None, ge=-100, le=100)
    temporal_filter_required: bool = True
    document_acl_filter_required: bool = True
    citation_required: bool = True
    parent_expansion_enabled: bool = True

    reranker_enabled: bool = True
    reranker_provider: Literal["flagembedding"] = "flagembedding"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_model_revision: str | None = None
    reranker_device: Literal["cpu", "cuda"] = "cpu"
    reranker_batch_size: int = Field(default=2, ge=1, le=64)
    reranker_max_sequence_length: int = Field(default=1024, ge=128, le=8192)
    reranker_cache_dir: str = "artifacts/model_cache/reranker"
    reranker_timeout_seconds: int = Field(default=120, ge=30, le=1800)

    llm_provider: Literal["ollama"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_primary_model: str = "qwen3.5:4b"
    llm_fallback_model: str = "qwen3.5:4b"
    llm_allow_fallback: bool = False
    llm_thinking_enabled: bool = False
    llm_temperature: float = Field(default=0.1, ge=0, le=1)
    llm_top_p: float = Field(default=0.9, gt=0, le=1)
    llm_max_output_tokens: int = Field(default=700, ge=64, le=4096)
    llm_context_window: int = Field(default=8192, ge=2048, le=131072)
    llm_request_timeout_seconds: int = Field(default=180, ge=30, le=1800)
    llm_max_concurrent_requests: int = Field(default=1, ge=1, le=1)
    llm_keep_alive: str = "30m"
    llm_streaming: bool = True
    llm_json_mode_enabled: bool = True
    llm_citation_validation_enabled: bool = True
    llm_refuse_without_evidence: bool = True

    chat_schema: str = "pms_chat"
    case_workflow_enabled: bool = True
    case_thread_mode: Literal["shared_case_thread"] = "shared_case_thread"
    case_default_visibility: Literal["case_participants"] = "case_participants"
    case_require_assignment: bool = True
    case_allow_silent_message_edit: bool = False
    case_context_capsule_enabled: bool = True
    case_context_capsule_on_transition: bool = True
    case_recent_message_window: int = Field(default=12, ge=1, le=100)
    case_retrieved_message_top_k: int = Field(default=8, ge=1, le=100)
    case_context_max_tokens: int = Field(default=4500, ge=256, le=32768)
    case_summary_max_tokens: int = Field(default=900, ge=128, le=8192)
    case_decision_ledger_enabled: bool = True
    case_open_task_ledger_enabled: bool = True
    case_artifact_versioning_enabled: bool = True
    case_handoff_require_remarks: bool = True
    case_no_self_verification: bool = True
    case_no_self_approval: bool = True
    case_sla_enabled: bool = True
    case_state_hash_algorithm: Literal["sha256"] = "sha256"

    semantic_catalog_enabled: bool = True
    semantic_catalog_schema: str = "pms_catalog"
    semantic_catalog_embed_metadata: bool = True
    semantic_catalog_top_k_tables: int = Field(default=8, ge=1, le=61)
    semantic_catalog_top_k_columns: int = Field(default=30, ge=1, le=1010)
    governed_views_only: bool = True
    query_router_mode: Literal["deterministic_first"] = "deterministic_first"
    query_router_config: str = "config/query_router.yml"
    domain_synonyms_config: str = "config/domain_synonyms.yml"
    nl_to_sql_enabled: bool = False
    approved_sql_only: bool = True
    sql_template_dir: str = "sql/approved_queries"
    ambiguity_check_enabled: bool = True
    query_max_length: int = Field(default=4000, ge=1, le=4000)
    text_to_sql_enabled: bool = True
    text_to_sql_mode: Literal["templates_first"] = "templates_first"
    text_to_sql_select_only: bool = True
    text_to_sql_ast_validation: bool = True
    text_to_sql_explain_before_execute: bool = True
    text_to_sql_max_tables: int = Field(default=8, ge=1, le=8)
    text_to_sql_max_columns: int = Field(default=40, ge=1, le=40)
    text_to_sql_max_joins: int = Field(default=8, ge=0, le=8)
    text_to_sql_max_rows: int = Field(default=500, ge=1, le=500)
    text_to_sql_timeout_seconds: int = Field(default=15, ge=1, le=60)
    text_to_sql_max_plan_cost: float = Field(default=100_000, gt=0, le=10_000_000)
    text_to_sql_allowlist_schemas: str = (
        "pms_app,pms_extract_2010_2023,pms_forecast,pms_graph"
    )
    text_to_sql_deny_sensitive_columns: bool = True
    text_to_sql_human_review_for_high_risk: bool = True

    @field_validator("retrieval_min_score", mode="before")
    @classmethod
    def blank_optional_number_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_environment_contract(self) -> Self:
        if self.default_page_size > self.max_page_size:
            raise ValueError("DEFAULT_PAGE_SIZE must not exceed MAX_PAGE_SIZE")
        if self.case_summary_max_tokens > self.case_context_max_tokens:
            raise ValueError("CASE_SUMMARY_MAX_TOKENS must not exceed CASE_CONTEXT_MAX_TOKENS")
        if self.chunk_target_tokens > self.chunk_max_tokens:
            raise ValueError("CHUNK_TARGET_TOKENS must not exceed CHUNK_MAX_TOKENS")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be below CHUNK_TARGET_TOKENS")
        if self.chunk_max_tokens > self.embedding_max_sequence_length:
            raise ValueError(
                "CHUNK_MAX_TOKENS must not exceed EMBEDDING_MAX_SEQUENCE_LENGTH"
            )
        if self.chunk_max_tokens > self.parent_chunk_max_tokens:
            raise ValueError(
                "CHUNK_MAX_TOKENS must not exceed PARENT_CHUNK_MAX_TOKENS"
            )
        if self.rerank_output_top_k > self.rerank_input_top_k:
            raise ValueError(
                "RERANK_OUTPUT_TOP_K must not exceed RERANK_INPUT_TOP_K"
            )
        if self.final_context_max_chunks > self.rerank_output_top_k:
            raise ValueError(
                "FINAL_CONTEXT_MAX_CHUNKS must not exceed RERANK_OUTPUT_TOP_K"
            )
        if self.llm_thinking_enabled:
            raise ValueError("LLM_THINKING_ENABLED must remain false")
        if self.text_to_sql_enabled and not self.text_to_sql_allowlist_schemas.strip():
            raise ValueError(
                "TEXT_TO_SQL_ALLOWLIST_SCHEMAS is required when Text-to-SQL is enabled"
            )
        if self.nl_to_sql_enabled:
            raise ValueError(
                "NL_TO_SQL_ENABLED must remain false; use approved templates "
                "or an internal constrained plan"
            )
        if self.pms_demo_mode:
            if self.app_env != "development":
                raise ValueError("PMS_DEMO_MODE is allowed only in development")
            if self.app_host.casefold() not in {"localhost", "127.0.0.1"}:
                raise ValueError("PMS_DEMO_MODE requires localhost or 127.0.0.1")
            if _secret_is_blank(self.pms_demo_database_url):
                raise ValueError("PMS_DEMO_DATABASE_URL is required when demo mode is enabled")
        if self.local_password_auth_enabled:
            if self.app_env != "development":
                raise ValueError("LOCAL_PASSWORD_AUTH_ENABLED is allowed only in development")
            if self.app_host.casefold() not in {"localhost", "127.0.0.1"}:
                raise ValueError(
                    "LOCAL_PASSWORD_AUTH_ENABLED requires localhost or 127.0.0.1"
                )
            required_local_auth = {
                "LOCAL_AUTH_DATA_ENTRY_OPERATOR_USERNAME": (
                    self.local_auth_data_entry_operator_username
                ),
                "LOCAL_AUTH_DATA_ENTRY_OPERATOR_PASSWORD_HASH": (
                    self.local_auth_data_entry_operator_password_hash
                ),
                "LOCAL_AUTH_NODAL_REGIONAL_OFFICER_USERNAME": (
                    self.local_auth_nodal_regional_officer_username
                ),
                "LOCAL_AUTH_NODAL_REGIONAL_OFFICER_PASSWORD_HASH": (
                    self.local_auth_nodal_regional_officer_password_hash
                ),
                "LOCAL_AUTH_HOD_USERNAME": self.local_auth_hod_username,
                "LOCAL_AUTH_HOD_PASSWORD_HASH": self.local_auth_hod_password_hash,
                "LOCAL_AUTH_TENANT_USERNAME": self.local_auth_tenant_username,
                "LOCAL_AUTH_TENANT_PASSWORD_HASH": self.local_auth_tenant_password_hash,
                "LOCAL_AUTH_TENANT_ID": self.local_auth_tenant_id,
            }
            missing_local_auth = [
                name
                for name, value in required_local_auth.items()
                if (
                    _secret_is_blank(value)
                    if isinstance(value, SecretStr)
                    else not isinstance(value, str) or not value.strip()
                )
            ]
            if missing_local_auth:
                raise ValueError(
                    "local password authentication requires: "
                    + ", ".join(sorted(missing_local_auth))
                )
        required_safety_controls = {
            "CASE_REQUIRE_ASSIGNMENT": self.case_require_assignment,
            "CASE_CONTEXT_CAPSULE_ENABLED": self.case_context_capsule_enabled,
            "CASE_CONTEXT_CAPSULE_ON_TRANSITION": self.case_context_capsule_on_transition,
            "CASE_DECISION_LEDGER_ENABLED": self.case_decision_ledger_enabled,
            "CASE_OPEN_TASK_LEDGER_ENABLED": self.case_open_task_ledger_enabled,
            "CASE_ARTIFACT_VERSIONING_ENABLED": self.case_artifact_versioning_enabled,
            "CASE_HANDOFF_REQUIRE_REMARKS": self.case_handoff_require_remarks,
            "CASE_NO_SELF_VERIFICATION": self.case_no_self_verification,
            "CASE_NO_SELF_APPROVAL": self.case_no_self_approval,
            "GOVERNED_VIEWS_ONLY": self.governed_views_only,
            "TEXT_TO_SQL_SELECT_ONLY": self.text_to_sql_select_only,
            "TEXT_TO_SQL_AST_VALIDATION": self.text_to_sql_ast_validation,
            "TEXT_TO_SQL_EXPLAIN_BEFORE_EXECUTE": self.text_to_sql_explain_before_execute,
            "APPROVED_SQL_ONLY": self.approved_sql_only,
            "AMBIGUITY_CHECK_ENABLED": self.ambiguity_check_enabled,
            "RULE_REQUIRE_DUAL_APPROVAL": self.rule_require_dual_approval,
            "RULE_REQUIRE_DOCUMENT_EVIDENCE": self.rule_require_document_evidence,
            "TEXT_TO_SQL_DENY_SENSITIVE_COLUMNS": self.text_to_sql_deny_sensitive_columns,
            "TEXT_TO_SQL_HUMAN_REVIEW_FOR_HIGH_RISK": (
                self.text_to_sql_human_review_for_high_risk
            ),
            "OPENDATALOADER_CONTENT_SAFETY": self.opendataloader_content_safety,
            "PDF_QUALITY_GATE_ENABLED": self.pdf_quality_gate_enabled,
            "PDF_NUMERIC_TOKEN_EXACT_MATCH_REQUIRED": (
                self.pdf_numeric_token_exact_match_required
            ),
            "PDF_CITATION_BBOX_REQUIRED": self.pdf_citation_bbox_required,
            "PDF_HUMAN_REVIEW_ON_CRITICAL_FAILURE": (
                self.pdf_human_review_on_critical_failure
            ),
            "PRESERVE_TABLE_HEADERS": self.preserve_table_headers,
            "PRESERVE_LEGAL_PROVISOS": self.preserve_legal_provisos,
            "PRESERVE_FORMULA_CONTEXT": self.preserve_formula_context,
            "REEMBED_CHANGED_ONLY": self.reembed_changed_only,
            "EMBEDDING_STORE_TEXT": self.embedding_store_text,
            "TEMPORAL_FILTER_REQUIRED": self.temporal_filter_required,
            "DOCUMENT_ACL_FILTER_REQUIRED": self.document_acl_filter_required,
            "CITATION_REQUIRED": self.citation_required,
            "PARENT_EXPANSION_ENABLED": self.parent_expansion_enabled,
            "RERANKER_ENABLED": self.reranker_enabled,
            "LLM_JSON_MODE_ENABLED": self.llm_json_mode_enabled,
            "LLM_CITATION_VALIDATION_ENABLED": self.llm_citation_validation_enabled,
            "LLM_REFUSE_WITHOUT_EVIDENCE": self.llm_refuse_without_evidence,
        }
        disabled_controls = [
            name for name, enabled in required_safety_controls.items() if not enabled
        ]
        if disabled_controls:
            raise ValueError(
                "mandatory safety controls must remain enabled: "
                + ", ".join(sorted(disabled_controls))
            )
        if self.case_allow_silent_message_edit:
            raise ValueError("CASE_ALLOW_SILENT_MESSAGE_EDIT must remain false")
        if self.app_env != "production":
            return self
        if self.debug:
            raise ValueError("DEBUG must be false in production")

        required_secrets = {
            "APP_SECRET_KEY": self.app_secret_key,
            "FIELD_ENCRYPTION_KEY": self.field_encryption_key,
            "PASSWORD_PEPPER": self.password_pepper,
            "POSTGRES_PASSWORD": self.postgres_password,
        }
        if self.keycloak_enabled:
            required_secrets["KEYCLOAK_CLIENT_SECRET"] = self.keycloak_client_secret
        if self.minio_enabled:
            required_secrets["MINIO_ACCESS_KEY"] = self.minio_access_key
            required_secrets["MINIO_SECRET_KEY"] = self.minio_secret_key

        missing = [name for name, value in required_secrets.items() if _secret_is_blank(value)]
        if missing:
            raise ValueError(f"production secrets are required: {', '.join(sorted(missing))}")
        if self.keycloak_enabled and (
            not self.keycloak_verify_ssl
            or not self.keycloak_issuer.startswith("https://")
            or not self.keycloak_jwks_url.startswith("https://")
        ):
            raise ValueError("production Keycloak issuer/JWKS must use verified HTTPS")
        if self.database_url is not None:
            database_url = self.database_url.get_secret_value()
            if "CHANGE_ME" in database_url:
                raise ValueError("DATABASE_URL contains the placeholder CHANGE_ME")
        return self

    def safe_diagnostics(self) -> dict[str, object]:
        """Return startup diagnostics containing no secret-bearing fields."""

        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "app_version": self.app_version,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "api_prefix": self.api_prefix,
            "timezone": self.timezone,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "debug": self.debug,
            "enable_docs": self.enable_docs,
            "request_id_header": self.request_id_header,
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_database": self.postgres_database,
            "postgres_user": self.postgres_user,
            "db_ssl_mode": self.db_ssl_mode,
            "db_connect_timeout_seconds": self.db_connect_timeout_seconds,
            "db_command_timeout_seconds": self.db_command_timeout_seconds,
            "db_pool_size": self.db_pool_size,
            "db_max_overflow": self.db_max_overflow,
            "db_pool_recycle_seconds": self.db_pool_recycle_seconds,
            "db_echo": self.db_echo,
            "source_schema": self.source_schema,
            "extract_schema": self.extract_schema,
            "app_schema": self.app_schema,
            "document_schema": self.document_schema,
            "vector_schema": self.vector_schema,
            "rule_schema": self.rule_schema,
            "forecast_schema": self.forecast_schema,
            "graph_schema": self.graph_schema,
            "audit_schema": self.audit_schema,
            "rls_enabled": self.rls_enabled,
            "pms_demo_mode": self.pms_demo_mode,
            "pms_demo_database_role": self.pms_demo_database_role,
            "pms_demo_max_rows": self.pms_demo_max_rows,
            "pms_demo_statement_timeout_seconds": (
                self.pms_demo_statement_timeout_seconds
            ),
            "rule_calculation_version": self.rule_calculation_version,
            "rule_rounding_method": self.rule_rounding_method,
            "rule_money_scale": self.rule_money_scale,
            "rule_max_segments": self.rule_max_segments,
            "rule_candidate_batch_size": self.rule_candidate_batch_size,
            "rule_require_dual_approval": self.rule_require_dual_approval,
            "rule_require_document_evidence": self.rule_require_document_evidence,
            "keycloak_enabled": self.keycloak_enabled,
            "keycloak_base_url": self.keycloak_base_url,
            "keycloak_realm": self.keycloak_realm,
            "keycloak_client_id": self.keycloak_client_id,
            "keycloak_issuer": self.keycloak_issuer,
            "keycloak_jwks_url": self.keycloak_jwks_url,
            "keycloak_audience": self.keycloak_audience,
            "keycloak_verify_ssl": self.keycloak_verify_ssl,
            "jwt_algorithm": self.jwt_algorithm,
            "jwt_clock_skew_seconds": self.jwt_clock_skew_seconds,
            "jwt_tenant_claim": self.jwt_tenant_claim,
            "jwt_role_claim": self.jwt_role_claim,
            "jwt_department_claim": self.jwt_department_claim,
            "jwt_classification_claim": self.jwt_classification_claim,
            "authz_default_deny": self.authz_default_deny,
            "local_password_auth_enabled": self.local_password_auth_enabled,
            "local_auth_token_ttl_minutes": self.local_auth_token_ttl_minutes,
            "tenant_scope_required": self.tenant_scope_required,
            "default_document_classification": self.default_document_classification,
            "audit_log_enabled": self.audit_log_enabled,
            "minio_enabled": self.minio_enabled,
            "minio_endpoint": self.minio_endpoint,
            "minio_console_url": self.minio_console_url,
            "minio_secure": self.minio_secure,
            "minio_region": self.minio_region,
            "minio_bucket_raw": self.minio_bucket_raw,
            "minio_bucket_canonical": self.minio_bucket_canonical,
            "minio_bucket_derived": self.minio_bucket_derived,
            "minio_bucket_models": self.minio_bucket_models,
            "minio_bucket_evaluation": self.minio_bucket_evaluation,
            "minio_object_lock_enabled": self.minio_object_lock_enabled,
            "minio_presigned_url_expiry_seconds": (
                self.minio_presigned_url_expiry_seconds
            ),
            "file_hash_algorithm": self.file_hash_algorithm,
            "upload_mime_allowlist": self.upload_mime_allowlist,
            "upload_extension_allowlist": self.upload_extension_allowlist,
            "upload_max_mb": self.upload_max_mb,
            "clamav_enabled": self.clamav_enabled,
            "clamav_host": self.clamav_host,
            "clamav_port": self.clamav_port,
            "pdf_primary_parser": self.pdf_primary_parser,
            "pdf_primary_mode": self.pdf_primary_mode,
            "pdf_output_formats": self.pdf_output_formats,
            "pdf_use_struct_tree": self.pdf_use_struct_tree,
            "pdf_image_output": self.pdf_image_output,
            "pdf_threads": self.pdf_threads,
            "java_home_configured": bool(self.java_home and self.java_home.strip()),
            "java_min_major_version": self.java_min_major_version,
            "opendataloader_enabled": self.opendataloader_enabled,
            "opendataloader_sanitize": self.opendataloader_sanitize,
            "opendataloader_content_safety": self.opendataloader_content_safety,
            "opendataloader_hybrid_enabled": self.opendataloader_hybrid_enabled,
            "opendataloader_hybrid_backend": self.opendataloader_hybrid_backend,
            "docling_enabled": self.docling_enabled,
            "paddleocr_enabled": self.paddleocr_enabled,
            "pymupdf_enabled": self.pymupdf_enabled,
            "pdfplumber_enabled": self.pdfplumber_enabled,
            "ocr_enabled": self.ocr_enabled,
            "ocr_languages": self.ocr_languages,
            "ocr_render_dpi": self.ocr_render_dpi,
            "ocr_confidence_review_threshold": (
                self.ocr_confidence_review_threshold
            ),
            "pdf_quality_gate_enabled": self.pdf_quality_gate_enabled,
            "pdf_max_unexpected_blank_page_ratio": (
                self.pdf_max_unexpected_blank_page_ratio
            ),
            "pdf_numeric_token_exact_match_required": (
                self.pdf_numeric_token_exact_match_required
            ),
            "pdf_citation_bbox_required": self.pdf_citation_bbox_required,
            "pdf_human_review_on_critical_failure": (
                self.pdf_human_review_on_critical_failure
            ),
            "pdf_batch_size": self.pdf_batch_size,
            "pdf_max_retries": self.pdf_max_retries,
            "pdf_parse_timeout_seconds": self.pdf_parse_timeout_seconds,
            "canonical_schema_version": self.canonical_schema_version,
            "chunk_strategy": self.chunk_strategy,
            "chunk_target_tokens": self.chunk_target_tokens,
            "chunk_max_tokens": self.chunk_max_tokens,
            "chunk_overlap_tokens": self.chunk_overlap_tokens,
            "parent_chunk_max_tokens": self.parent_chunk_max_tokens,
            "preserve_table_headers": self.preserve_table_headers,
            "preserve_legal_provisos": self.preserve_legal_provisos,
            "preserve_formula_context": self.preserve_formula_context,
            "chunk_hash_algorithm": self.chunk_hash_algorithm,
            "reembed_changed_only": self.reembed_changed_only,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_model_revision": self.embedding_model_revision,
            "embedding_dimension": self.embedding_dimension,
            "embedding_device": self.embedding_device,
            "embedding_batch_size": self.embedding_batch_size,
            "embedding_normalize": self.embedding_normalize,
            "embedding_max_sequence_length": self.embedding_max_sequence_length,
            "embedding_workers": self.embedding_workers,
            "embedding_timeout_seconds": self.embedding_timeout_seconds,
            "embedding_cache_dir": self.embedding_cache_dir,
            "embedding_store_text": self.embedding_store_text,
            "embedding_fail_fast": self.embedding_fail_fast,
            "embedding_max_retries": self.embedding_max_retries,
            "vector_backend": self.vector_backend,
            "vector_distance": self.vector_distance,
            "vector_index_mode": self.vector_index_mode,
            "lexical_backend": self.lexical_backend,
            "lexical_language_config": self.lexical_language_config,
            "lexical_top_k": self.lexical_top_k,
            "dense_top_k": self.dense_top_k,
            "rrf_k": self.rrf_k,
            "rerank_input_top_k": self.rerank_input_top_k,
            "rerank_output_top_k": self.rerank_output_top_k,
            "final_context_max_chunks": self.final_context_max_chunks,
            "final_context_max_tokens": self.final_context_max_tokens,
            "retrieval_min_score": self.retrieval_min_score,
            "temporal_filter_required": self.temporal_filter_required,
            "document_acl_filter_required": self.document_acl_filter_required,
            "citation_required": self.citation_required,
            "parent_expansion_enabled": self.parent_expansion_enabled,
            "reranker_enabled": self.reranker_enabled,
            "reranker_provider": self.reranker_provider,
            "reranker_model": self.reranker_model,
            "reranker_model_revision": self.reranker_model_revision,
            "reranker_device": self.reranker_device,
            "reranker_batch_size": self.reranker_batch_size,
            "reranker_max_sequence_length": self.reranker_max_sequence_length,
            "reranker_cache_dir": self.reranker_cache_dir,
            "reranker_timeout_seconds": self.reranker_timeout_seconds,
            "llm_provider": self.llm_provider,
            "ollama_base_url": self.ollama_base_url,
            "llm_primary_model": self.llm_primary_model,
            "llm_fallback_model": self.llm_fallback_model,
            "llm_allow_fallback": self.llm_allow_fallback,
            "llm_thinking_enabled": self.llm_thinking_enabled,
            "llm_temperature": self.llm_temperature,
            "llm_top_p": self.llm_top_p,
            "llm_max_output_tokens": self.llm_max_output_tokens,
            "llm_context_window": self.llm_context_window,
            "llm_request_timeout_seconds": self.llm_request_timeout_seconds,
            "llm_max_concurrent_requests": self.llm_max_concurrent_requests,
            "llm_keep_alive": self.llm_keep_alive,
            "llm_streaming": self.llm_streaming,
            "llm_json_mode_enabled": self.llm_json_mode_enabled,
            "llm_citation_validation_enabled": self.llm_citation_validation_enabled,
            "llm_refuse_without_evidence": self.llm_refuse_without_evidence,
            "chat_schema": self.chat_schema,
            "case_workflow_enabled": self.case_workflow_enabled,
            "case_thread_mode": self.case_thread_mode,
            "case_default_visibility": self.case_default_visibility,
            "case_require_assignment": self.case_require_assignment,
            "case_allow_silent_message_edit": self.case_allow_silent_message_edit,
            "case_context_capsule_enabled": self.case_context_capsule_enabled,
            "case_context_capsule_on_transition": self.case_context_capsule_on_transition,
            "case_recent_message_window": self.case_recent_message_window,
            "case_retrieved_message_top_k": self.case_retrieved_message_top_k,
            "case_context_max_tokens": self.case_context_max_tokens,
            "case_summary_max_tokens": self.case_summary_max_tokens,
            "case_decision_ledger_enabled": self.case_decision_ledger_enabled,
            "case_open_task_ledger_enabled": self.case_open_task_ledger_enabled,
            "case_artifact_versioning_enabled": self.case_artifact_versioning_enabled,
            "case_handoff_require_remarks": self.case_handoff_require_remarks,
            "case_no_self_verification": self.case_no_self_verification,
            "case_no_self_approval": self.case_no_self_approval,
            "case_sla_enabled": self.case_sla_enabled,
            "case_state_hash_algorithm": self.case_state_hash_algorithm,
            "semantic_catalog_enabled": self.semantic_catalog_enabled,
            "semantic_catalog_schema": self.semantic_catalog_schema,
            "semantic_catalog_embed_metadata": self.semantic_catalog_embed_metadata,
            "semantic_catalog_top_k_tables": self.semantic_catalog_top_k_tables,
            "semantic_catalog_top_k_columns": self.semantic_catalog_top_k_columns,
            "governed_views_only": self.governed_views_only,
            "query_router_mode": self.query_router_mode,
            "query_router_config": self.query_router_config,
            "domain_synonyms_config": self.domain_synonyms_config,
            "nl_to_sql_enabled": self.nl_to_sql_enabled,
            "approved_sql_only": self.approved_sql_only,
            "sql_template_dir": self.sql_template_dir,
            "ambiguity_check_enabled": self.ambiguity_check_enabled,
            "query_max_length": self.query_max_length,
            "text_to_sql_enabled": self.text_to_sql_enabled,
            "text_to_sql_mode": self.text_to_sql_mode,
            "text_to_sql_select_only": self.text_to_sql_select_only,
            "text_to_sql_ast_validation": self.text_to_sql_ast_validation,
            "text_to_sql_explain_before_execute": self.text_to_sql_explain_before_execute,
            "text_to_sql_max_tables": self.text_to_sql_max_tables,
            "text_to_sql_max_columns": self.text_to_sql_max_columns,
            "text_to_sql_max_joins": self.text_to_sql_max_joins,
            "text_to_sql_max_rows": self.text_to_sql_max_rows,
            "text_to_sql_timeout_seconds": self.text_to_sql_timeout_seconds,
            "text_to_sql_max_plan_cost": self.text_to_sql_max_plan_cost,
            "text_to_sql_allowlist_schemas": self.text_to_sql_allowlist_schemas,
            "text_to_sql_deny_sensitive_columns": self.text_to_sql_deny_sensitive_columns,
            "text_to_sql_human_review_for_high_risk": (
                self.text_to_sql_human_review_for_high_risk
            ),
            "pii_log_redaction_enabled": self.pii_log_redaction_enabled,
            "log_sql_parameters": self.log_sql_parameters,
            "log_retrieved_text": self.log_retrieved_text,
            "log_model_prompts": self.log_model_prompts,
        }
