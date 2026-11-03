from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


def test_ci_runs_on_every_push_and_pull_request() -> None:
    workflow = WORKFLOW.read_text()
    trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]

    assert trigger == "  push: {}\n  pull_request: {}\n"


def test_dependency_review_runs_only_on_pull_requests() -> None:
    workflow = WORKFLOW.read_text()
    job = workflow.split("\n  dependency-review:\n", 1)[1].split("\n  integration:\n", 1)[0]

    assert "    if: github.event_name == 'pull_request'\n" in job
