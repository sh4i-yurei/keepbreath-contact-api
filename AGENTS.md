# Contact API work

Project notes: [[Contact form backend]], [[Deploy automation for the contact API]]. Work handed
off from another project names its own note and buildlog in the handoff.

Read `ARCHITECTURE.md` before changing anything, and ask before opening a buildlog. The full check
is `ruff check . && ruff format --check . && mypy . && pytest -q && git diff --check`, after
`pip install -r requirements-dev.txt`. CI runs the same code checks and unit tests on every push
to every branch. It also builds the Compose stack and sends one test request with dummy values to
check processing end to end. Dependency review runs on pull requests. CodeQL and dependency review
are advisory. Scope
integration tests carefully, because a production contact submission sends real mail.
