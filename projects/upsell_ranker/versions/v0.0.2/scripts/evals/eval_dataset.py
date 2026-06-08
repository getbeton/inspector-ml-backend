"""Build a Langfuse evaluation dataset FROM the dryfit rig.

This is the `make eval-dataset` entrypoint. It is the bridge between dryfit's
ground-truth manifests and Langfuse's dataset/evaluation surface:

    dryfit config (posthog_seat_based_mvp.yaml)
        --> dryfit_to_posthog.py  (in-memory generate, no DB)
        --> ground_truth.json + manifest.json   (the dryfit golden cases)
        --> THIS SCRIPT: one Langfuse dataset item per planted signal template
        --> Langfuse dataset "mason-dryfit-<scenario>" ready for evaluation runs

Each Langfuse dataset item is shaped for "given this warehouse, did Mason find
this planted signal?" evaluation:

    input  = { scenario, dataset_id, success_event, template_id, kind,
               event_names, instance_count }   (what the warehouse contains)
    expected_output = { should_promote: true, template_id, event_names }
                        (the dryfit ground truth — Mason SHOULD surface a signal
                         covering these events)
    metadata = { source: "dryfit", scenario, dataset_id, manifest_path }

Once Mason runs against the warehouse, its traces (linked via the dryfit
session_id) are attached to these items and scored with the same coverage logic
as scripts/dryfit_e2e/score.py, but inside Langfuse so runs are comparable
across model profiles over time.

Two modes
---------
  --from-manifest <dir>   : use an already-generated dryfit output dir
                            (artifacts/.../<scenario>/ with ground_truth.json).
  --generate              : run dryfit_to_posthog.py first (needs DRYFIT_DIR +,
                            unless --dry-run, PostHog Project B keys).

Usage
-----
    # Generate from dryfit and upload (needs DRYFIT_DIR, Langfuse + PostHog keys):
    source ~/.claude/secrets/mason-flash3.env
    python eval_dataset.py --generate --scenario posthog_seat_based_mvp

    # Reuse a prior dryfit run, just (re)upload to Langfuse:
    python eval_dataset.py --from-manifest ../../artifacts/dryfit_e2e/posthog_seat_based_mvp

    # Build the item list but DON'T upload (offline check, no Langfuse needed):
    python eval_dataset.py --from-manifest <dir> --no-upload --json-out items.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

EVALS_DIR = Path(__file__).resolve().parent
DRYFIT_E2E_DIR = EVALS_DIR.parent / "dryfit_e2e"
V0_DIR = EVALS_DIR.parents[1]
DEFAULT_ARTIFACTS = V0_DIR / "artifacts" / "dryfit_e2e"


def _run_dryfit_generate(scenario: str, out_dir: Path, dry_run: bool) -> Path:
    """Invoke dryfit_to_posthog.py to produce ground_truth.json + manifest.json."""
    dryfit_dir = os.getenv("DRYFIT_DIR", os.path.expanduser("~/code/dryfit"))
    config = Path(dryfit_dir) / "configs" / f"{scenario}.yaml"
    if not config.is_file():
        raise SystemExit(
            f"dryfit config not found: {config}\n"
            f"set DRYFIT_DIR (got {dryfit_dir}) or `gh repo clone getbeton/dryfit`"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(DRYFIT_E2E_DIR / "dryfit_to_posthog.py"),
        "--config", str(config),
        "--output-dir", str(out_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    sys.stderr.write(">> running dryfit: " + " ".join(cmd) + "\n")
    subprocess.run(cmd, check=True)
    return out_dir


def _build_items(ground_truth: Dict[str, Any], manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One dataset item per planted signal template (the dryfit golden cases)."""
    scenario = ground_truth.get("scenario") or manifest.get("scenario") or ""
    dataset_id = ground_truth.get("dataset_id") or manifest.get("dataset_id") or ""
    success_event = ground_truth.get("success_event") or ""

    by_template: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for inst in ground_truth.get("signals") or []:
        tid = str(inst.get("template_id") or "")
        if tid:
            by_template[tid].append(inst)

    items: List[Dict[str, Any]] = []
    for tid, instances in by_template.items():
        events = sorted(
            {
                str(n).strip().lower()
                for inst in instances
                for n in (inst.get("event_names") or [])
                if str(n).strip()
            }
        )
        kind = str(instances[0].get("kind") if instances else "")
        items.append(
            {
                "id": f"{scenario}:{tid}",
                "input": {
                    "scenario": scenario,
                    "dataset_id": dataset_id,
                    "success_event": success_event,
                    "template_id": tid,
                    "kind": kind,
                    "event_names": events,
                    "instance_count": len(instances),
                },
                "expected_output": {
                    "should_promote": True,
                    "template_id": tid,
                    "event_names": events,
                    "min_coverage": 0.5,
                },
                "metadata": {
                    "source": "dryfit",
                    "scenario": scenario,
                    "dataset_id": dataset_id,
                    "kind": kind,
                },
            }
        )
    return items


