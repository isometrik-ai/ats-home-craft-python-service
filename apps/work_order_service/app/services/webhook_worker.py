"""Webhook delivery worker with httpx retries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpcore

from apps.work_order_service.app.utils.webhook_url import (
    resolve_webhook_hostname,
    validate_outbound_webhook_url,
)
from libs.shared_db.drivers.asyncpg_client import get_pool
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.logger import app_logger

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (5, 30)
RECOVERY_BATCH_SIZE = 100


class WebhookWorker:
    """Background worker that delivers outbound webhooks with retries."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None

    def _ensure_queue(self) -> asyncio.Queue:
        """Return the delivery queue, creating it on first use."""
        if self._queue is None:
            self._queue = asyncio.Queue()
        return self._queue

    def enqueue_delivery(
        self,
        *,
        organization_id: str,
        project_id: str,
        trigger_id: str,
        webhook_url: str,
        secret: str | None,
        event: str,
        payload: dict,
        delivery_id: str | None = None,
        prior_attempts: int = 0,
    ) -> None:
        """Queue one webhook delivery."""
        self._ensure_queue().put_nowait(
            {
                "delivery_id": delivery_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "trigger_id": trigger_id,
                "webhook_url": webhook_url,
                "secret": secret,
                "event": event,
                "payload": payload,
                "prior_attempts": prior_attempts,
            }
        )

    async def _delivery_loop(self) -> None:
        """Process queued webhook deliveries until cancelled."""
        queue = self._ensure_queue()
        while True:
            item = await queue.get()
            try:
                await _deliver(item)
            except Exception:
                app_logger.exception("Delivery failed — continuing")
            finally:
                queue.task_done()

    async def start(self) -> None:
        """Start the background delivery loop."""
        self._ensure_queue()
        await _recover_pending_deliveries(self)
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._delivery_loop())
            app_logger.info("Webhook delivery worker started")

    async def stop(self) -> None:
        """Stop the background delivery loop."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
            app_logger.info("Webhook delivery worker stopped")


_worker = WebhookWorker()


def _sign(secret: str, body: bytes) -> str:
    """Build the HMAC signature header value for a webhook payload."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _post_webhook(
    url: str, secret: str | None, event: str, body: bytes
) -> tuple[int | None, str | None]:
    """POST a signed webhook payload and return status or transport error."""
    validate_outbound_webhook_url(url)
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return None, "ValidationException: invalid webhook URL"

    port = parsed.port or 443
    try:
        pinned_ip = await resolve_webhook_hostname(hostname, port=port)
    except ValidationException:
        return None, "ValidationException: webhook URL blocked"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    header_items = [
        (b"content-type", b"application/json"),
        (b"x-ats-event", event.encode()),
        (b"host", hostname.encode()),
    ]
    if secret:
        header_items.append((b"x-ats-signature", _sign(secret, body).encode()))

    timeout = {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}
    request = httpcore.Request(
        method=b"POST",
        url=(b"https", pinned_ip.encode(), port, path.encode()),
        headers=header_items,
        content=body,
        extensions={
            "timeout": timeout,
            "sni_hostname": hostname.encode("ascii"),
        },
    )

    try:
        async with httpcore.AsyncConnectionPool() as pool:
            response = await pool.handle_async_request(request)
        status = response.status
        await response.aclose()
        return status, None
    except Exception as exc:  # — retried below
        return None, f"{type(exc).__name__}: {exc}"


