# Work Order Service

Facility management API — assets, AMC contracts, work orders, vendor invoices, and payments.

## Documentation

- **Top-level index:** [../../docs/README.md](../../docs/README.md)
- **Service docs:** [docs/README.md](./docs/README.md)

## Run locally

```bash
cd ats-home-craft-python-service
uvicorn apps.work_order_service.app.main:app --host 0.0.0.0 --port 5001 --reload
```

## Tests

```bash
ENVIRONMENT=test PYTHONPATH=. pytest apps/work_order_service/tests -q
```

## Docker

```bash
docker build -f docker/work_order_service.Dockerfile -t ats-work-order-service .
docker run --env-file .env -p 5001:5001 ats-work-order-service
```

## API prefix

Staff routes: `/v1/projects/{project_id}/...` (JWT + `WORK_ORDER_MANAGEMENT_*` permissions)

Vendor portal: `/v1/vendor/...` (`X-Vendor-Token` header)

## Schema

[work-order-management-schema.md](../../../ats-home-craft-supabase/docs/work-order-management-schema.md)
