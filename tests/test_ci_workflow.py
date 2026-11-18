import re
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
DEPENDENCY_REVIEW = WORKFLOW.with_name("dependency-review.yml")


def test_ci_runs_on_every_push() -> None:
    workflow = WORKFLOW.read_text()
    trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]

    assert trigger == "  push: {}\n"
    assert "pull_request" not in workflow
    assert "dependency-review" not in workflow


def test_dependency_review_runs_only_on_pull_requests() -> None:
    workflow = DEPENDENCY_REVIEW.read_text()
    assert "\non:\n" in workflow
    assert "\npermissions:\n" in workflow
    assert "\n  dependency-review:\n" in workflow
    trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    job = workflow.split("\n  dependency-review:\n", 1)[1]

    assert trigger == "  pull_request: {}\n"
    assert "permissions:\n  contents: read\n" in workflow
    assert "    continue-on-error: true\n" in job
    assert "    runs-on: ubuntu-latest\n" in job
    assert "      - uses: actions/checkout@v7\n" in job
    assert "      - uses: actions/dependency-review-action@v5\n" in job


def test_ci_jobs_keep_the_required_names_and_ordering() -> None:
    workflow = WORKFLOW.read_text()
    jobs = re.findall(r"^  ([a-z-]+):$", workflow, re.MULTILINE)

    assert jobs == ["code-quality", "tests", "integration"]
    integration = workflow.split("\n  integration:\n", 1)[1]
    assert "    needs: [code-quality, tests]\n" in integration


def test_ci_installs_lock_before_test_tools_and_checks_the_image() -> None:
    workflow = WORKFLOW.read_text()
    for name, following in (("code-quality", "tests"), ("tests", "integration")):
        job = workflow.split(f"\n  {name}:\n", 1)[1].split(f"\n  {following}:\n", 1)[0]
        assert "      - run: pip install --require-hashes -r requirements.lock\n" in job
        assert "      - run: pip install -r requirements-dev.txt\n" in job
        assert job.index("pip install --require-hashes -r requirements.lock") < job.index(
            "pip install -r requirements-dev.txt"
        )
    integration = workflow.split("\n  integration:\n", 1)[1]
    exact = "docker compose exec -T contact-api python - --exact /app/requirements.lock < scripts/check_lock.py"
    assert exact in integration
    assert integration.index("Smoke — enqueue one message") < integration.index(exact)
    assert integration.index(exact) < integration.index("name: Tear down")
