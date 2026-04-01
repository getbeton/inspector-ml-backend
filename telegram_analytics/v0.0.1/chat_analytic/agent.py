import inspect
import os
from typing import Any, Dict

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.models.google_llm import Gemini

try:
    import agentops
except Exception:  # pragma: no cover
    agentops = None

from .tools import (
    _tool_cache_get,
    dedupe_candidate,
    describe_table,
    finalize_analysis_report,
    get_analysis_objective,
    get_database_profile,
    identify_admin_candidates,
    infer_analysis_objective,
    initialize_signal_run,
    list_tables,
    load_existing_insights,
    profile_activity_frequency_distribution,
    profile_activity_gap_distribution,
    profile_chat_activity,
    profile_first_to_next_activity,
    profile_reaction_reply_effects,
    profile_retention_windows,
    profile_user_return_gaps,
    run_readonly_query,
    sample_rows,
    store_candidate_insight,
    store_database_profile,
    store_objective_evidence,
    validate_chat_scope,
    validate_sql_policy,
)

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY and agentops is not None:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = Gemini(model=os.getenv("TELEGRAM_ANALYTICS_MODEL", "gemini-3-flash-preview"))
REVIEWER_MODEL = Gemini(
    model=os.getenv("TELEGRAM_ANALYTICS_REVIEWER_MODEL", os.getenv("TELEGRAM_ANALYTICS_MODEL", "gemini-3-flash-preview"))
)


class ScopedLoopAgent(LoopAgent):
    def _should_exit_from_state(self, ctx: Any) -> bool:
        state = getattr(getattr(ctx, "session", None), "state", None)
        if state is None:
            return False
        limits = state.get("loop_limits") or {}
        max_iterations = int(limits.get("max_iterations") or os.getenv("TELEGRAM_ANALYTICS_MAX_ITERATIONS", "8"))
        target_promoted = int(limits.get("target_promoted_insights") or os.getenv("TELEGRAM_ANALYTICS_TARGET_PROMOTED", "3"))
        max_failures = int(limits.get("too_many_failures") or os.getenv("TELEGRAM_ANALYTICS_MAX_FAILURES", "5"))
        min_iterations_before_exit = int(os.getenv("TELEGRAM_ANALYTICS_MIN_ITERATIONS_BEFORE_EXIT", "3"))
        iteration = int(state.get("iteration_index") or 0) + 1
        promoted_count = len(state.get("promoted_insights") or [])
        failures = int(state.get("failure_count") or 0)
        query_count = int(state.get("query_count") or 0)
        llm_calls_used_estimate = max(int(state.get("llm_calls_used_estimate") or 0), (iteration * 2) + 2)
        state["iteration_index"] = iteration
        state["llm_calls_used_estimate"] = llm_calls_used_estimate

        reached_limits = iteration >= max_iterations or promoted_count >= target_promoted or failures >= max_failures
        should_exit = reached_limits and iteration >= min_iterations_before_exit
        reason = "continue"
        if reached_limits and iteration < min_iterations_before_exit:
            reason = "continue_min_iterations_guard"
        elif iteration >= max_iterations:
            reason = "max_iterations_reached"
        elif promoted_count >= target_promoted:
            reason = "target_promoted_insights_reached"
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
                "target_promoted_insights": target_promoted,
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
            if author == "telegram_iteration_pipeline" and ctx is not None and self._should_exit_from_state(ctx):
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


def telegram_before_tool_callback(*args: Any, **kwargs: Any) -> Any:
    tool_name = _tool_name_from_callback_args(args, kwargs)
    tool_args = _tool_args_from_callback_args(args, kwargs)
    tool_context = kwargs.get("tool_context")
    if tool_context is None and len(args) >= 3:
        tool_context = args[2]
    if tool_context is None:
        return None
    state = getattr(tool_context, "state", None)
    if isinstance(state, dict) and tool_name:
        cached = _tool_cache_get(state, tool_name, tool_args)
        if cached is not None:
            cached["duplicate_tool_call_blocked"] = True
            return cached
    if tool_name != "run_readonly_query":
        return None
    sql = str(tool_args.get("sql") or "")
    scope = validate_chat_scope(sql, tool_context=tool_context)
    if not scope.get("allowed"):
        return {
            "ok": False,
            "error": "blocked_in_before_tool_callback",
            "violations": scope.get("violations", []),
        }
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
        kwargs["before_tool_callback"] = telegram_before_tool_callback
    if output_key and "output_key" in sig.parameters:
        kwargs["output_key"] = output_key
    return kwargs


