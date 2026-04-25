import inspect
import os
from typing import Any, Dict

import agentops
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from shared.model_factory import build_gemini

try:
    from google.adk.models.google_llm import _ResourceExhaustedError
except Exception:  # pragma: no cover - runtime compatibility guard
    _ResourceExhaustedError = Exception

from .tools import (
    dedupe_candidate,
    finalize_experiment_report,
    get_signal_objective_compact,
    get_warehouse_profile_compact,
    load_existing_signals,
    prepare_signal_inputs,
    run_readonly_query,
    store_candidate_signal,
    validate_sql_policy,
)

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = build_gemini("SIGNAL_AGENT_MODEL", "gemini-3-flash-preview")
REVIEWER_MODEL = build_gemini(
    "SIGNAL_REVIEWER_MODEL",
    os.getenv("SIGNAL_AGENT_MODEL", "gemini-3-flash-preview"),
)


class ScopedLoopAgent(LoopAgent):
    """
    Loop agent that consumes local exit escalation and also stops deterministically
    from session state instead of asking an LLM to decide when to end.
    """

    def _should_exit_from_state(self, ctx: Any) -> bool:
        state = getattr(getattr(ctx, "session", None), "state", None)
        if state is None:
            return False
        limits = state.get("loop_limits") or {}
        max_iterations = int(limits.get("max_iterations") or os.getenv("SIGNAL_AGENT_MAX_ITERATIONS", "8"))
        target_promoted = int(limits.get("target_promoted_signals") or os.getenv("SIGNAL_AGENT_TARGET_PROMOTED", "3"))
        max_failures = int(limits.get("too_many_failures") or os.getenv("SIGNAL_AGENT_MAX_FAILURES", "5"))
        min_iterations_before_exit = int(os.getenv("SIGNAL_AGENT_MIN_ITERATIONS_BEFORE_EXIT", "3"))
        iteration = int(state.get("iteration_index") or 0) + 1
        promoted_count = len(state.get("promoted_signals") or [])
        failures = int(state.get("failure_count") or 0)
        query_count = int(state.get("query_count") or 0)
        llm_calls_used_estimate = max(int(state.get("llm_calls_used_estimate") or 0), (iteration * 2) + 2)
        state["iteration_index"] = iteration
        state["llm_calls_used_estimate"] = llm_calls_used_estimate

        reached_limits = (
            iteration >= max_iterations
            or promoted_count >= target_promoted
            or failures >= max_failures
        )
        should_exit = reached_limits and iteration >= min_iterations_before_exit
        reason = "continue"
        if reached_limits and iteration < min_iterations_before_exit:
            reason = "continue_min_iterations_guard"
        elif iteration >= max_iterations:
            reason = "max_iterations_reached"
        elif promoted_count >= target_promoted:
            reason = "target_promoted_signals_reached"
        elif failures >= max_failures:
            reason = "too_many_failures"

        status_payload = {
            "ok": True,
            "iteration_index": iteration,
            "promoted_count": promoted_count,
            "failure_count": failures,
            "non_query_failure_count": int(state.get("non_query_failure_count") or 0),
            "query_count": query_count,
            "llm_calls_used_estimate": llm_calls_used_estimate,
            "limits": {
                "max_iterations": max_iterations,
                "target_promoted_signals": target_promoted,
                "too_many_failures": max_failures,
                "min_iterations_before_exit": min_iterations_before_exit,
            },
            "should_exit": should_exit,
            "reason": reason,
        }
        state["last_loop_status"] = status_payload
        state.setdefault("loop_iteration_events", []).append(status_payload)
        return should_exit

    async def _run_async_impl(self, *args: Any, **kwargs: Any):
        should_exit = False
        ctx = args[0] if args else kwargs.get("ctx")
        try:
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
                author = getattr(event, "author", "") or getattr(getattr(event, "invocation_metadata", None), "agent", "")
                if author == "signal_iteration_pipeline" and ctx is not None and self._should_exit_from_state(ctx):
                    break
        except _ResourceExhaustedError as exc:
            state = getattr(getattr(ctx, "session", None), "state", None)
            if state is not None:
                detail = str(exc)
                state["quota_exhausted_error"] = detail
                state["last_loop_status"] = {
                    "ok": False,
                    "iteration_index": int(state.get("iteration_index") or 0),
                    "reason": "resource_exhausted_retry_exhausted",
                    "should_exit": True,
                    "detail": detail,
                }
                state.setdefault("loop_iteration_events", []).append(state["last_loop_status"])
            return


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


