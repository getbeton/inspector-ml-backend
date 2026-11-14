"""Run the ADK built-in (11-criteria) evaluator against a golden eval set.

This is the `make eval-run` entrypoint. It runs ONE model profile at a time.
Model-vs-model is done by running this once per profile (see eval_compare.py),
not via a native ADK compare mode.

How model parameterization works
---------------------------------
The upsell_ranker agents build their LiteLlm() instances at *module import* from
per-role env vars (UPSELL_AGENT_MODEL, DWH_ANALYST_MODEL, SIGNAL_AGENT_MODEL,
SIGNAL_REVIEWER_MODEL — see each agent.py). So to swap models we set those env
vars from the chosen profile in `scripts/evals/model_profiles.json` BEFORE
importing the agent module. That is exactly what this script does.

What it evaluates
-----------------
`AgentEvaluator.evaluate_eval_set` loads the agent module, replays each
EvalCase's user turns, and scores the agent's actual responses against the
golden `final_response` / `tool_uses` using the criteria in
`golden/test_config.json`. ADK 1.19 supports the metric set:
tool_trajectory_avg_score, response_match_score (ROUGE), final_response_match_v2
(LLM judge), safety_v1, and the rest of the 11-criteria suite.

Usage
-----
    python eval_run.py --profile gemini-3-flash
    python eval_run.py --profile opus-baseline --eval-set golden/seat_based_smoke.evalset.json
    python eval_run.py --profile gemini-3-flash --json-out artifacts/evals/run.json

Credentials / runtime (NOT required to import this file, required to run):
    source ~/.claude/secrets/mason-flash3.env   # Langfuse keys (optional here)
    export ANTHROPIC_API_KEY=...                 # for opus-baseline
    export GEMINI_API_KEY=... (or GOOGLE_API_KEY) # for gemini-3-flash
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

# v0.0.2 root = scripts/evals/eval_run.py -> ../../
V0_DIR = Path(__file__).resolve().parents[2]
EVALS_DIR = Path(__file__).resolve().parent
PROFILES_PATH = EVALS_DIR / "model_profiles.json"
DEFAULT_EVAL_SET = EVALS_DIR / "golden" / "seat_based_smoke.evalset.json"
DEFAULT_CONFIG = EVALS_DIR / "golden" / "test_config.json"

# The ADK app module path. `adk api_server ... v0.0.2` loads `upsell_ranker`
# from this dir; mirror that so AgentEvaluator can import the same agent.
AGENT_MODULE = "upsell_ranker"


def _load_profiles() -> Dict[str, Any]:
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))


def _apply_profile(profile_name: str) -> Dict[str, str]:
    """Set the per-role model env vars for `profile_name`. Returns the env it set."""
    profiles = _load_profiles()["profiles"]
    if profile_name not in profiles:
        raise SystemExit(
            f"unknown profile '{profile_name}'. known: {', '.join(profiles)}"
        )
    env = profiles[profile_name]["env"]
    for key, value in env.items():
        os.environ[key] = value
    # Make the agent package importable the same way `adk` does.
    if str(V0_DIR) not in sys.path:
        sys.path.insert(0, str(V0_DIR))
    return env


async def _run(eval_set_path: Path, config_path: Path, num_runs: int) -> Dict[str, Any]:
    # Imported AFTER env is applied so the agent picks up the profile models.
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    # AgentEvaluator.evaluate runs assertions and raises on failure. We wrap it
    # so a failing threshold is reported, not just thrown, and we capture the
    # pass/fail signal for the comparison table.
    result: Dict[str, Any] = {"passed": True, "error": None}
    try:
        await AgentEvaluator.evaluate(
            agent_module=AGENT_MODULE,
            eval_dataset_file_path_or_dir=str(eval_set_path),
            num_runs=num_runs,
            initial_session_file=None,
        )
    except AssertionError as exc:  # threshold failure
        result["passed"] = False
        result["error"] = f"criteria not met: {exc}"
    except Exception as exc:  # import / runtime failure
        result["passed"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", required=True, help="model profile name from model_profiles.json")
    p.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET), help="path to .evalset.json")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to test_config.json (criteria)")
    p.add_argument("--num-runs", type=int, default=1, help="repeat each case N times (variance smoothing)")
    p.add_argument("--json-out", default="", help="optional path to write a machine-readable result")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    eval_set_path = Path(args.eval_set)
    config_path = Path(args.config)
    if not eval_set_path.is_file():
        sys.stderr.write(f"ERROR: eval set not found: {eval_set_path}\n")
        return 2

    applied = _apply_profile(args.profile)
    sys.stderr.write(
        f">> profile={args.profile} models={json.dumps(applied)}\n"
        f">> eval_set={eval_set_path}\n>> config={config_path}\n"
    )

    result = asyncio.run(_run(eval_set_path, config_path, args.num_runs))
    result["profile"] = args.profile
    result["eval_set"] = str(eval_set_path)
    result["models"] = applied

    out = json.dumps(result, indent=2)
    sys.stdout.write(out + "\n")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(out, encoding="utf-8")
        sys.stderr.write(f">> wrote {args.json_out}\n")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
