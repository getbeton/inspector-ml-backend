from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class DatabaseProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str = ""
    target_chat_uuid: str = ""
    available_tables: List[str] = Field(default_factory=list)
    inferred_chat_table: str = ""
    inferred_message_table: str = ""
    inferred_reaction_table: str = ""
    inferred_membership_table: str = ""
    inferred_user_table: str = ""
    inferred_message_time_column: str = ""
    inferred_message_chat_column: str = ""
    inferred_message_user_column: str = ""
    inferred_message_id_column: str = ""
    inferred_reply_to_column: str = ""
    inferred_reaction_time_column: str = ""
    inferred_reaction_chat_column: str = ""
    inferred_reaction_user_column: str = ""
    inferred_membership_joined_at_column: str = ""
    inferred_membership_left_at_column: str = ""
    inferred_admin_source: str = ""
    table_notes: List[str] = Field(default_factory=list)
    confidence_notes: List[str] = Field(default_factory=list)


class AnalysisObjective(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str = ""
    target_chat_uuid: str = ""
    qualifying_activity: List[str] = Field(default_factory=list)
    success_definition: str = ""
    failure_definition: str = ""
    success_metric: str = ""
    failure_metric: str = ""
    success_threshold_value: float = 0.0
    failure_threshold_value: float = 0.0
    threshold_unit: str = ""
    admin_exclusion_strategy: str = ""
    admin_candidate_ids: List[str] = Field(default_factory=list)
    lookback_days: int = 21
    retention_windows_days: List[int] = Field(default_factory=lambda: [1, 7, 14, 21])
    objective_confidence: float = 0.0
    objective_evidence: Dict[str, Any] = Field(default_factory=dict)
    schema_hints: Dict[str, str] = Field(default_factory=dict)
    objective_notes: List[str] = Field(default_factory=list)
    captured_at: str = ""


class CandidateInsightDraft(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    hypothesis: str = ""
    entity_grain: str = ""
    time_window: str = ""
    comparison_baseline: str = ""
    query_template: str = ""
    parameter_set: Dict[str, Any] = Field(default_factory=dict)
    interpretation: str = ""
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    admin_implication: str = ""
    status: str = "draft"


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: str = "reject"
    reasons: List[str] = Field(default_factory=list)
    required_fixes: List[str] = Field(default_factory=list)
    promotion_readiness: bool = False


class ExecutionOutcome(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str = ""
    purpose: str = ""
    expected_grain: str = ""
    sql: str = ""
    row_count: int = 0
    column_count: int = 0
    cached: bool = False
    notes: str = ""


class PromotedInsight(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    hypothesis: str = ""
    entity_grain: str = ""
    time_window: str = ""
    comparison_baseline: str = ""
    query_template: str = ""
    parameter_set: Dict[str, Any] = Field(default_factory=dict)
    interpretation: str = ""
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    admin_implication: str = ""
    promoted: bool = True
    status: str = "promoted"


class ExperimentReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str = ""
    generated_at: str = ""
    target_chat_uuid: str = ""
    candidates_proposed: int = 0
    policy_blocked: int = 0
    reviewer_rejected: int = 0
    executed_successfully: int = 0
    promoted: int = 0
    notes: List[str] = Field(default_factory=list)