signal_prep_agent = LlmAgent(
    name="signal_prep_agent",
    model=MODEL,
    tools=[prepare_signal_inputs],
    **_llm_kwargs(output_key="signal_prep"),
    instruction=(
        "You prepare signal_agent state from upstream upsell_agent and dwh_analyst outputs.\n"
        "\n"
        "Hard constraints:\n"
        "- Do not redo website discovery or broad warehouse discovery.\n"
        "- Use upstream shared state as the source of truth.\n"
        "- Call prepare_signal_inputs exactly once.\n"
        "- Pass session_id from input when present.\n"
        "- Return compact JSON only.\n"
        "\n"
        "Workflow:\n"
        "1) Extract session_id from the input if present.\n"
        "2) Call prepare_signal_inputs exactly once.\n"
        "3) Return the resulting JSON only.\n"
    ),
)

explorer_agent = LlmAgent(
    name="signal_explorer_agent",
    model=MODEL,
    tools=[
        load_existing_signals,
        get_warehouse_profile_compact,
        get_signal_objective_compact,
        dedupe_candidate,
        store_candidate_signal,
        validate_sql_policy,
        run_readonly_query,
    ],
    **_llm_kwargs(output_key="signal_explorer_iteration"),
    instruction=(
        "You are the Explorer in a two-LLM signal discovery loop.\n"
        "\n"
        "Mission:\n"
        "- Propose exactly one reusable signal candidate per iteration.\n"
        "- Candidate must predict pre-conversion upsell opportunity for users who did NOT yet complete success_event.\n"
        "- Candidate can be either path-pattern or profile-similarity, but it must align to success_event.\n"
        "- You are the only role allowed to author SQL/HogQL.\n"
        "\n"
        "Allowed SQL subset (strict):\n"
        "- Use a single SELECT statement.\n"
        "- Use conditional aggregations only: countIf/sumIf/uniqIf/avgIf.\n"
        "- Use allowlisted base tables prepared from upstream warehouse metadata.\n"
        "- Use explicit bounded time filters and LIMIT.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only SQL only.\n"
        "- Keep queries cheap and bounded.\n"
        "- Declare intended entity grain before SQL.\n"
        "- Avoid one-off descriptive reports and raw volume deltas.\n"
        "- Exclude already-converted users from opportunity outputs.\n"
        "- Use failure cohort as users with no events for >7 days; success cohort always overrides failure.\n"
        "- Do not use UNION/UNION ALL (Inspector rejects UNION queries).\n"
        "- Do not use CASE expressions.\n"
        "- Do not use CAST(...); Inspector rejects CAST expressions.\n"
        "- Do not join events.distinct_id to persons.id (String vs UUID mismatch risk in Inspector/PostHog).\n"
        "- Prefer events.distinct_id as person-level grain unless a compatible persons key is explicitly verified.\n"
        "- Do not use blocked functions/patterns: arrayJoin, numbers*, remote*, url(), CROSS JOIN, INTO OUTFILE, LOAD_FILE.\n"
        "- For recent vs baseline comparisons, use a single SELECT with conditional aggregation.\n"
        "- Do not call schema discovery tools in this stage; bootstrap already handled schema discovery.\n"
        "- Do not spend query budget on rediscovery checks such as top events, date range scans, event existence checks, or generic top pages.\n"
        "- The single run_readonly_query call must validate the current candidate hypothesis itself.\n"
        "- Never emit prose like 'I will now...' or 'the experiment is complete'. Return compact JSON only.\n"
        "\n"
        "Window priors:\n"
        "- event-path precursors should be tested in both same-session and 7-day lookback windows;\n"
        "- include cohort discrimination evidence (success vs failure vs grey) in promotion_evidence.\n"
        "\n"
        "Workflow each iteration:\n"
        "1) Call get_signal_objective_compact and get_warehouse_profile_compact first.\n"
        "   If success_proxy_mode is present, treat success_event_name as a conversion proxy label rather than a literal event that must exist in the events table.\n"
        "2) Load existing signals.\n"
        "3) Use prior stored candidates/reviews to avoid repeating the same hypothesis.\n"
        "4) Propose one candidate hypothesis tied to success_event_name.\n"
        "5) Produce query_template + parameter_set + rationale.\n"
        "6) Include target_event and cohort evidence fields in candidate.\n"
        "7) If prior iterations failed or had zero evidence, change the hypothesis rather than restating it.\n"
        "8) Do not attempt to terminate the loop yourself; just complete one iteration.\n"
        "9) Call dedupe_candidate.\n"
        "10) Call validate_sql_policy.\n"
        "11) If policy allows, call run_readonly_query once.\n"
        "12) Call store_candidate_signal with full candidate draft.\n"
        "\n"
        "Candidate fields must include:\n"
        "name, entity_grain, time_window, comparison_baseline, query_template,\n"
        "parameter_set, interpretation, promotion_evidence, target_event, status.\n"
        "promotion_evidence must always be a JSON object, never a string.\n"
        "After store_candidate_signal, return only compact ack JSON with candidate name, status, and stored_as when available.\n"
    ),
)

