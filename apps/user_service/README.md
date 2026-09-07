# User Service

CRM and resident platform API — auth, contacts, membership, passes, fees, events, and related modules.

## Documentation

- **Top-level index:** [../../docs/README.md](../../docs/README.md)
- **Service docs:** [docs/README.md](./docs/README.md)

## Run locally

```bash
cd ats-home-craft-python-service
uvicorn apps.user_service.app.main:app --host 0.0.0.0 --port 5000 --reload
```

## Tests

```bash
ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests -q
```

## Docker

```bash
docker build -f docker/user_service.Dockerfile -t ats-user-service .
docker run --env-file .env -p 5000:5000 ats-user-service
```

## API prefix

Staff and resident routes under `/v1/...` (JWT + project/org permissions).