def _upload_to_langfuse(dataset_name: str, items: List[Dict[str, Any]], scenario: str) -> None:
    """Create/replace a Langfuse dataset and push one item per golden case."""
    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "langfuse SDK not installed. `pip install langfuse` "
            f"(import failed: {exc})"
        )
    missing = [k for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.getenv(k)]
    if missing:
        raise SystemExit(
            "missing Langfuse env: " + ", ".join(missing) +
            "\nsource ~/.claude/secrets/mason-flash3.env first."
        )

    client = Langfuse()  # reads LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST from env
    client.create_dataset(
        name=dataset_name,
        description=f"Mason dryfit golden cases for scenario '{scenario}'.",
        metadata={"source": "dryfit", "scenario": scenario},
    )
    for item in items:
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
            id=item["id"],  # stable id => idempotent re-upload (upsert)
        )
    client.flush()
    sys.stderr.write(f">> uploaded {len(items)} items to Langfuse dataset '{dataset_name}'\n")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", default="posthog_seat_based_mvp", help="dryfit scenario name")
    p.add_argument("--generate", action="store_true", help="run dryfit to (re)generate ground truth first")
    p.add_argument("--from-manifest", default="", help="existing dir containing ground_truth.json + manifest.json")
    p.add_argument("--dataset-name", default="", help="Langfuse dataset name (default mason-dryfit-<scenario>)")
    p.add_argument("--no-upload", action="store_true", help="build items but skip Langfuse upload")
    p.add_argument("--dry-run", action="store_true", help="with --generate: don't POST events to PostHog")
    p.add_argument("--json-out", default="", help="optional path to write the item list")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    if args.from_manifest:
        manifest_dir = Path(args.from_manifest).resolve()
    else:
        manifest_dir = (DEFAULT_ARTIFACTS / args.scenario).resolve()

    if args.generate:
        manifest_dir = _run_dryfit_generate(args.scenario, manifest_dir, args.dry_run)

    gt_path = manifest_dir / "ground_truth.json"
    mf_path = manifest_dir / "manifest.json"
    if not gt_path.is_file():
        sys.stderr.write(
            f"ERROR: ground_truth.json not found in {manifest_dir}\n"
            "Pass --generate, or point --from-manifest at a prior dryfit run.\n"
        )
        return 2

    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))
    manifest = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.is_file() else {}

    items = _build_items(ground_truth, manifest)
    sys.stderr.write(f">> built {len(items)} dataset items from {gt_path}\n")

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(items, indent=2), encoding="utf-8")
        sys.stderr.write(f">> wrote {args.json_out}\n")

    if args.no_upload:
        sys.stdout.write(json.dumps({"items": len(items), "uploaded": False}, indent=2) + "\n")
        return 0

    scenario = args.scenario
    dataset_name = args.dataset_name or f"mason-dryfit-{scenario}"
    _upload_to_langfuse(dataset_name, items, scenario)
    sys.stdout.write(
        json.dumps({"items": len(items), "uploaded": True, "dataset": dataset_name}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