reviewer_agent = LlmAgent(
    name="signal_reviewer_agent",
    model=REVIEWER_MODEL,
    tools=[get_signal_objective_compact, store_candidate_signal],
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
        "- Reject candidates not tied to success_event_name, except when success_proxy_mode is present; in that case allow proxy-aligned hypotheses without a literal event-name match.\n"
        "- Reject candidates missing cohort discrimination evidence.\n"
        "- Approve only reusable conversion-oriented signal checks.\n"
        "- Reject any candidate that uses UNION/UNION ALL.\n"
        "- Reject any candidate that uses CAST(...).\n"
        "- Reject candidates that join events.distinct_id to persons.id.\n"
        "- Reject candidates using CASE expressions or blocked functions/patterns (arrayJoin/numbers/remote/url/CROSS JOIN/etc.).\n"
        "- If no explorer candidate is available in this turn, return a non-fatal continue decision (do not fabricate errors).\n"
        "- Deterministic policy tools are ultimate safety authority.\n"
        "- Never emit narrative prose or completion summaries. Return compact JSON only.\n"
        "\n"
        "Workflow:\n"
        "1) Call get_signal_objective_compact to load success/failure priors.\n"
        "   If success_proxy_mode is present, interpret success_event_name as a proxy conversion label rather than a required literal event.\n"
        "2) Build ReviewDecision JSON with decision=approve|reject and promotion_readiness boolean.\n"
        "3) Call store_candidate_signal once with candidate + review_decision.\n"
        "4) Set promoted=true,status=promoted only when candidate is reusable and execution evidence is meaningful.\n"
        "5) Do not attempt to terminate the loop; only review the current candidate.\n"
        "6) Return concise ack JSON with decision, promoted flag, and stored_as when available.\n"
    ),
)

iteration_pipeline_agent = SequentialAgent(
    name="signal_iteration_pipeline",
    sub_agents=[explorer_agent, reviewer_agent],
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
    sub_agents=[signal_prep_agent, discovery_loop_agent, finalize_agent],
)

# Best-effort RunConfig export for runners that support it.
try:
    from google.adk.runners import RunConfig

    run_config = RunConfig(max_llm_calls=int(os.getenv("SIGNAL_AGENT_MAX_LLM_CALLS", "96")))
except Exception:
    run_config = None

# Backward-compatible aliases used in prior runs.
signal_worker = root_agent
signal_judge = reviewer_agent
