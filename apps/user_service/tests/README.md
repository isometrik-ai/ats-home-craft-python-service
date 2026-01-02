# User Service Tests

This folder holds unit and integration tests for the user_service. It is structured to mirror the API domains and keep helpers reusable.

## Layout

- `integration/auth/` — endpoint-level tests for auth flows (login, refresh, signup, password flows, verify email, 2FA, delete user)
- `unit/` — pure/function-level tests (schemas, utils)
- `factories.py` — payload builders for tests
- `utils/assertions.py` — common assertion helpers
- `conftest.py` — shared fixtures (app client, dependency overrides, auth/db/supabase stubs)

## Run tests

```bash
# Auth integration (with coverage on auth router)
pytest apps/user_service/tests/integration/auth -q \
  --cov=apps.user_service.app.api.auth \
  --cov-report=term-missing --cov-report=html --cov-fail-under=85

# All unit tests
pytest apps/user_service/tests/unit -q

# Everything (adjust coverage targets as needed)
pytest apps/user_service/tests -q
```

Coverage HTML lives in `htmlcov/index.html`. Threshold is set to 85% via `.coveragerc`.

## Prompts for an agent (to add tests)

- *“Add integration tests for `<router>` endpoints in user_service using the existing fixtures in `conftest.py` and helpers in `tests/utils/assertions.py`.”*
- *“Add unit tests for `<function>` in `apps/user_service/app/utils/common_utils.py` mirroring the existing pattern in `tests/unit/test_utils_common.py`.”*
- *“Add schema validation tests for `<schema>` in `apps/user_service/app/schemas/auth.py` similar to `tests/unit/test_schemas_auth.py`.”*
- *“Raise coverage for `apps/user_service/app/api/<file>.py` to 85%+ using pytest, reusing the `client` fixture and monkeypatching services like in `tests/integration/auth/test_auth_api.py`.”*
- *“Update factories or add a new factory in `tests/factories.py` to support `<payload>` for new endpoint tests.”*
- *“Add integration tests for users/sessions/invites/permissions routers mirroring the auth test style (stub services with monkeypatch, assert response status/message/code).”*
- *“Add unit tests for session or invite schemas/functions under `tests/unit/` following the same structure as existing unit tests.”*