bootstrap_agent = LlmAgent(
    name="telegram_bootstrap_agent",
    model=MODEL,
    tools=[
        initialize_signal_run,
        list_tables,
        describe_table,
        sample_rows,
        profile_chat_activity,
        identify_admin_candidates,
        profile_user_return_gaps,
        profile_first_to_next_activity,
        profile_activity_gap_distribution,
        profile_activity_frequency_distribution,
        store_objective_evidence,
        infer_analysis_objective,
        store_database_profile,
    ],
    **_llm_kwargs(output_key="telegram_bootstrap"),
    instruction=(
        "You are the bootstrap stage for a Telegram chat analytics agent.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only.\n"
        "- Use tools only; never invent schema.\n"
        "- Tool calls must be sequential: call, inspect, then decide the next call.\n"
        "- Do not call the same tool twice with identical arguments.\n"
        "- Never repeat describe_table for the same table once you already have the columns.\n"
        "- Keep samples small to avoid context pollution.\n"
        "- Return compact JSON only after tool work is complete.\n"
        "\n"
        "Workflow:\n"
        "1) Call initialize_signal_run first and pass both session_id and target_chat_uuid from input.\n"
        "2) Call list_tables, then describe_table for likely chat, message, reaction, membership, and user tables.\n"
        "3) Call sample_rows on a few likely tables with limit=5.\n"
        "4) Call profile_chat_activity only if you already know likely message table, chat column, and timestamp column.\n"
        "5) Call identify_admin_candidates if you know likely message/user/chat columns.\n"
        "6) Infer the best schema mapping for chat_uuid, user identifiers, message ids, replies, reactions, joins, and leaves.\n"
        "7) Call store_database_profile exactly once with the inferred schema profile.\n"
        "8) Profile activity cadence for this chat: use profile_user_return_gaps, profile_first_to_next_activity, profile_activity_gap_distribution, and profile_activity_frequency_distribution when the needed columns are known.\n"
        "9) Compare candidate success/failure thresholds using observed min/max/mean/median/percentiles rather than hardcoded values.\n"
        "10) Call store_objective_evidence to persist the distribution evidence you used.\n"
        "11) Call infer_analysis_objective exactly once after the evidence is stored.\n"
        "12) Return short JSON with key tables, key columns, chosen success/failure definitions, threshold values, and confidence.\n"
        "\n"
        "Objective inference rules:\n"
        "- Do not hardcode inactivity thresholds.\n"
        "- Use observed gap distributions in the target chat to decide what counts as drop-off/failure.\n"
        "- Use observed return/frequency behavior to decide what counts as success.\n"
        "- Record why competing threshold choices were rejected.\n"
    ),
)

