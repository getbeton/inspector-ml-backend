"""Memory-bank loader: read strategy docs once at process start.

The docs live at the repo root (`memory-bank/*.md`), originally added in
PR #1 (`memory-bank-draft`). This module locates them relative to its own
file, loads + caches the content, and computes a SHA-256 fingerprint per
doc that downstream code (notably Langfuse session metadata) can use to
confirm the agent received the context it was supposed to receive.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryBankDoc:
    name: str
    body: str
    sha256: str

    def fingerprint(self) -> str:
        return f"{self.name}:{self.sha256[:8]}"


def _repo_root() -> Path:
    # This file lives at:
    #   <repo>/projects/upsell_ranker/versions/v0.0.2/shared/memory_bank.py
    # parents[0]=shared, [1]=v0.0.2, [2]=versions, [3]=upsell_ranker,
    # [4]=projects, [5]=<repo root>.
    return Path(__file__).resolve().parents[5]


@lru_cache(maxsize=1)
def _all_docs() -> Mapping[str, MemoryBankDoc]:
    base = _repo_root() / "memory-bank"
    if not base.is_dir():
        _LOG.warning("memory-bank directory not found at %s", base)
        return {}
    out: dict[str, MemoryBankDoc] = {}
    for path in sorted(base.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        body = path.read_text(encoding="utf-8")
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        out[path.stem] = MemoryBankDoc(name=path.stem, body=body, sha256=sha)
    return out


AGENT_DOC_PLAN: Mapping[str, Sequence[str]] = {
    "upsell": ("founding-sales", "monetization-pricing"),
    "dwh": ("running-lean", "monetization-pricing"),
    "bootstrap": ("running-lean",),  # frames what success_event means
    "explorer": ("running-lean", "hypotheses-generation", "rice-prioritization"),
    "reviewer": ("rice-prioritization", "cohort-retention-analysis"),
    "finalize": ("rice-prioritization",),  # context for what's worth surfacing
}


def docs_for(role: str) -> tuple[MemoryBankDoc, ...]:
    plan = AGENT_DOC_PLAN.get(role)
    if plan is None:
        raise KeyError(f"No memory-bank plan for role={role!r}")
    all_docs = _all_docs()
    missing = [name for name in plan if name not in all_docs]
    if missing:
        raise FileNotFoundError(
            f"memory-bank docs missing for role={role!r}: {missing}. "
            f"Looked in {_repo_root() / 'memory-bank'}"
        )
    return tuple(all_docs[name] for name in plan)


def render_for(role: str) -> str:
    """Render the memory-bank context block to splice into an agent's
    instruction string. Each doc gets a delimited section so the model
    can cite by name.

    The header tells the agent how to use the docs (read, cite by short
    name, refuse to contradict). The agent's own instruction should still
    reinforce this with a "Workflow step 0: read the memory bank above"
    line — see signal_agent / dwh_analyst / upsell_agent for examples.
    """
    docs = docs_for(role)
    doc_names = [d.name for d in docs]
    sections = ["=== MEMORY BANK START ===\n"]
    sections.append(
        f"The {len(docs)} section(s) below ({', '.join(doc_names)}) contain "
        "the team's prior knowledge on signal discovery, prioritization, "
        "cohort math, and pricing.\n\n"
        "Mandatory read-before-act protocol:\n"
        "1. Before issuing any tool call, read each section in this block.\n"
        "2. When you author SQL, hypotheses, or rankings, name the doc(s) "
        "you drew from in your output's `mechanism_rationale` (or equivalent) "
        "field. Cite by the short name (e.g. 'rice-prioritization') so a "
        "reviewer can audit which advice you applied.\n"
        "3. Treat these docs as ground truth. Do not contradict them; if you "
        "must, flag the contradiction explicitly in your output and explain.\n"
        "4. The fingerprints (sha256 prefixes) below are checked at startup. "
        "If a doc is missing, the process aborts before any LLM call.\n"
    )
    for d in docs:
        sections.append(f"\n--- {d.name} (sha256:{d.sha256[:8]}) ---\n{d.body}")
    sections.append("\n=== MEMORY BANK END ===\n")
    return "".join(sections)


def fingerprints_for(role: str) -> list[str]:
    """Short fingerprints suitable for Langfuse session tags."""
    return [d.fingerprint() for d in docs_for(role)]


def assert_loaded(role: str) -> None:
    """Raises FileNotFoundError if any expected doc didn't load.
    Wire this immediately before Explorer's first batch call.
    """
    docs_for(role)
