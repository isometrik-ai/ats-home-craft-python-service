# Contributing

Thanks for considering a contribution! This monorepo hosts the FastAPI `user_service` and shared libraries under `libs/`. Please follow the guide below to keep changes smooth and consistent.

## Getting started

1. Fork/clone the repo and create a branch (`feature/…` or `fix/…`).
1. Python 3.13; create a virtual environment:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate  # Windows
   # or: source .venv/bin/activate
   ```
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
1. Create a `.env` at repo root with the required settings (see README “Environment”).

## Development workflow

- Keep business logic inside services and shared helpers; avoid duplication across apps.
- Prefer adding shared utilities to `libs/` when functionality can be reused.
- Update docs/tests when changing behavior or adding endpoints.
- Run formatting/linting before pushing:
  ```bash
  ruff check .
  ```
- Run tests (unit + integration):
  ```bash
  pytest apps/user_service/tests -q \
    --cov=apps.user_service \
    --cov-report=term-missing --cov-report=html --cov-fail-under=85
  ```

## Pull requests

- Keep PRs focused and small; include a clear description and checklist of changes.
- Note any config/env/DB migration impacts.
- Add or update tests to cover new/changed behavior; aim for ≥85% coverage on touched areas.
- Ensure local tests pass before opening the PR.

## Issue reporting / feature requests

- Use clear titles, expected vs. actual behavior, repro steps, and environment details.
- For new features, describe the use case, proposed API/behavior, and any breaking changes.

## Code style / patterns

- FastAPI routers under `apps/user_service/app/api`, services under `app/services`, schemas under `app/schemas`.
- Shared logging, exceptions, translations, and rate limiting live in `libs/shared_utils`; database access helpers in `libs/shared_db`; configuration in `libs/shared_config`.
- Use dependency injection patterns already present (e.g., `db_conn`, `db_uow`, `supabase_*`, `get_user_from_auth`).
- Favor composable helpers and avoid hard-coded environment values.

## Security

- Do not commit secrets. Use environment variables/.env locally and secrets in CI.
- Report security issues privately to rahul@houseofapps.ai.

## Releases

- Keep versioning and changelog entries where applicable; note API-affecting changes.

Thanks for your contributions!