explorer_agent = LlmAgent(
    name="telegram_explorer_agent",
    model=MODEL,
    tools=[
        load_existing_insights,
        get_database_profile,
        get_analysis_objective,
        dedupe_candidate,
        validate_chat_scope,
        validate_sql_policy,
        run_readonly_query,
        profile_retention_windows,
        profile_reaction_reply_effects,
        store_candidate_insight,
    ],
    **_llm_kwargs(output_key="telegram_explorer_iteration"),
    instruction=(
        "You are the Explorer in a two-LLM Telegram engagement analysis loop.\n"
        "\n"
        "Mission:\n"
        "- Propose exactly one actionable engagement insight candidate per iteration.\n"
        "- Every candidate must be scoped to the target chat_uuid only.\n"
        "- Focus on precursors that plausibly increase later messaging, replying, or reacting.\n"
        "- You are the only role allowed to author SQL.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only SQL only.\n"
        "- Keep queries cheap, bounded, and interpretable.\n"
        "- Use discovered PostgreSQL schema only.\n"
        "- Exclude admin candidates from user-behavior conclusions when objective includes them.\n"
        "- Prefer cohort-style comparisons over raw totals.\n"
        "- Do not produce generic descriptive summaries unless they support a concrete hypothesis.\n"
        "- Include the target chat_uuid filter directly in the SQL you execute.\n"
        "- Never emit narrative prose. Return compact JSON only.\n"
        "\n"
        "Recommended hypothesis families:\n"
        "- Early reactions to a newcomer's first message increase retention.\n"
        "- Fast replies to initial posts increase next-day or next-week activity.\n"
        "- Users who receive a reply or reaction within a short window are more likely to react later.\n"
        "- Joining followed by message/reaction within the same day predicts multi-week activity.\n"
        "- Certain reply network patterns or lag times correlate with drop-off.\n"
        "\n"
        "Workflow:\n"
        "1) Call get_analysis_objective and get_database_profile first.\n"
        "2) Load existing insights to avoid repeating prior hypotheses.\n"
        "3) Propose one candidate tied to later engagement in the target chat.\n"
        "4) Call dedupe_candidate.\n"
        "5) Call validate_chat_scope.\n"
        "6) Call validate_sql_policy.\n"
        "7) If both pass, call run_readonly_query once.\n"
        "8) Call store_candidate_insight with the full candidate draft.\n"
        "9) Return JSON only.\n"
    ),
)

reviewer_agent = LlmAgent(
    name="telegram_reviewer_agent",
    model=REVIEWER_MODEL,
    tools=[get_analysis_objective, store_candidate_insight],
    **_llm_kwargs(output_key="telegram_reviewer_iteration"),
    instruction=(
        "You are the Reviewer in a two-LLM Telegram engagement loop.\n"
        "Review the Explorer candidate for safety, target-chat scope, temporal logic, non-triviality, and admin usefulness.\n"
        "\n"
        "Rules:\n"
        "- Reject vague entity grain.\n"
        "- Reject findings not scoped to the target chat.\n"
        "- Reject descriptive summaries that do not link a precursor to later engagement.\n"
        "- Reject claims without interpretable evidence.\n"
        "- Approve only reusable, actionable engagement insights.\n"
        "- Deterministic policy tools are the ultimate safety authority.\n"
        "- Return compact JSON only.\n"
        "\n"
        "Workflow:\n"
        "1) Call get_analysis_objective.\n"
        "2) Build ReviewDecision JSON with decision=approve|reject.\n"
        "3) Call store_candidate_insight once with candidate + review_decision.\n"
        "4) Set promoted=true,status=promoted only when the candidate is actionable and evidence is meaningful.\n"
        "5) Do not attempt to terminate the loop.\n"
    ),
)

iteration_pipeline_agent = SequentialAgent(
    name="telegram_iteration_pipeline",
    sub_agents=[explorer_agent, reviewer_agent],
)

discovery_loop_agent = ScopedLoopAgent(
    name="telegram_discovery_loop",
    sub_agents=[iteration_pipeline_agent],
    max_iterations=int(os.getenv("TELEGRAM_ANALYTICS_MAX_ITERATIONS", "8")),
)

finalize_agent = LlmAgent(
    name="telegram_finalize_agent",
    model=MODEL,
    tools=[finalize_analysis_report],
    **_llm_kwargs(output_key="telegram_final_report"),
    instruction=(
        "You are the finalization stage.\n"
        "Call finalize_analysis_report exactly once.\n"
        "Return JSON with summary_path and report metrics only.\n"
    ),
)

root_agent = SequentialAgent(
    name="telegram_chat_analytic_agent",
    sub_agents=[bootstrap_agent, discovery_loop_agent, finalize_agent],
)

try:
    from google.adk.runners import RunConfig

    run_config = RunConfig(max_llm_calls=int(os.getenv("TELEGRAM_ANALYTICS_MAX_LLM_CALLS", "96")))
except Exception:
    run_config = None

signal_worker = root_agent
signal_judge = reviewer_agent
