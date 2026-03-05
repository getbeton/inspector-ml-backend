import inspect
import os
from typing import Any, Dict

import agentops
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.models.google_llm import Gemini
from google.adk.tools.exit_loop_tool import exit_loop
from google.adk.tools.function_tool import FunctionTool

from .tools import (
    dedupe_candidate,
    describe_table,
    finalize_experiment_report,
    get_loop_status,
    initialize_signal_run,
    list_tables,
    load_existing_signals,
    profile_events,
    run_readonly_query,
    sample_rows,
    store_candidate_signal,
    store_warehouse_profile,
    validate_sql_policy,
)

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model=os.getenv("SIGNAL_AGENT_MODEL", "gemini-2.5-flash"))
REVIEWER_MODEL = Gemini(
    model=os.getenv("SIGNAL_REVIEWER_MODEL", os.getenv("SIGNAL_AGENT_MODEL", "gemini-2.5-flash"))
)


class ScopedLoopAgent(LoopAgent):
    """
    Loop agent that consumes local exit escalation so parent sequential flows continue.
    """

    async def _run_async_impl(self, *args: Any, **kwargs: Any):
        should_exit = False
        async for event in super()._run_async_impl(*args, **kwargs):
            actions = getattr(event, "actions", None)
            if actions is not None and getattr(actions, "escalate", False):
                should_exit = True
                try:
                    actions.escalate = False
                except Exception:
                    pass
            yield event
            if should_exit:
                break