async def _persist_delivery_result(
    item: dict,
    *,
    status: int | None,
    error: str | None,
    attempts: int,
    duration_ms: int,
    delivered: bool,
) -> None:
    """Persist webhook delivery outcome (update pending row or insert)."""
    from apps.work_order_service.app.db.repositories.integration_repository import (
        IntegrationRepository,
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("SET search_path TO work_order, public")
        repo = IntegrationRepository(conn)
        delivery_id = item.get("delivery_id")
        if delivery_id:
            await repo.update_webhook_delivery(
                delivery_id,
                response_status=status,
                error=error,
                attempt=attempts,
                duration_ms=duration_ms,
                delivered=delivered,
            )
            return

        payload = item["payload"]
        await repo.insert_webhook_delivery(
            {
                "organization_id": item["organization_id"],
                "project_id": item["project_id"],
                "trigger_id": item.get("trigger_id"),
                "entity": payload.get("entity"),
                "entity_id": payload.get("entity_id"),
                "event": item["event"],
                "request_payload": payload,
                "response_status": status,
                "error": error,
                "attempt": attempts,
                "duration_ms": duration_ms,
                "delivered": delivered,
            }
        )


async def _deliver(item: dict) -> None:
    """Deliver one queued webhook with retries and audit logging."""
    prior_attempts = int(item.get("prior_attempts") or 0)
    if prior_attempts >= MAX_ATTEMPTS:
        app_logger.warning(
            "Skipping webhook delivery %s — attempt limit reached",
            item.get("delivery_id"),
        )
        return

    started = datetime.now(timezone.utc)
    body = json.dumps(item["payload"], default=str).encode()
    status: int | None = None
    error: str | None = None
    attempts = prior_attempts

    for attempt in range(prior_attempts + 1, MAX_ATTEMPTS + 1):
        attempts = attempt
        status, error = await _post_webhook(
            item["webhook_url"], item.get("secret"), item["event"], body
        )
        if status is not None and 200 <= status < 300:
            error = None
            break
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    delivered = status is not None and 200 <= status < 300
    if not delivered and error is None:
        error = f"endpoint returned {status}"

    try:
        await _persist_delivery_result(
            item,
            status=status,
            error=error,
            attempts=attempts,
            duration_ms=duration_ms,
            delivered=delivered,
        )
    except Exception:
        app_logger.exception(
            "Failed to record webhook delivery for trigger %s", item.get("trigger_id")
        )


async def _recover_pending_deliveries(worker: WebhookWorker) -> None:
    """Re-queue undelivered webhook rows after process restart."""
    from apps.work_order_service.app.db.repositories.integration_repository import (
        IntegrationRepository,
    )

    after_id: str | None = None
    recovered = 0
    while True:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SET search_path TO work_order, public")
            pending = await IntegrationRepository(conn).list_pending_webhook_deliveries(
                limit=RECOVERY_BATCH_SIZE,
                after_id=after_id,
                max_attempts=MAX_ATTEMPTS,
            )

        if not pending:
            break

        for row in pending:
            after_id = row["id"]
            webhook_url = row.get("webhook_url")
            if not webhook_url:
                app_logger.warning(
                    "Skipping webhook delivery %s — trigger URL unavailable", row.get("id")
                )
                continue
            worker.enqueue_delivery(
                organization_id=row["organization_id"],
                project_id=row["project_id"],
                trigger_id=str(row["trigger_id"]) if row.get("trigger_id") else "",
                webhook_url=webhook_url,
                secret=row.get("secret"),
                event=row["event"],
                payload=row.get("request_payload") or {},
                delivery_id=row["id"],
                prior_attempts=int(row.get("attempt") or 0),
            )
            recovered += 1

        if len(pending) < RECOVERY_BATCH_SIZE:
            break

    if recovered:
        app_logger.info("Recovered %d pending webhook deliveries", recovered)


def enqueue_delivery(
    *,
    organization_id: str,
    project_id: str,
    trigger_id: str,
    webhook_url: str,
    secret: str | None,
    event: str,
    payload: dict,
    delivery_id: str | None = None,
    prior_attempts: int = 0,
) -> None:
    """Queue one webhook delivery."""
    _worker.enqueue_delivery(
        organization_id=organization_id,
        project_id=project_id,
        trigger_id=trigger_id,
        webhook_url=webhook_url,
        secret=secret,
        event=event,
        payload=payload,
        delivery_id=delivery_id,
        prior_attempts=prior_attempts,
    )


async def start_webhook_worker() -> None:
    """Start the module-level webhook worker."""
    await _worker.start()


async def stop_webhook_worker() -> None:
    """Stop the module-level webhook worker."""
    await _worker.stop()
