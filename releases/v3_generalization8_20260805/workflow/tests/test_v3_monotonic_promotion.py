import importlib.util
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "v3/crosslang20/tools/promote_best_refinement_v3.py"
)
SPEC = importlib.util.spec_from_file_location("promote_best_refinement_v3", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def summary(primary: float, secondary: float, *, quality: bool = True) -> dict:
    return {
        "case_count": 10,
        "all_tests_passed": quality,
        "binary_consistent": quality,
        "stop_gate": {
            "primary_percent": primary,
            "secondary_percent": secondary,
            "checks": {
                "coverage_signal_valid": True,
                "all_dummies_rejected": quality,
                "assertion_lint": quality,
                "source_leak": quality,
            },
        },
    }


def write(repo: Path, payload: dict) -> None:
    repo.mkdir(parents=True)
    (repo / "pipeline_summary.json").write_text(json.dumps(payload))


def test_rejects_secondary_coverage_regression(tmp_path: Path) -> None:
    prior, candidate = tmp_path / "prior", tmp_path / "candidate"
    write(prior, summary(60.0, 50.0))
    write(candidate, summary(61.0, 45.0))
    selected, reason = MODULE.choose(prior, candidate, 0.05)
    assert selected == prior
    assert reason == "candidate_coverage_regression"


def test_promotes_non_regressing_candidate(tmp_path: Path) -> None:
    prior, candidate = tmp_path / "prior", tmp_path / "candidate"
    write(prior, summary(60.0, 50.0))
    write(candidate, summary(61.0, 50.0))
    selected, reason = MODULE.choose(prior, candidate, 0.05)
    assert selected == candidate
    assert reason == "candidate_non_regressing"


def test_rejects_quality_failure_even_with_more_coverage(tmp_path: Path) -> None:
    prior, candidate = tmp_path / "prior", tmp_path / "candidate"
    write(prior, summary(60.0, 50.0))
    write(candidate, summary(90.0, 90.0, quality=False))
    selected, reason = MODULE.choose(prior, candidate, 0.05)
    assert selected == prior
    assert reason == "candidate_quality_rejected"
