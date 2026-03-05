from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class WarehouseProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str = ""
    primary_events_table: str = ""
    primary_persons_table: str = ""
    inferred_time_column: str = ""
    inferred_event_column: str = ""
    inferred_distinct_id_column: str = ""
    inferred_session_column: str = ""
    inferred_group_column: str = ""
    inferred_properties_column: str = ""
    available_tables: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class CandidateSignalDraft(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    entity_grain: str = ""
    time_window: str = ""
    comparison_baseline: str = ""
    query_template: str = ""
    parameter_set: Dict[str, Any] = Field(default_factory=dict)
    interpretation: str = ""
    promotion_evidence: Dict[str, Any] = Field(default_factory=dict)
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


class PromotedSignal(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    entity_grain: str = ""
    time_window: str = ""
    comparison_baseline: str = ""
    query_template: str = ""
    parameter_set: Dict[str, Any] = Field(default_factory=dict)
    interpretation: str = ""
    promotion_evidence: Dict[str, Any] = Field(default_factory=dict)
    promoted: bool = True
    status: str = "promoted"


class ExperimentReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str = ""
    generated_at: str = ""
    candidates_proposed: int = 0
    policy_blocked: int = 0
    reviewer_rejected: int = 0
    executed_successfully: int = 0
    promoted: int = 0
    rerun_successful: int = 0
    rerun_success_rate: float = 0.0
    hypothesis_passed: bool = False
    notes: List[str] = Field(default_factory=list)