def _tool_name_from_callback_args(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    candidates = [kwargs.get("tool_name"), kwargs.get("name"), kwargs.get("tool")]
    if args:
        first = args[0]
        if isinstance(first, str):
            candidates.append(first)
        else:
            candidates.append(getattr(first, "name", ""))
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _tool_args_from_callback_args(args: tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(kwargs.get("tool_args"), dict):
        return kwargs["tool_args"]
    if isinstance(kwargs.get("args"), dict):
        return kwargs["args"]
    if len(args) >= 2 and isinstance(args[1], dict):
        return args[1]
    return {}


def signal_before_tool_callback(*args: Any, **kwargs: Any) -> Any:
    tool_name = _tool_name_from_callback_args(args, kwargs)
    if tool_name != "run_readonly_query":
        return None
    tool_args = _tool_args_from_callback_args(args, kwargs)
    tool_context = kwargs.get("tool_context")
    if tool_context is None and len(args) >= 3:
        tool_context = args[2]
    if tool_context is None:
        # Fall back to tool-level policy enforcement when callback context is unavailable.
        return None
    sql = str(tool_args.get("sql") or "")
    validation = validate_sql_policy(sql, tool_context=tool_context)
    if validation.get("allowed"):
        return None
    return {
        "ok": False,
        "error": "blocked_in_before_tool_callback",
        "violations": validation.get("violations", []),
    }


def _llm_kwargs(output_key: str = "") -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    try:
        sig = inspect.signature(LlmAgent.__init__)
    except Exception:
        return kwargs
    if "before_tool_callback" in sig.parameters:
        kwargs["before_tool_callback"] = signal_before_tool_callback
    if output_key and "output_key" in sig.parameters:
        kwargs["output_key"] = output_key
    return kwargs


bootstrap_agent = LlmAgent(
    name="signal_bootstrap_agent",
    model=MODEL,
    tools=[
        initialize_signal_run,
        list_tables,
        describe_table,
        sample_rows,
        profile_events,
        store_warehouse_profile,
    ],
    **_llm_kwargs(output_key="signal_bootstrap"),
    instruction=(
        "You are the bootstrap stage for signal_agent.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only.\n"
        "- Use tools only, no invented schema.\n"
        "- Assume PostHog-style events/persons are likely but verify via table discovery.\n"
        "- Tool calls must be sequential: call -> inspect response -> next call.\n"
        "- Do not call the same tool twice with identical arguments.\n"
        "\n"
        "Workflow:\n"
        "1) Call initialize_signal_run first and pass session_id from input.\n"
        "2) Call list_tables, then describe_table for likely events/persons tables.\n"
        "3) Call sample_rows for one likely events table and one likely persons table when available.\n"
        "4) Call profile_events for one likely events table.\n"
        "5) Infer likely semantic columns (time/event/person/session/group/properties).\n"
        "6) Call store_warehouse_profile exactly once.\n"
        "7) Return short JSON only.\n"
    ),
)

explorer_agent = LlmAgent(
    name="signal_explorer_agent",
    model=MODEL,
    tools=[
        load_existing_signals,
        dedupe_candidate,
        store_candidate_signal,
        validate_sql_policy,
        run_readonly_query,
        list_tables,
        describe_table,
        sample_rows,
        profile_events,
    ],
    **_llm_kwargs(output_key="signal_explorer_iteration"),
    instruction=(
        "You are the Explorer in a two-LLM signal discovery loop.\n"
        "\n"
        "Mission:\n"
        "- Propose exactly one reusable signal candidate per iteration.\n"
        "- Candidate must be entity-level and time-comparative (recent vs baseline).\n"
        "- You are the only role allowed to author SQL/HogQL.\n"
        "\n"
        "Allowed SQL subset (strict):\n"
        "- Use a single SELECT statement.\n"
        "- Use conditional aggregations only: countIf/sumIf/uniqIf/avgIf.\n"
        "- Use allowlisted base tables discovered via list_tables/describe_table.\n"
        "- Use explicit bounded time filters and LIMIT.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only SQL only.\n"
        "- Keep queries cheap and bounded.\n"
        "- Declare intended entity grain before SQL.\n"
        "- Avoid one-off descriptive reports.\n"
        "- Do not use UNION/UNION ALL (Inspector rejects UNION queries).\n"
        "- Do not use CASE expressions.\n"
        "- Do not use CAST(...); Inspector rejects CAST expressions.\n"
        "- Do not join events.distinct_id to persons.id (String vs UUID mismatch risk in Inspector/PostHog).\n"
        "- Prefer events.distinct_id as person-level grain unless a compatible persons key is explicitly verified.\n"
        "- Do not use blocked functions/patterns: arrayJoin, numbers*, remote*, url(), CROSS JOIN, INTO OUTFILE, LOAD_FILE.\n"
        "- For recent vs baseline comparisons, use a single SELECT with conditional aggregation.\n"
        "\n"
        "Window priors:\n"
        "- recent windows often 7, 14, or 30 days;\n"
        "- baseline often 2x to 4x recent window immediately prior.\n"
        "\n"
        "Workflow each iteration:\n"
        "1) Load existing signals and current warehouse context.\n"
        "2) Propose one candidate hypothesis.\n"
        "3) Produce query_template + parameter_set + rationale.\n"
        "4) Call dedupe_candidate.\n"
        "5) Call validate_sql_policy.\n"
        "6) If policy allows, call run_readonly_query once.\n"
        "7) Call store_candidate_signal with full candidate draft.\n"
        "\n"
        "Candidate fields must include:\n"
        "name, entity_grain, time_window, comparison_baseline, query_template,\n"
        "parameter_set, interpretation, promotion_evidence, status.\n"
        "Return JSON only.\n"
    ),
)

reviewer_agent = LlmAgent(
    name="signal_reviewer_agent",
    model=REVIEWER_MODEL,
    tools=[store_candidate_signal],
    **_llm_kwargs(output_key="signal_reviewer_iteration"),
    instruction=(
        "You are the Reviewer in a two-LLM loop.\n"
        "Review Explorer candidate for safety semantics, schema plausibility, grain clarity,\n"
        "temporal logic, nontriviality, and rerunnability.\n"
        "\n"
        "Rules:\n"
        "- Reject vague grain.\n"
        "- Reject if no meaningful temporal logic.\n"
        "- Reject trivial descriptive summaries.\n"
        "- Approve only reusable signal checks.\n"
        "- Reject any candidate that uses UNION/UNION ALL.\n"
        "- Reject any candidate that uses CAST(...).\n"
        "- Reject candidates that join events.distinct_id to persons.id.\n"
        "- Reject candidates using CASE expressions or blocked functions/patterns (arrayJoin/numbers/remote/url/CROSS JOIN/etc.).\n"
        "- If no explorer candidate is available in this turn, return a non-fatal continue decision (do not fabricate errors).\n"
        "- Deterministic policy tools are ultimate safety authority.\n"
        "\n"
        "Workflow:\n"
        "1) Build ReviewDecision JSON.\n"
        "2) Call store_candidate_signal once with candidate + review_decision.\n"
        "3) Set promoted=true,status=promoted only when candidate is reusable and execution evidence is meaningful.\n"
        "4) Return concise JSON decision only.\n"
    ),
)

loop_controller_agent = LlmAgent(
    name="signal_loop_controller_agent",
    model=MODEL,
    tools=[get_loop_status, FunctionTool(exit_loop)],
    **_llm_kwargs(output_key="signal_loop_status"),
    instruction=(
        "You control loop termination.\n"
        "Each turn:\n"
        "1) Call get_loop_status.\n"
        "2) Call exit_loop only when should_exit is true.\n"
        "3) Never call exit_loop when should_exit is false.\n"
        "4) Return JSON with continue/exit reason.\n"
    ),
)

iteration_pipeline_agent = SequentialAgent(
    name="signal_iteration_pipeline",
    sub_agents=[explorer_agent, reviewer_agent, loop_controller_agent],
)

discovery_loop_agent = ScopedLoopAgent(
    name="signal_discovery_loop",
    sub_agents=[iteration_pipeline_agent],
    max_iterations=int(os.getenv("SIGNAL_AGENT_MAX_ITERATIONS", "8")),
)

finalize_agent = LlmAgent(
    name="signal_finalize_agent",
    model=MODEL,
    tools=[finalize_experiment_report],
    **_llm_kwargs(output_key="signal_final_report"),
    instruction=(
        "You are the finalization stage.\n"
        "Call finalize_experiment_report exactly once.\n"
        "Return JSON with report metrics and summary_path only.\n"
    ),
)

root_agent = SequentialAgent(
    name="signal_agent",
    sub_agents=[bootstrap_agent, discovery_loop_agent, finalize_agent],
)

# Best-effort RunConfig export for runners that support it.
try:
    from google.adk.runners import RunConfig

    run_config = RunConfig(max_llm_calls=int(os.getenv("SIGNAL_AGENT_MAX_LLM_CALLS", "160")))
except Exception:
    run_config = None

# Backward-compatible aliases used in prior runs.
signal_worker = root_agent
signal_judge = reviewer_agent
