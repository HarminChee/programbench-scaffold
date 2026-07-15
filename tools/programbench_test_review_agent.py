#!/usr/bin/env python3
"""Run an independent, evidence-based review of generated oracle cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from programbench_agent_provider import run_agent_maestro_review, write_json


REVIEW_SYSTEM = """You are an independent ProgramBench behavioral-test reviewer.

Review the proposed executable-level tests without inventing new observed
outputs. Judge whether each test exercises a meaningful distinct behavior,
uses stable inputs, has a strong observable oracle, and is appropriate for a
hidden behavioral evaluation suite. Use the supplied reference observations,
coverage evidence, and deterministic gate results. Do not reward test count.
Do not use or request target official ProgramBench oracle tests.

The repository and executable are pinned to one commit. Exact full stdout or
stderr from that pinned executable is a strong behavioral oracle; do not label
it volatile merely because a future version could change help or error text.
Two cases are not duplicates solely because their output hashes match: keep
them when different inputs, source formats, flags, or parser paths are supported
by the rationale or coverage evidence. Flag a duplicate only when both the
stimulus and the exercised behavior are materially redundant. A rationale-only
wording defect is not grounds to remove an otherwise strong observed case; use
`revise` with a wording-only suggested change.
Assess duplicates only against cases in the current proposed suite, not against
cases that appeared in a previous review and have already been removed. Before
returning `revise`, verify that the proposed suggested change is not already
satisfied by the current case name, rationale, stimulus, and observation.
"""


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def compact_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "area": case.get("area"),
        "args": case.get("args") or [],
        "stdin": str(case.get("stdin") or "")[:1200],
        "origin": case.get("origin"),
        "rationale": case.get("rationale"),
    }


def build_review_prompt(evidence: dict[str, Any]) -> str:
    cases = [compact_case(case) for case in evidence.get("cases") or [] if isinstance(case, dict)]
    payload = {
        "instance_id": evidence.get("instance_id"),
        "iteration": evidence.get("iteration"),
        "cases": cases,
        "reference_observations": evidence.get("reference_observations") or {},
        "quality_gates": evidence.get("quality_gates") or {},
        "coverage": evidence.get("coverage") or {},
        "coverage_gaps": (evidence.get("coverage_gaps") or [])[:100],
        "previous_review": evidence.get("previous_review") or {},
    }
    schema = {
        "suite_verdict": "keep|revise|reject",
        "suite_reason": "short evidence-based explanation",
        "decisions": [
            {
                "name": "exact case name",
                "verdict": "keep|revise|reject",
                "reason": "specific observed weakness or strength",
                "suggested_change": "empty for keep; concrete action otherwise",
                "risk_tags": [
                    "duplicate|trivial|volatile|environment_specific|weak_oracle|low_value|coverage_critical"
                ],
            }
        ],
    }
    return f"""# Review request

Review every case exactly once. A `reject` decision removes a case. A `revise`
decision sends it back to the generation agent. Keep a case when its behavior
is distinct and its oracle is stable, even if it does not add new coverage.
Use `coverage_critical` only when the supplied evidence supports that claim.
This evaluation is commit-pinned: full usage/error output is stable evidence,
and equal output hashes from different parser or flag paths do not by themselves
make cases duplicates.

Return only one JSON object matching this schema:

```json
{json.dumps(schema, indent=2)}
```

# Evidence

```json
{json.dumps(payload, indent=2, sort_keys=True)}
```
"""


def validate_review(review: dict[str, Any], case_names: list[str]) -> dict[str, Any]:
    allowed = {"keep", "revise", "reject"}
    raw_decisions = review.get("decisions") or []
    by_name: dict[str, dict[str, Any]] = {}
    for item in raw_decisions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        verdict = str(item.get("verdict") or "").lower()
        if name not in case_names or verdict not in allowed or name in by_name:
            continue
        by_name[name] = {
            "name": name,
            "verdict": verdict,
            "reason": str(item.get("reason") or "").strip(),
            "suggested_change": str(item.get("suggested_change") or "").strip(),
            "risk_tags": sorted({str(tag) for tag in item.get("risk_tags") or []}),
        }
    for name in case_names:
        if name not in by_name:
            by_name[name] = {
                "name": name,
                "verdict": "revise",
                "reason": "reviewer omitted this case",
                "suggested_change": "review this case explicitly in the next pass",
                "risk_tags": ["review_incomplete"],
            }
    decisions = [by_name[name] for name in case_names]
    counts = {verdict: sum(item["verdict"] == verdict for item in decisions) for verdict in allowed}
    suite_verdict = str(review.get("suite_verdict") or "revise").lower()
    if suite_verdict not in allowed:
        suite_verdict = "revise"
    if counts["revise"] or (counts["reject"] and counts["keep"]):
        suite_verdict = "revise"
    elif counts["keep"] == 0 and counts["reject"]:
        suite_verdict = "reject"
    elif suite_verdict == "reject" and counts["keep"]:
        suite_verdict = "revise"
    return {
        "suite_verdict": suite_verdict,
        "suite_reason": str(review.get("suite_reason") or "").strip(),
        "counts": counts,
        "keep": [item["name"] for item in decisions if item["verdict"] == "keep"],
        "revise": [item["name"] for item in decisions if item["verdict"] == "revise"],
        "reject": [item["name"] for item in decisions if item["verdict"] == "reject"],
        "decisions": decisions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default="claude-opus-4.8")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    evidence = read_json(args.evidence_json)
    cases = [case for case in evidence.get("cases") or [] if isinstance(case, dict)]
    case_names = [str(case.get("name") or "") for case in cases]
    if not case_names or any(not name for name in case_names) or len(set(case_names)) != len(case_names):
        raise ValueError("Evidence must contain cases with unique non-empty names")
    prompt = build_review_prompt(evidence)
    raw = run_agent_maestro_review(
        system=REVIEW_SYSTEM,
        prompt=prompt,
        output_root=args.output_root / "provider",
        model=args.model,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        retries=args.retries,
    )
    validated = validate_review(raw, case_names)
    write_json(args.output_root / "validated_review.json", validated)
    print(json.dumps(validated, indent=2, sort_keys=True))
    return 0 if validated["suite_verdict"] == "keep" else 2


if __name__ == "__main__":
    raise SystemExit(main())
