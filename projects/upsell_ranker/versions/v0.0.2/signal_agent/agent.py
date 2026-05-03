import inspect
import os
from typing import Any, Dict

import agentops
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.models.lite_llm import LiteLlm

from shared.memory_bank import render_for as _memory_bank_for
from shared.observability import init_observability

# ADK loads each agent module directly without importing the v0.0.2 package
# `__init__.py`, so we wire observability at agent-import time. The function
# is idempotent and logs once on first call.
init_observability()

from .tools import (
    dedupe_candidate,
    describe_table,
    finalize_experiment_report,
    get_pending_candidates,
    get_signal_objective,
    get_warehouse_profile,
    infer_signal_objective,
    initialize_signal_run,
    list_tables,
    load_existing_signals,
    profile_events,
    propose_batch_candidates,
    rice_prioritize_batch,
    run_readonly_query,
    sample_rows,
    store_candidate_signal,
    store_warehouse_profile,
    validate_sql_policy,
)

AGENTOPS_API_KEY = os.getenv("AGENTOPS_API_KEY")
if AGENTOPS_API_KEY:
    agentops.init(api_key=AGENTOPS_API_KEY, default_tags=["google adk"])

MODEL = LiteLlm(model=os.getenv("SIGNAL_AGENT_MODEL", "anthropic/claude-opus-4-6"))
REVIEWER_MODEL = LiteLlm(
    model=os.getenv("SIGNAL_REVIEWER_MODEL", os.getenv("SIGNAL_AGENT_MODEL", "anthropic/claude-opus-4-6"))
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
            # SequentialAgent never emits events with its own author name —
            # only leaf LlmAgents do.  Check for the reviewer (last sub-agent
            # in signal_iteration_pipeline) to detect iteration boundaries.
            is_iteration_end = author == "signal_reviewer_agent" and not any(
                getattr(p, "function_call", None) is not None
                for p in getattr(getattr(event, "content", None), "parts", [])
            )
            if is_iteration_end and ctx is not None and self._should_exit_from_state(ctx):
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


def _sanitize_history_for_anthropic(callback_context, llm_request):
    """No-op — ADK v1.19.0 already reformats cross-agent tool calls as
    '[agent_name] said:' text messages via _present_other_agent_message(),
    satisfying Anthropic's strict tool_use→tool_result pairing without
    manual sanitization.  Previous versions of this callback stripped the
    agent's own tool history because ADK inserts instruction content
    (role='user' with text) after tool responses, which shifted the
    boundary detection."""
    return None


def _llm_kwargs(output_key: str = "", sanitize_history: bool = False) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    try:
        sig = inspect.signature(LlmAgent.__init__)
    except Exception:
        return kwargs
    if "before_tool_callback" in sig.parameters:
        kwargs["before_tool_callback"] = signal_before_tool_callback
    if output_key and "output_key" in sig.parameters:
        kwargs["output_key"] = output_key
    if sanitize_history and "before_model_callback" in sig.parameters:
        kwargs["before_model_callback"] = _sanitize_history_for_anthropic
    return kwargs


bootstrap_agent = LlmAgent(
    name="signal_bootstrap_agent",
    model=MODEL,
    before_model_callback=_sanitize_history_for_anthropic,
    tools=[
        initialize_signal_run,
        list_tables,
        describe_table,
        sample_rows,
        profile_events,
        infer_signal_objective,
        store_warehouse_profile,
    ],
    **_llm_kwargs(output_key="signal_bootstrap"),
    instruction=(
        _memory_bank_for("bootstrap") + "\n"
        "You are the bootstrap stage for signal_agent.\n"
        "\n"
        "Workflow step 0 (mandatory): read the memory-bank section above —\n"
        "particularly running-lean — to ground your interpretation of what\n"
        "'success_event' means for this workspace before you start probing\n"
        "the schema. The memory bank tells you which user actions count as\n"
        "validated learning vs vanity events.\n"
        "\n"
        "Hard constraints:\n"
        "- Read-only behavior only.\n"
        "- Use tools only, no invented schema.\n"
        "- Assume PostHog-style events/persons are likely but verify via table discovery.\n"
        "- Tool calls must be sequential: call -> inspect response -> next call.\n"
        "- Do not call the same tool twice with identical arguments.\n"
        "- Never emit narrative prose after tool calls; either call the next tool or return final JSON.\n"
        "\n"
        "Workflow:\n"
        "1) Call initialize_signal_run first and pass session_id from input.\n"
        "2) Call list_tables, then describe_table for likely events/persons tables.\n"
        "3) Call sample_rows for one likely events table and one likely persons table when available.\n"
        "4) Call profile_events for one likely events table.\n"
        "5) Infer likely semantic columns (time/event/person/session/group/properties).\n"
        "6) Call infer_signal_objective exactly once (hint defaults to user_signup).\n"
        "   Apply the running-lean framing: prefer events that mark a user crossing\n"
        "   from trial intent to confirmed value (e.g. 'seat_activated', 'first_payment',\n"
        "   'project_published') over vanity events ('page_view', 'login').\n"
        "7) Call store_warehouse_profile exactly once and include objective fields.\n"
        "8) Return short JSON only with success_event_name, failure rule, and key columns.\n"
    ),
)

explorer_agent = LlmAgent(
    name="signal_explorer_agent",
    model=MODEL,
    tools=[
        load_existing_signals,
        get_warehouse_profile,
        get_signal_objective,
        dedupe_candidate,
        store_candidate_signal,
        validate_sql_policy,
        run_readonly_query,
    ],
    **_llm_kwargs(output_key="signal_explorer_iteration"),
    instruction=(
        _memory_bank_for("explorer") + "\n"
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
        "- Use allowlisted base tables discovered via list_tables/describe_table.\n"
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
        "- Never emit prose like 'I will now...' or 'the experiment is complete'. Return compact JSON only.\n"
        "\n"
        "Window priors:\n"
        "- event-path precursors should be tested in both same-session and 7-day lookback windows;\n"
        "- include cohort discrimination evidence (success vs failure vs grey) in promotion_evidence.\n"
        "\n"
        "Workflow each iteration:\n"
        "1) Call get_signal_objective and get_warehouse_profile first.\n"
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
        "\n"
        "CRITICAL — promotion_evidence format (required for promotion to succeed):\n"
        "promotion_evidence must be a dict with numeric cohort metrics from your query.\n"
        "Include AT LEAST these keys: success_cohort_size, failure_cohort_size,\n"
        "grey_cohort_size, conversion_lift, conversion_rate_delta, precision_proxy.\n"
        "Example:\n"
        "  \"promotion_evidence\": {\n"
        "    \"success_cohort_size\": 41,\n"
        "    \"failure_cohort_size\": 2795,\n"
        "    \"grey_cohort_size\": 120,\n"
        "    \"conversion_lift\": 2.87,\n"
        "    \"conversion_rate_delta\": 0.065,\n"
        "    \"precision_proxy\": 0.098\n"
        "  }\n"
        "\n"
        "CRITICAL — target_event must EXACTLY match the success_event_name from\n"
        "get_signal_objective (e.g. 'user_signup'). Case-sensitive exact match.\n"
        "Return JSON only.\n"
    ),
)

reviewer_agent = LlmAgent(
    name="signal_reviewer_agent",
    model=REVIEWER_MODEL,
    tools=[get_signal_objective, store_candidate_signal],
    **_llm_kwargs(output_key="signal_reviewer_iteration"),
    instruction=(
        _memory_bank_for("reviewer") + "\n"
        "You are the Reviewer in a two-LLM loop.\n"
        "Review Explorer candidate for safety semantics, schema plausibility, grain clarity,\n"
        "temporal logic, nontriviality, and rerunnability.\n"
        "\n"
        "Rules:\n"
        "- Reject vague grain.\n"
        "- Reject if no meaningful temporal logic.\n"
        "- Reject trivial descriptive summaries.\n"
        "- Reject candidates not tied to success_event_name.\n"
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
        "1) Call get_signal_objective to load success/failure priors.\n"
        "2) Build ReviewDecision JSON with decision=approve|reject and promotion_readiness boolean.\n"
        "3) Call store_candidate_signal once with candidate + review_decision.\n"
        "4) Set promoted=true,status=promoted only when candidate is reusable and execution evidence is meaningful.\n"
        "5) When promoting, PRESERVE the explorer's promotion_evidence dict (must contain\n"
        "   success_cohort_size, failure_cohort_size, grey_cohort_size, conversion_lift,\n"
        "   conversion_rate_delta, precision_proxy). Also set target_event to exactly match\n"
        "   the success_event_name from get_signal_objective.\n"
        "6) Do not attempt to terminate the loop; only review the current candidate.\n"
        "7) Return concise JSON decision only.\n"
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


# ── Batched (single-pass) Explorer + Reviewer ─────────────────────────────
#
# Replaces the LoopAgent-driven iterative path. Explorer emits N candidates
# in one LLM round; `propose_batch_candidates` validates/executes/stores all
# of them deterministically. Reviewer reads the stored batch, ranks with RICE,
# and `rice_prioritize_batch` applies promotion decisions.
#
# Target call budget per scenario (validated via Langfuse trace count, T-C):
#   Explorer ≤ 8 LLM calls (down from 51) — typically 3–4.
#   Reviewer ≤ 5 LLM calls (down from 22) — typically 2–3.

batched_explorer_agent = LlmAgent(
    name="signal_batched_explorer_agent",
    model=MODEL,
    tools=[
        load_existing_signals,
        get_warehouse_profile,
        get_signal_objective,
        propose_batch_candidates,
    ],
    **_llm_kwargs(output_key="signal_batched_explorer"),
    instruction=(
        _memory_bank_for("explorer") + "\n"
        "You are the Explorer in a single-pass batched signal discovery pipeline.\n"
        "\n"
        "Workflow step 0 (mandatory): re-read the memory-bank sections above —\n"
        "particularly hypotheses-generation and rice-prioritization — before\n"
        "drafting any SQL. Cite the doc(s) you applied in each candidate's\n"
        "`interpretation` field by short name, e.g.\n"
        "  \"Per hypotheses-generation, intent precursors with conversion\n"
        "  lift > 1.5 are higher-quality.\"\n"
        "If a hypothesis contradicts the docs, flag it explicitly.\n"
        "\n"
        "Mission:\n"
        "- Propose AT LEAST 30 candidate signals across multiple batches.\n"
        "- Each call to propose_batch_candidates SHOULD CONTAIN at most\n"
        "  " + str(int(os.getenv('SIGNAL_AGENT_BATCH_MAX_CANDIDATES', '10'))) + " candidates.\n"
        "  This is the per-call width: smaller widths give you more thinking\n"
        "  time per candidate (each candidate's SQL + interpretation gets a\n"
        "  larger share of the output budget), larger widths are cheaper per\n"
        "  candidate but force shallower per-candidate reasoning.\n"
        "- Plan the number of batches accordingly. With width=" +
        str(int(os.getenv('SIGNAL_AGENT_BATCH_MAX_CANDIDATES', '10'))) +
        " you'll need\n"
        "  " + str(max(1, 30 // max(1, int(os.getenv('SIGNAL_AGENT_BATCH_MAX_CANDIDATES', '10'))))) +
        " or more batches to clear the 30-candidate target.\n"
        "- Mason processes EVERY candidate you emit (no silent truncation);\n"
        "  the Reviewer ranks the full union after you're done. Submit only\n"
        "  candidates you'd be willing to defend.\n"
        "- Diversify mechanisms aggressively: path-pattern signals, recency\n"
        "  thresholds, intent signals, cohort comparisons, frequency signals,\n"
        "  property-value signals, and event-pair correlations. Two\n"
        "  candidates that share a hypothesis but differ only in threshold\n"
        "  count as separate candidates.\n"
        "- Each candidate must predict pre-conversion upsell opportunity for users\n"
        "  who did NOT yet complete success_event.\n"
        "- Each candidate must align to success_event_name from get_signal_objective.\n"
        "- You are the only role allowed to author SQL/HogQL.\n"
        "\n"
        "Allowed SQL subset (strict — same as before):\n"
        "- Single SELECT statement.\n"
        "- Conditional aggregations only: countIf/sumIf/uniqIf/avgIf.\n"
        "- Allowlisted base tables only (discovered via bootstrap).\n"
        "- Explicit bounded time filters and LIMIT.\n"
        "- No UNION/UNION ALL, CASE, CAST, arrayJoin, numbers*, remote*, url(), CROSS JOIN, INTO OUTFILE, LOAD_FILE.\n"
        "- Do not join events.distinct_id to persons.id.\n"
        "- Prefer events.distinct_id as person-level grain.\n"
        "\n"
        "MANDATORY column naming — emit the SIX raw cohort counts:\n"
        "Each candidate's SQL MUST SELECT columns named EXACTLY (case-insensitive):\n"
        "  signal_success    -- users WITH the signal AND has_success=1\n"
        "  signal_failure    -- users WITH the signal AND has_failure=1 (inactive ≥ 7d, no success)\n"
        "  signal_grey       -- users WITH the signal AND still in-flight (no success, recently active)\n"
        "  control_success   -- users WITHOUT the signal AND has_success=1\n"
        "  control_failure   -- users WITHOUT the signal AND has_failure=1\n"
        "  control_grey      -- users WITHOUT the signal AND in-flight\n"
        "\n"
        "These are SIX countIf-style integers per row. Mason will compute\n"
        "lift / delta / z-score / p-value / significance in Python from them.\n"
        "Do NOT emit lift / delta / precision_proxy / p_value yourself —\n"
        "the gate ignores those when the six raw counts are present.\n"
        "\n"
        "PROMOTION RULE (deterministic, applied after your SQL runs):\n"
        "A candidate is promoted iff ALL of the following hold:\n"
        "  - Two-proportion z-test on (signal_success/n_signal) vs\n"
        "    (control_success/n_control) yields p-value ≤ 0.05 (two-tailed).\n"
        "  - Lift = signal_cr / control_cr ≥ 1.10 (≥10% relative uplift).\n"
        "  - Both cohorts have n ≥ 30 (success+failure, excluding grey).\n"
        "Hardcoded scalar values for the cohort counts produce degenerate\n"
        "test statistics (n=0, zero variance, infinite z), and the gate\n"
        "rejects them automatically. There is no shortcut.\n"
        "\n"
        "Required SQL shape (compute everything from the data):\n"
        "  WITH user_stats AS (\n"
        "    SELECT distinct_id,\n"
        "           maxIf(1, event = '<success_event>') AS has_success,\n"
        "           max(timestamp) AS last_seen,\n"
        "           <signal_predicate_expr> AS has_signal\n"
        "    FROM events WHERE timestamp >= now() - INTERVAL <window> DAY\n"
        "    GROUP BY distinct_id\n"
        "  ),\n"
        "  cohorts AS (\n"
        "    SELECT *,\n"
        "      (has_success = 0 AND last_seen <  now() - INTERVAL 7 DAY) AS has_failure,\n"
        "      (has_success = 0 AND last_seen >= now() - INTERVAL 7 DAY) AS is_grey\n"
        "    FROM user_stats\n"
        "  )\n"
        "  SELECT\n"
        "    countIf(has_signal=1 AND has_success=1) AS signal_success,\n"
        "    countIf(has_signal=1 AND has_failure=1) AS signal_failure,\n"
        "    countIf(has_signal=1 AND is_grey=1)     AS signal_grey,\n"
        "    countIf(has_signal=0 AND has_success=1) AS control_success,\n"
        "    countIf(has_signal=0 AND has_failure=1) AS control_failure,\n"
        "    countIf(has_signal=0 AND is_grey=1)     AS control_grey\n"
        "  FROM cohorts\n"
        "  LIMIT 1\n"
        "\n"
        "Workflow:\n"
        "1) Call get_signal_objective AND get_warehouse_profile AND\n"
        "   load_existing_signals in parallel within a single round.\n"
        "2) Loop until stored ≥ 30 (or you've exhausted distinct mechanisms):\n"
        "   - Propose " + str(int(os.getenv('SIGNAL_AGENT_BATCH_MAX_CANDIDATES', '10'))) +
        " candidates spanning mechanisms you haven't\n"
        "     yet covered (path, recency, frequency, intent, cohort, property,\n"
        "     event-pair, threshold variants).\n"
        "   - Call propose_batch_candidates ONCE with that list.\n"
        "   - Inspect the per-candidate result (stored / duplicates /\n"
        "     execution_failed / policy_blocked) before deciding the next batch.\n"
        "3) Stop once stored ≥ 30 OR you can't think of new mechanisms —\n"
        "   the Reviewer makes the final RICE call.\n"
        "\n"
        "Each candidate dict MUST include:\n"
        "- name, entity_grain, time_window, comparison_baseline\n"
        "- query_template (single SELECT with the 6 mandatory named columns)\n"
        "- parameter_set (a dict of substitution values used when rendering the query_template; keys appear as :name placeholders in the SQL)\n"
        "- interpretation, target_event (must equal success_event_name)\n"
        "\n"
        "Output: After the batch tool call(s), return compact JSON only — a flat\n"
        "object with these integer keys: stored, duplicates, policy_blocked,\n"
        "execution_failed, invalid. No prose, no markdown.\n"
        "\n"
        "Do not call validate_sql_policy or run_readonly_query directly — \n"
        "propose_batch_candidates does that for every candidate atomically.\n"
        "Do not call store_candidate_signal directly.\n"
        "Never emit narrative prose. Return JSON only.\n"
    ),
)

batched_reviewer_agent = LlmAgent(
    name="signal_batched_reviewer_agent",
    model=REVIEWER_MODEL,
    tools=[get_signal_objective, get_pending_candidates, rice_prioritize_batch],
    **_llm_kwargs(output_key="signal_batched_reviewer"),
    instruction=(
        _memory_bank_for("reviewer") + "\n"
        "You are the Reviewer in a single-pass batched signal discovery pipeline.\n"
        "Rank ALL pending candidates with RICE in ONE batch call.\n"
        "\n"
        "Workflow step 0 (mandatory): re-read the rice-prioritization and\n"
        "cohort-retention-analysis sections in the memory bank above. Cite\n"
        "the doc(s) you applied in each candidate's `rationale` field.\n"
        "\n"
        "Workflow (≤3 LLM calls):\n"
        "1) Call get_signal_objective AND get_pending_candidates in parallel within\n"
        "   a single round to load the success target and the full candidate batch.\n"
        "2) Score every candidate with RICE:\n"
        "   - reach: estimated affected user count (raw number, not %).\n"
        "   - impact: 1=minimal, 3=high (anchored to conversion lift / cohort size).\n"
        "   - confidence: 0.0–1.0 (anchored to cohort discrimination evidence).\n"
        "   - effort: 1=trivial query rerun, 5=expensive backfill (default 1).\n"
        "   - rice_score = (reach × impact × confidence) / effort.\n"
        "   Use cohort-retention-analysis and rice-prioritization memory-bank docs\n"
        "   as the rubric.\n"
        "2a) BEFORE assigning RICE scores, look at each candidate's\n"
        "    promotion_evidence. Mason has already computed:\n"
        "      - z_score / p_value (two-proportion z-test)\n"
        "      - lift, delta_pp, signal_cr, control_cr\n"
        "      - significant flag (p ≤ 0.05 AND lift ≥ 1.10 AND n ≥ 30 each side)\n"
        "    If significant=False, set decision='skip' immediately with\n"
        "    rationale 'not statistically significant: p=<value>, lift=<value>'.\n"
        "    Don't re-rank insignificant signals — the structural gate will\n"
        "    block their promotion anyway, but skipping them keeps your\n"
        "    rankings list clean.\n"
        "3) For each candidate, set decision='promote' if it passes ALL of:\n"
        "   - clear entity grain + time window + comparison baseline\n"
        "   - meaningful temporal logic (not a one-off snapshot)\n"
        "   - tied to success_event_name\n"
        "   - has cohort discrimination evidence (the 6 named columns from Explorer)\n"
        "   - non-trivial RICE score (top half of the batch by rice_score)\n"
        "   Otherwise decision='skip'.\n"
        "4) Call rice_prioritize_batch ONCE with the full list of rankings.\n"
        "5) If rice_prioritize_batch returns promoted<target_promoted (default 3) and\n"
        "   any candidates were promote_blocked, do NOT retry — the gates are policy,\n"
        "   not LLM judgement. Final answer.\n"
        "\n"
        "Hard constraints:\n"
        "- Reject vague grain or trivial descriptive summaries.\n"
        "- Reject candidates not tied to success_event_name.\n"
        "- Reject candidates missing cohort discrimination evidence.\n"
        "- The deterministic policy tools are the ultimate safety authority — \n"
        "  rice_prioritize_batch will block promotion if cohort evidence or\n"
        "  success-target alignment is missing.\n"
        "- Never emit narrative prose or completion summaries. Return compact JSON only.\n"
        "\n"
        "Each ranking dict MUST include:\n"
        "  name (matching candidate.name), decision ('promote'|'skip'),\n"
        "  reach, impact, confidence, effort, rice_score, rationale.\n"
        "\n"
        "Output: After rice_prioritize_batch, return compact JSON only — a flat\n"
        "object with these integer keys: promoted, skipped, promote_blocked,\n"
        "not_found. No prose, no markdown.\n"
    ),
)

batched_pipeline_agent = SequentialAgent(
    name="signal_batched_pipeline",
    sub_agents=[batched_explorer_agent, batched_reviewer_agent],
)

finalize_agent = LlmAgent(
    name="signal_finalize_agent",
    model=MODEL,
    tools=[finalize_experiment_report],
    **_llm_kwargs(output_key="signal_final_report"),
    instruction=(
        _memory_bank_for("finalize") + "\n"
        "You are the finalization stage.\n"
        "\n"
        "Workflow step 0 (mandatory): read the rice-prioritization section\n"
        "in the memory bank above. Use it to frame which numbers in the\n"
        "report are worth surfacing — RICE-style (reach × impact × confidence\n"
        "÷ effort) takes precedence over raw cohort sizes when both are\n"
        "available.\n"
        "\n"
        "Call finalize_experiment_report exactly once.\n"
        "Return JSON with report metrics and summary_path only.\n"
    ),
)

def _batch_enabled() -> bool:
    raw = os.getenv("BATCH_HYPOTHESES", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


if _batch_enabled():
    root_agent = SequentialAgent(
        name="signal_agent",
        sub_agents=[bootstrap_agent, batched_pipeline_agent, finalize_agent],
    )
else:
    root_agent = SequentialAgent(
        name="signal_agent",
        sub_agents=[bootstrap_agent, discovery_loop_agent, finalize_agent],
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
