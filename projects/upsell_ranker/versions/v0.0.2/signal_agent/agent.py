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
    apply_latest_review_decision_to_state,
    finalize_experiment_report,
    prepare_signal_inputs,
    submit_candidate_signal,
    validate_sql_policy,
)
from shared.skills import build_skill_toolset

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = build_gemini("SIGNAL_AGENT_MODEL", "gemini-3-flash-preview")
REVIEWER_MODEL = build_gemini(
    "SIGNAL_REVIEWER_MODEL",
    os.getenv("SIGNAL_AGENT_MODEL", "gemini-3-flash-preview"),
)

_SKILL_TOOLSET = build_skill_toolset()
_SKILL_TOOLS: list[Any] = [_SKILL_TOOLSET] if _SKILL_TOOLSET is not None else []
_SKILL_INSTRUCTION = (
    "You have access to expert skill tools (list_skills, load_skill, load_skill_resource).\n"
    "Before designing a new hypothesis, prioritization, or review heuristic, call list_skills\n"
    "once and load any directly relevant skill before drafting. Do not load the same skill\n"
    "more than once per iteration.\n\n"
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
        apply_latest_review_decision_to_state(state)
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


def signal_tool_not_found_callback(*args: Any, **kwargs: Any) -> Any:
    # ADK raises ValueError("Tool '<name>' not found.\nAvailable tools: ...") from
    # _get_tool when an LLM hallucinates a function name. Return a structured
    # function_response so the loop can continue instead of 500-ing the whole /run.
    # Use *args/**kwargs to stay compatible across ADK callback signatures
    # (current ADK invokes this as callback(tool=..., args=..., tool_context=..., error=...)).
    tool = kwargs.get("tool") if "tool" in kwargs else (args[0] if args else None)
    error = kwargs.get("error")
    if error is None:
        for a in args[1:]:
            if isinstance(a, Exception):
                error = a
                break
    if not isinstance(error, ValueError):
        return None
    tool_name = getattr(tool, "name", "") or ""
    if not str(error).startswith(f"Tool '{tool_name}' not found."):
        return None
    return {
        "ok": False,
        "error": "tool_not_available_in_this_stage",
        "tool_name": tool_name,
        "hint": (
            "This tool is not registered on the current agent. Re-read the agent "
            "instruction for the allowed tool list and call only an allowed tool. "
            "Do not invent finalize/terminate tools - the loop controller exits "
            "deterministically once limits or promotion targets are met."
        ),
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
    # on_tool_error_callback is a pydantic model field; LlmAgent.__init__ is the
    # generated pydantic constructor so inspect.signature.parameters is empty.
    # Check model_fields directly so the callback actually gets attached.
    model_fields = getattr(LlmAgent, "model_fields", None) or {}
    if "on_tool_error_callback" in model_fields:
        kwargs["on_tool_error_callback"] = signal_tool_not_found_callback
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
    tools=[submit_candidate_signal, *_SKILL_TOOLS],
    **_llm_kwargs(output_key="signal_explorer_iteration"),
    instruction=(
        _SKILL_INSTRUCTION +
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
        "- Do not append a SETTINGS clause (e.g. `SETTINGS allow_experimental_analyzer=1`). PostHog HogQL does not support it and rejects the query with `Unsupported: SelectStmt.settingsClause()`.\n"
        "- For recent vs baseline comparisons, use a single SELECT with conditional aggregation.\n"
        "- Do not call schema discovery tools in this stage; bootstrap already handled schema discovery.\n"
        "- Do not spend query budget on rediscovery checks such as top events, date range scans, event existence checks, or generic top pages.\n"
        "- Never emit prose like 'I will now...' or 'the experiment is complete'. Return compact JSON only.\n"
        "\n"
        "Window priors:\n"
        "- event-path precursors should be tested in both same-session and 7-day lookback windows;\n"
        "- include cohort discrimination evidence (success vs failure vs grey) in promotion_evidence.\n"
        "\n"
        "State available to you:\n"
        "- signal_context_digest contains compact objective, warehouse constraints, existing signal fingerprints, loop limits, and playbook snippets.\n"
        "- candidate_signals and review_decisions contain prior in-run attempts.\n"
        "\n"
        "Workflow each iteration:\n"
        "1) Read signal_context_digest from session state before drafting.\n"
        "2) Propose exactly one candidate hypothesis tied to success_event_name or an allowed success proxy.\n"
        "3) Produce query_template + parameter_set + rationale.\n"
        "4) Include target_event and cohort evidence fields in candidate.\n"
        "5) If prior iterations failed or had zero evidence, change the hypothesis rather than restating it.\n"
        "6) Call submit_candidate_signal exactly once with the full candidate draft.\n"
        "7) Do not call schema discovery, dedupe, SQL validation, query execution, or storage tools directly.\n"
        "8) If submit_candidate_signal returns duplicate, policy_blocked, query_failed, or invalid_candidate, return compact JSON that acknowledges the status and what should change next iteration.\n"
        "9) Do not attempt to terminate the loop yourself; just complete one iteration.\n"
        "\n"
        "Candidate fields must include:\n"
        "name, entity_grain, time_window, comparison_baseline, query_template,\n"
        "parameter_set, interpretation, promotion_evidence, target_event, status.\n"
        "promotion_evidence must always be a JSON object, never a string.\n"
        "After submit_candidate_signal, return only compact ack JSON.\n"
    ),
)

reviewer_agent = LlmAgent(
    name="signal_reviewer_agent",
    model=REVIEWER_MODEL,
    tools=[*_SKILL_TOOLS],
    **_llm_kwargs(output_key="signal_reviewer_iteration"),
    instruction=(
        _SKILL_INSTRUCTION +
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
        "State available to you:\n"
        "- signal_context_digest contains compact objective, warehouse constraints, and playbook snippets.\n"
        "- latest_pending_candidate_for_review contains the latest pending candidate, including compact execution evidence.\n"
        "\n"
        "Workflow:\n"
        "1) Read signal_context_digest and latest_pending_candidate_for_review from session state.\n"
        "2) Review the latest pending candidate only.\n"
        "3) Return JSON only with candidate_id, decision, promotion_readiness, reasons, required_fixes, and confidence.\n"
        "4) Do not persist anything yourself and do not attempt to terminate the loop.\n"
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
    tools=[finalize_experiment_report, *_SKILL_TOOLS],
    **_llm_kwargs(output_key="signal_final_report"),
    instruction=(
        _SKILL_INSTRUCTION +
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
