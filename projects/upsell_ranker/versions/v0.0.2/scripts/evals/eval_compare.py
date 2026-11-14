"""Run the SAME golden eval set across 2+ model profiles and emit a comparison.

This is the `make eval-compare` entrypoint. ADK has no native model-vs-model
mode, so we run eval_run.py once per profile and diff the results. Each profile
runs in its OWN subprocess so the per-role model env vars are applied cleanly
before the agent module is imported (a single process can only import the agent
once, baking in whichever model was set first).

Output: a markdown + JSON comparison table (profile × pass/fail × models used).

Usage
-----
    python eval_compare.py                                  # uses default_compare from model_profiles.json
    python eval_compare.py --profiles gemini-3-flash opus-baseline
    python eval_compare.py --eval-set golden/seat_based_smoke.evalset.json --out-dir artifacts/evals
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

EVALS_DIR = Path(__file__).resolve().parent
PROFILES_PATH = EVALS_DIR / "model_profiles.json"
EVAL_RUN = EVALS_DIR / "eval_run.py"
DEFAULT_EVAL_SET = EVALS_DIR / "golden" / "seat_based_smoke.evalset.json"
V0_DIR = EVALS_DIR.parents[1]
DEFAULT_OUT = V0_DIR / "artifacts" / "evals"


def _default_profiles() -> List[str]:
    data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    return data.get("default_compare") or list(data.get("profiles", {}).keys())


def _run_one(profile: str, eval_set: Path, out_dir: Path) -> Dict[str, Any]:
    json_out = out_dir / f"run_{profile}.json"
    cmd = [
        sys.executable, str(EVAL_RUN),
        "--profile", profile,
        "--eval-set", str(eval_set),
        "--json-out", str(json_out),
    ]
    sys.stderr.write(f"\n>> ===== profile={profile} =====\n")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stderr.write(proc.stderr)
    if json_out.is_file():
        result = json.loads(json_out.read_text(encoding="utf-8"))
    else:
        # eval_run failed before writing; synthesize a failure record.
        result = {
            "profile": profile,
            "passed": False,
            "error": (proc.stdout.strip() or proc.stderr.strip() or "no output")[:500],
            "models": {},
        }
    result["exit_code"] = proc.returncode
    return result


def _table_md(results: List[Dict[str, Any]], eval_set: Path) -> str:
    lines = [
        f"# Mason eval comparison — `{eval_set.name}`",
        "",
        "| Profile | Result | Signal model | Reviewer model | Note |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        status = "PASS" if r.get("passed") else "FAIL"
        models = r.get("models", {})
        sig = models.get("SIGNAL_AGENT_MODEL", "?")
        rev = models.get("SIGNAL_REVIEWER_MODEL", "?")
        note = (r.get("error") or "criteria met").replace("\n", " ")[:80]
        lines.append(f"| `{r['profile']}` | {status} | `{sig}` | `{rev}` | {note} |")
    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profiles", nargs="*", default=None, help="profiles to compare (default: default_compare)")
    p.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT))
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    profiles = args.profiles or _default_profiles()
    eval_set = Path(args.eval_set)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = [_run_one(p, eval_set, out_dir) for p in profiles]

    table = _table_md(results, eval_set)
    (out_dir / "comparison.md").write_text(table, encoding="utf-8")
    (out_dir / "comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    sys.stdout.write("\n" + table + "\n")
    sys.stderr.write(f">> wrote {out_dir / 'comparison.md'} and comparison.json\n")

    # Non-zero exit if any profile errored out (distinct from a criteria FAIL,
    # which is an expected, comparable outcome).
    return 1 if any(r.get("error") and r.get("exit_code") not in (0, 1) for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
