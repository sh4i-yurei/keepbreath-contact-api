# Architecture

How the pieces of this repository fit together, and what each one must not do. When a change
doesn't fit this, change this file first, on purpose, in the same commit.

## The shape

The contact form on keepbreath.ing posts JSON to this API. The web process validates the
submission and checks it for bots, then puts the send on a Redis queue and answers 202 at once. A
separate Celery worker takes the send off the queue, builds the email and delivers it through the
mail server, retrying and shelving failures so no message is silently lost. The README describes
the delivery model in full. CI runs the code checks, the unit tests and one end-to-end message
through the real Compose stack.

## The pieces

| Piece | Responsibility | Interface | Fails how | Must not |
|---|---|---|---|---|
| `app.py` | The web endpoint: validate, run the honeypot and ALTCHA checks, enqueue the send | `POST /api/contact`, JSON in, 202 on success | Returns an error status naming the problem; never sends mail itself | Send mail in the request; construct the worker's settings or hold the mail credential |
| `validation.py` | Server-side validation of a submission | A pure function returning a cleaned value and an error | Returns the error | Touch Flask, app state or anything outside its arguments |
| `replay.py` | Accept each ALTCHA challenge signature once | SQLite registry with a unique constraint | Rejects a repeat as a replay | Rely on in-process locking |
| `config.py` | Typed settings read from the environment | Separate settings classes for the web process and the worker | A missing variable stops startup with a clear error | Give one process the other's secrets |
| `celery_app.py`, `worker.py` | The Celery application, and the worker's entry point that checks its environment first | `celery -A worker.celery` | Stops at startup on a missing credential or broker address | Be imported by the web app |
| `tasks.py` | The send task, its retries, the dead-letter shelf, the scheduled re-drive and the retention backstop | Celery task enqueued by `app.py` | Shelves a failed send with its reason; drops only a malformed message or one past the age limit, and logs it | Give up on a deliverable message |
| `logging_config.py` | One structured JSON log format for both processes | Imported by the web app and the worker | — | Log message content or secrets |
| `Dockerfile`, `docker-compose.yml`, `deploy/` | Build the image and run the web app, worker and Redis together | Compose services and the Quadlet units under `deploy/` | — | Hold a secret; the `.env` file is never committed |
| `tests/` | Unit tests for the web app, validation, replay, tasks and worker | `pytest -q` | — | Reach a real mail server or a real Redis; fakeredis stands in for it |
| `.github/workflows/ci.yml` | Run the code checks, the unit tests, and the Compose integration test | GitHub Actions on every push | A failed job blocks the merge | Use real secrets; the integration test writes a throwaway `.env` with dummy values |
| `.github/workflows/dependency-review.yml` | Run advisory dependency review | GitHub Actions on pull requests only | A failed review is advisory | Use real secrets |

## Rules every piece follows

- **The send never runs in the web request.** The web process enqueues and answers; only the
  worker talks to the mail server.
- **Each process holds only its own secrets.** The web process never has the mail credential, and
  the worker never has the ALTCHA key.
- **Nothing here holds a secret.** Real values live in the server's `.env` file, never in the
  repository.
- **A production submission sends real mail.** Tests and CI use mocks, fakeredis or dummy values,
  and anything that would reach the real mail path needs deliberate authorisation.
