"""Client enrichment service.

Calls the external enrichment API for person (/enrich) and company (/enrich/company).
Used after client creation to trigger async enrichment; request_id and status
are stored on the client. Webhook handling for company and person enrichment.
On enrichment completion, calls /enrich/sales-intelligence and stores the result
in the client's sales_intelligence field. Enrichment updates only fields allowed
in the Update API; never overwrites non-empty existing data with empty enrichment values.
"""

import asyncio
import ipaddress
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx

from apps.user_service.app.api.presigned_url import get_r2_client
from apps.user_service.app.config.app_settings import app_settings, shared_settings
from apps.user_service.app.db.repositories import (
    CompaniesRepository,
    ContactsRepository,
)
from apps.user_service.app.db.repositories.companies_repository import (
    COMPANY_JSONB_COLUMNS,
)
from apps.user_service.app.db.repositories.contacts_repository import (
    CONTACT_JSONB_COLUMNS,
)
from apps.user_service.app.schemas.enums import ClientEnrichmentStatus, ClientType
from apps.user_service.app.utils.common_utils import parse_json_field, safe_json_loads
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.logger import get_logger

logger = get_logger("client_enrichment_service")

_MAX_PROFILE_PHOTO_BYTES = 50 * 1024 * 1024  # 10MB safety cap


def _normalize_webhook_update_payload(payload: dict[str, Any], jsonb_keys: frozenset[str]) -> None:
    """Normalize webhook update payload so types match direct PATCH
    (parse_json_field per JSONB key)."""
    for k in list(payload.keys()):
        if k not in jsonb_keys:
            continue
        value = parse_json_field(payload.get(k))
        payload[k] = value if isinstance(value, (list, dict)) else []


def _is_empty_value(value: Any) -> bool:
    """Return True if value is considered empty (do not use to overwrite existing non-empty)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _merge_update_without_overwriting_empty(
    update: dict[str, Any], existing: dict[str, Any] | None
) -> dict[str, Any]:
    """Remove from update any key where enrichment value is empty and existing is non-empty."""
    if not existing:
        return dict(update)
    existing_parsed = existing or {}
    result = dict(update)
    for key in list(result.keys()):
        new_val = result[key]
        if not _is_empty_value(new_val):
            continue
        existing_val = existing_parsed.get(key)
        if existing_val is None:
            continue
        if isinstance(existing_val, str):
            if existing_val.strip():
                result.pop(key, None)
        elif isinstance(existing_val, (list, dict)) and len(existing_val) > 0:
            result.pop(key, None)
    return result


def _normalize_str_key(value: Any) -> str:
    """Normalize a string for key comparisons (trim + lower).

    Returns empty string for non-string inputs.
    """
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _trim_nonempty_str(value: Any) -> str | None:
    """Trim string and return None if empty (or non-string)."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _merge_dict_list_by_key(
    enriched_items: list[dict[str, Any]],
    existing_items: list[dict[str, Any]],
    *,
    get_key: Callable[[dict[str, Any]], str],
    merge_on_match: Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], bool]],
    build_enriched_only: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generic merge helper for list-of-dicts by normalized key.

    Keeps existing item order and only overrides when `merge_on_match` says so.
    Enrichment-only entries are appended; any keys overridden at least once are
    not appended.
    """

    # key -> first enriched item for that key
    enriched_map: dict[str, dict[str, Any]] = {}
    for item in enriched_items:
        if not isinstance(item, dict):
            continue
        key = get_key(item)
        if not key or key in enriched_map:
            continue
        enriched_map[key] = item

    result: list[dict[str, Any]] = []
    overridden_keys: set[str] = set()

    for existing_item in existing_items:
        key = get_key(existing_item)
        enriched_item = enriched_map.get(key) if key else None
        if not key or not enriched_item or key in overridden_keys:
            result.append(existing_item)
            continue

        merged_item, did_override = merge_on_match(existing_item, enriched_item)
        result.append(merged_item)
        if did_override:
            overridden_keys.add(key)

    # Append enrichment-only keys (or keys whose override did not happen).
    for key, enriched_item in enriched_map.items():
        if key in overridden_keys:
            continue
        result.append(build_enriched_only(enriched_item))

    return result


def _merge_social_pages_by_platform(
    enriched_social_pages: list[dict[str, Any]],
    existing_social_pages: Any,
) -> list[dict[str, Any]]:
    """Merge `social_pages` by case-insensitive `platform`, without wiping entries.

    Rules:
    - If enrichment provides the same `platform` with a non-empty `url`, override ONLY
      `url` on the first matching existing item, preserving that item's `id` and other fields.
    - If enrichment has the platform but `url` is empty, keep the existing item unchanged.
    - Existing platforms not returned by enrichment are preserved.
    - Enrichment-only platforms are appended; assign a UUID if enrichment item has no `id`.
    """

    parsed_existing = parse_json_field(existing_social_pages)
    if isinstance(parsed_existing, list):
        existing_list: list[dict[str, Any]] = [
            item for item in parsed_existing if isinstance(item, dict)
        ]
    else:
        existing_list = []

    def get_platform(item: dict[str, Any]) -> str:
        return _normalize_str_key(item.get("platform"))

    def merge_on_match(
        existing_item: dict[str, Any],
        enriched_item: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Return (merged_item, did_override_url)."""
        raw_url = enriched_item.get("url")
        if _is_empty_value(raw_url):
            return existing_item, False
        override_url = _trim_nonempty_str(raw_url)
        if override_url is None:
            return existing_item, False

        new_item = dict(existing_item)
        new_item["url"] = override_url
        return new_item, True

    def build_enriched_only(enriched_item: dict[str, Any]) -> dict[str, Any]:
        new_item = dict(enriched_item)
        if not new_item.get("id"):
            new_item["id"] = str(uuid.uuid4())
        return new_item

    return _merge_dict_list_by_key(
        enriched_items=enriched_social_pages,
        existing_items=existing_list,
        get_key=get_platform,
        merge_on_match=merge_on_match,
        build_enriched_only=build_enriched_only,
    )


def _first_country_from_addresses(data: dict[str, Any]) -> str | None:
    """Extract country from first address if present."""
    addresses = data.get("addresses") or []
    if not addresses or not isinstance(addresses[0], dict):
        return None
    return (addresses[0].get("country") or "").strip() or None


def _first_website_url(data: dict[str, Any]) -> str | None:
    """Extract first website URL from websites list."""
    for website in data.get("websites") or []:
        if isinstance(website, dict) and website.get("url"):
            return website["url"].strip()
    return None


def _linkedin_url_from_social_pages(data: dict[str, Any]) -> str | None:
    """Extract LinkedIn URL from social_pages list."""
    for social_page in data.get("social_pages") or []:
        if (
            isinstance(social_page, dict)
            and (social_page.get("platform") or "").lower() == "linkedin"
            and social_page.get("url")
        ):
            return social_page["url"].strip()
    return None


class ClientEnrichmentService:
    """Calls the external enrichment API for person and company clients.

    Use from_settings() for an instance from app config.
    """

    def __init__(self, base_url: str, webhook_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._webhook_url = webhook_url.rstrip("/")
        self._timeout = timeout_seconds

    @classmethod
    def from_settings(cls) -> "ClientEnrichmentService":
        """Create an instance from app config."""
        settings = app_settings.enrichment_service
        return cls(
            base_url=settings.base_url,
            webhook_url=settings.webhook_url,
            timeout_seconds=settings.timeout_seconds,
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to enrichment API; returns JSON. Raises httpx.HTTPError on failure."""
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        logger.info(
            "Enrichment requested",
            extra={"request_id": data.get("request_id"), "path": path},
        )
        return data

    def _build_person_payload(
        self, data: dict[str, Any], webhook_url: str | None = None
    ) -> dict[str, Any]:
        """Build payload for POST /enrich (person).
        At least one of name, email, company, phone required."""
        name = build_full_name(
            data.get("first_name") or "",
            data.get("middle_name") or "",
            data.get("last_name") or "",
        ).strip()
        phone_isd = data.get("phone_isd_code") or ""
        phone_num = data.get("phone_number") or ""
        phone = f"{phone_isd}{phone_num}".strip() or None
        country = _first_country_from_addresses(data)

        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        if data.get("email"):
            payload["email"] = data["email"]
        company_name = (data.get("company_name") or data.get("company") or "").strip()
        if company_name:
            payload["company"] = company_name
        if country:
            payload["country"] = country
        if phone:
            payload["phone"] = phone
        if not payload:
            payload["name"] = name or ""
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    def _build_company_payload(
        self,
        client_id: str,
        organization_id: str,
        data: dict[str, Any],
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        """Build payload for POST /enrich/company.
        At least one of company_name, website_url, linkedin_url, company_email required."""
        website_url = _first_website_url(data)
        linkedin_url = _linkedin_url_from_social_pages(data)
        location = _first_country_from_addresses(data)
        company_name = (data.get("name") or "").strip() or ""

        payload: dict[str, Any] = {
            "account_id": str(organization_id),
            "external_id": str(client_id),
            "company_name": company_name,
            "project_id": str(client_id),
        }
        if website_url:
            payload["website_url"] = website_url
        if data.get("email"):
            payload["company_email"] = data["email"]
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url
        if data.get("industry"):
            payload["industry"] = data["industry"]
        if location:
            payload["location"] = location
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    async def enrich_person(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /enrich for person. Returns dict with request_id, status, message."""
        return await self._post("/enrich", payload)

    async def enrich_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /enrich/company for company. Returns dict with request_id, status, message."""
        return await self._post("/enrich/company", payload)

    async def _fetch_sales_intelligence(
        self, person_info: dict[str, Any], company_info: dict[str, Any]
    ) -> dict[str, Any] | None:
        """POST /enrich/sales-intelligence; returns sales_intelligence payload or None on error."""
        payload = {"person_info": person_info or {}, "company_info": company_info or {}}
        try:
            data = await self._post("/enrich/sales-intelligence", payload)
            if data.get("success") and isinstance(data.get("sales_intelligence"), dict):
                return data["sales_intelligence"]
            return None
        except Exception as e:
            logger.warning(
                "Sales intelligence API failed",
                extra={"error": str(e)},
            )
            return None

    async def _store_sales_intelligence_for_company(
        self,
        *,
        company_id: Any,
        organization_id: Any,
        person_info: dict[str, Any],
        company_info: dict[str, Any],
        conn: asyncpg.Connection,
    ) -> None:
        """Fetch sales intelligence using the shared API and persist it if available.

        This is the single generic helper used by both company and person enrichment flows.
        It does not change control flow: callers decide whether to await it inline or
        invoke it from a background task.
        """
        sales_data = await self._fetch_sales_intelligence(
            person_info=person_info or {},
            company_info=company_info or {},
        )
        if not sales_data:
            return

        repo = CompaniesRepository(conn)
        await repo.update_company(
            company_id=company_id,
            organization_id=organization_id,
            update_data={"sales_intelligence": sales_data},
        )

    @staticmethod
    async def _persist_enrichment_status(
        *,
        db_conn: asyncpg.Connection,
        entity_table: str,
        entity_id: str,
        organization_id: str,
        update_data: dict[str, Any],
    ) -> None:
        """Persist enrichment status fields to the given entity table.

        Supports v2 `contacts`/`companies`.
        """
        if entity_table == "contacts":
            repo = ContactsRepository(db_conn)
            await repo.update_contact(
                contact_id=entity_id,
                organization_id=organization_id,
                update_data=update_data,
            )
            return

        if entity_table == "companies":
            repo = CompaniesRepository(db_conn)
            await repo.update_company(
                company_id=entity_id,
                organization_id=organization_id,
                update_data=update_data,
            )
            return

        logger.error(
            "Unsupported entity_table for enrichment status update; skipping",
            extra={
                "entity_table": entity_table,
                "entity_id": entity_id,
                "organization_id": organization_id,
            },
        )

    @staticmethod
    def _build_contact_social_pages_update(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for social_pages from enriched_profile."""
        social = enriched_profile.get("socialProfiles")
        if social is None:
            return {}
        return {"social_pages": ClientEnrichmentService._map_social_profiles(social)}

    @staticmethod
    def _build_contact_skills_update(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for skills from enriched_profile."""
        skills = enriched_profile.get("skills")
        if not isinstance(skills, list):
            return {}
        return {"skills": ClientEnrichmentService._map_string_list(skills)}

    @staticmethod
    def _build_contact_work_history_update(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for work_history from enriched_profile."""
        work_history = enriched_profile.get("workHistory")
        if work_history is None:
            return {}
        return {
            "work_history": ClientEnrichmentService._map_work_history_from_enriched(work_history)
        }

    @staticmethod
    def _build_contact_education_update(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for educational_history from enriched_profile."""
        education = enriched_profile.get("education")
        if education is None:
            return {}
        return {
            "educational_history": ClientEnrichmentService._map_education_from_enriched(education)
        }

    @staticmethod
    def _build_contact_additional_data_update(
        enriched_profile: dict[str, Any],
        *,
        existing_additional_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build update dict for additional_data from enriched_profile."""
        merged_additional = existing_additional_data or {}
        merged_additional["enriched_profile"] = enriched_profile
        merged_additional["additional_info"] = (
            ClientEnrichmentService._build_person_additional_info(enriched_profile)
        )
        return {"additional_data": merged_additional}

    @staticmethod
    def build_contact_enrichment_update(
        enriched_profile: dict[str, Any],
        *,
        existing_contact: dict[str, Any] | None = None,
        existing_additional_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build contacts-table update from webhook enriched_profile (person).

        Mirrors legacy behavior:
        - Never overwrite non-empty existing top-level fields with empty enrichment values.
        - Merge social pages by platform without wiping entries.
        - Store raw enrichment under additional_data.enriched_profile + additional_info.
        """
        if not enriched_profile or not isinstance(enriched_profile, dict):
            return {}

        update: dict[str, Any] = {}

        update.update(ClientEnrichmentService._build_contact_social_pages_update(enriched_profile))
        update.update(ClientEnrichmentService._build_contact_skills_update(enriched_profile))
        update.update(ClientEnrichmentService._build_contact_work_history_update(enriched_profile))
        update.update(ClientEnrichmentService._build_contact_education_update(enriched_profile))
        update.update(
            ClientEnrichmentService._build_contact_additional_data_update(
                enriched_profile,
                existing_additional_data=existing_additional_data,
            )
        )
        update["enrichment_status"] = ClientEnrichmentStatus.COMPLETED.value
        update["enrichment_done"] = True
        update["last_enriched_at"] = datetime.now(timezone.utc)

        merged = _merge_update_without_overwriting_empty(update, existing_contact)
        if "social_pages" in merged and existing_contact is not None:
            merged["social_pages"] = _merge_social_pages_by_platform(
                enriched_social_pages=merged.get("social_pages") or [],
                existing_social_pages=existing_contact.get("social_pages"),
            )
        return merged

    async def run_client_enrichment(
        self,
        client_id: str,
        organization_id: str,
        client_type: str,
        payload_data: dict[str, Any],
        conn: asyncpg.Connection | None = None,
        *,
        entity_table: str = "clients",
    ) -> None:
        """Run enrichment after client creation: call API, then update client with
        request_id and status. Handles exceptions internally to prevent resource leaks.

        Runs only for client_type 'person' or 'company'. If conn is provided, uses
        the caller-managed connection (the caller is responsible for closing it);
        otherwise acquires a connection from the pool for the duration of this call.
        """
        webhook_url = self._webhook_url

        if client_type not in (ClientType.PERSON.value, ClientType.COMPANY.value):
            logger.error(
                "Unsupported client_type for enrichment; skipping",
                extra={
                    "client_id": client_id,
                    "organization_id": organization_id,
                    "client_type": client_type,
                },
            )
            return

        try:
            if client_type == ClientType.PERSON.value:
                body = self._build_person_payload(payload_data or {}, webhook_url=webhook_url)
                response = await self.enrich_person(body)
            else:
                body = self._build_company_payload(
                    client_id,
                    organization_id,
                    payload_data or {},
                    webhook_url=webhook_url,
                )
                response = await self.enrich_company(body)

            request_id = response.get("request_id") if isinstance(response, dict) else None
            if not request_id:
                logger.error(
                    "Enrichment API did not return request_id",
                    extra={"client_id": client_id, "response": response},
                )
                return

            await self.update_client_enrichment_status(
                client_id=client_id,
                organization_id=organization_id,
                request_id=request_id,
                status=ClientEnrichmentStatus.REQUESTED.value,
                conn=conn,
                entity_table=entity_table,
            )
            logger.info(
                "Client enrichment requested and record updated",
                extra={"client_id": client_id, "enrichment_request_id": request_id},
            )
        except Exception:
            logger.exception(
                "Error during run_client_enrichment",
                extra={
                    "client_id": client_id,
                    "organization_id": organization_id,
                    "client_type": client_type,
                },
            )

    async def update_client_enrichment_status(
        self,
        client_id: str,
        organization_id: str,
        request_id: str,
        status: str,
        conn: asyncpg.Connection | None = None,
        *,
        entity_table: str = "clients",
    ) -> None:
        """Update client enrichment request id and status.

        Centralizes logic for updating enrichment_request_id/enrichment_status using an
        optional caller-managed connection or acquiring one from the pool.
        """
        update_data = {
            "enrichment_request_id": request_id,
            "enrichment_status": status,
        }

        if conn is not None:
            await ClientEnrichmentService._persist_enrichment_status(
                db_conn=conn,
                entity_table=entity_table,
                entity_id=client_id,
                organization_id=organization_id,
                update_data=update_data,
            )
            return

        pool = await get_pool()
        async with AcquireConnection(pool) as acquired_conn:
            await ClientEnrichmentService._persist_enrichment_status(
                db_conn=acquired_conn,
                entity_table=entity_table,
                entity_id=client_id,
                organization_id=organization_id,
                update_data=update_data,
            )

    async def run_bulk_client_enrichment(
        self,
        items: list[dict[str, Any]],
        payload_data: dict[str, Any],
    ) -> None:
        """Run enrichment for multiple clients in parallel.

        Uses bounded asyncio.gather so person/company enrichment HTTP calls execute
        concurrently without unbounded task creation. Any exception from an individual
        task is logged and does not prevent others from running.
        """
        if not items:
            return

        # Basic input validation to avoid key errors on untrusted data
        valid_items = [
            item
            for item in items
            if isinstance(item, dict)
            and all(k in item for k in ("client_id", "organization_id", "client_type"))
        ]
        if not valid_items:
            return

        max_concurrency = 5

        for i in range(0, len(valid_items), max_concurrency):
            batch = valid_items[i : i + max_concurrency]
            tasks = [
                self.run_client_enrichment(
                    client_id=item["client_id"],
                    organization_id=item["organization_id"],
                    client_type=item["client_type"],
                    payload_data=payload_data,
                )
                for item in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for item, result in zip(batch, results, strict=False):
                if isinstance(result, Exception):
                    logger.error(
                        "Client enrichment task failed",
                        extra={
                            "client_id": item.get("client_id"),
                            "organization_id": item.get("organization_id"),
                            "client_type": item.get("client_type"),
                            "error": str(result),
                        },
                    )

    # Company enrichment webhook (mapping + processing)
    @staticmethod
    def _map_social_profiles(social: Any) -> list[dict[str, Any]]:
        """Map social profiles to list of dicts with id (same shape as API create/patch)."""
        if not isinstance(social, dict):
            return []
        return [
            {"id": str(uuid.uuid4()), "platform": k.lower(), "url": v.strip()}
            for k, v in social.items()
            if k and isinstance(v, str) and v.strip()
        ]

    @staticmethod
    def _map_string_list(value: Any) -> list[str]:
        """Map string list to list of strings."""
        if not isinstance(value, list):
            return []
        return [s for s in value if isinstance(s, str) and s.strip()]

    # Keys from enriched_company that we map to first-level)
    _COMPANY_MAPPED_KEYS = frozenset(
        {
            "companyName",
            "industry",
            "description",
            "website",
            "socialProfiles",
            "marketAudience",
            "communication",
            "platformPreferences",
            "linkedPages",
            "keyPeople",
            "products",
            "headquarters",
            "alternativeLocations",
        }
    )

    @staticmethod
    def _map_linked_pages(linked: Any) -> list[dict[str, Any]]:
        """Map linked pages to list of dicts with id, page_name, page_url (schema format)."""
        if not isinstance(linked, list):
            return []
        out: list[dict[str, Any]] = []
        for item in linked:
            if not isinstance(item, dict):
                continue
            name = (item.get("pageName") or "").strip()
            link = (item.get("pageLink") or "").strip()
            if name or link:
                out.append(
                    {
                        "id": str(uuid.uuid4()),
                        "page_name": name or link,
                        "page_url": link,
                    }
                )
        return out

    @staticmethod
    def _map_key_people(key_people: Any) -> list[dict[str, Any]]:
        """Map keyPeople[] to client key_people schema: id, name, title, linkedin."""
        if not isinstance(key_people, list):
            return []
        out: list[dict[str, Any]] = []
        for item in key_people:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            title = (item.get("title") or "").strip() or None
            linkedin = (item.get("linkedin") or "").strip() or None
            if name:
                out.append(
                    {
                        "id": str(uuid.uuid4()),
                        "name": name,
                        "title": title,
                        "linkedin": linkedin,
                    }
                )
        return out

    @staticmethod
    def _map_products(products: Any) -> list[dict[str, Any]]:
        """Map products[] to client products schema: id, name, url, description."""
        if not isinstance(products, list):
            return []
        out: list[dict[str, Any]] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            url = (item.get("url") or "").strip() or None
            description = (item.get("description") or "").strip() or None
            if name:
                out.append(
                    {
                        "id": str(uuid.uuid4()),
                        "name": name,
                        "url": url,
                        "description": description,
                    }
                )
        return out

    @staticmethod
    def _map_addresses_from_company(enriched_company: dict[str, Any]) -> list[dict[str, Any]]:
        """Build address rows from headquarters + alternativeLocations for bulk_create.
        Returns list of dicts with address_line1, city, country, etc. (no client_id)."""
        rows: list[dict[str, Any]] = []
        # Headquarters first (treat as primary if present)
        head = enriched_company.get("headquarters")
        if isinstance(head, dict):
            addr_line = (head.get("address") or "").strip()
            city = (head.get("city") or "").strip() or None
            country = (head.get("country") or "").strip() or "United States"
            if addr_line:
                rows.append(
                    {
                        "address_line1": addr_line[:1000],
                        "city": city,
                        "country": country,
                        "is_primary": False,
                    }
                )
        # Alternative locations (all non-primary)
        alts = enriched_company.get("alternativeLocations")
        if isinstance(alts, list):
            for item in alts:
                if not isinstance(item, dict):
                    continue
                addr_line = (item.get("address") or "").strip()
                city = (item.get("city") or "").strip() or None
                country = (item.get("country") or "").strip() or "United States"
                if addr_line:
                    rows.append(
                        {
                            "address_line1": addr_line[:1000],
                            "city": city,
                            "country": country,
                            "is_primary": False,
                        }
                    )
        return rows

    @staticmethod
    def _build_simple_company_updates(enriched_company: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for name, industry, description, websites from enriched_company."""
        update: dict[str, Any] = {}
        for src_key, tgt_key in (
            ("companyName", "name"),
            ("industry", "industry"),
            ("description", "description"),
        ):
            val = enriched_company.get(src_key)
            if isinstance(val, str):
                update[tgt_key] = val.strip()
        website_val = enriched_company.get("website")
        if isinstance(website_val, str):
            url = website_val.strip()
            update["websites"] = (
                [{"id": str(uuid.uuid4()), "url": url, "type": "primary", "is_primary": True}]
                if url
                else []
            )
        return update

    @staticmethod
    def _build_social_and_market_updates(enriched_company: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for social_pages and target_market_segments."""
        update: dict[str, Any] = {}
        social = enriched_company.get("socialProfiles")
        if social is not None:
            update["social_pages"] = ClientEnrichmentService._map_social_profiles(social)
        market = enriched_company.get("marketAudience")
        if market is not None:
            update["target_market_segments"] = (
                ClientEnrichmentService._map_string_list(market.get("marketSegments"))
                if isinstance(market, dict)
                else []
            )
        return update

    @staticmethod
    def _build_tech_comm_linked_updates(enriched_company: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for tech stack (platformPreferences)
        communication, terminologies, linked_pages."""
        update: dict[str, Any] = {}
        tech = enriched_company.get("platformPreferences")
        if tech is not None:
            update["current_tech_stack"] = ClientEnrichmentService._map_string_list(tech)
        comm = enriched_company.get("communication")
        if isinstance(comm, dict):
            update["preferred_communication_channels"] = ClientEnrichmentService._map_string_list(
                comm.get("channels")
            )
            update["industry_specific_terminologies"] = ClientEnrichmentService._map_string_list(
                comm.get("industryTerminology")
            )
        elif comm is not None:
            update["preferred_communication_channels"] = []
            update["industry_specific_terminologies"] = []
        linked = enriched_company.get("linkedPages")
        if linked is not None:
            update["linked_pages"] = ClientEnrichmentService._map_linked_pages(linked)
        return update

    @staticmethod
    def _build_key_people_products_updates(enriched_company: dict[str, Any]) -> dict[str, Any]:
        """Build update dict for key_people and products from enrichment."""
        update: dict[str, Any] = {}
        key_people = enriched_company.get("keyPeople")
        if key_people is not None:
            update["key_people"] = ClientEnrichmentService._map_key_people(key_people)
        products = enriched_company.get("products")
        if products is not None:
            update["products"] = ClientEnrichmentService._map_products(products)
        return update

    @staticmethod
    def _build_company_additional_details(enriched_company: dict[str, Any]) -> dict[str, Any]:
        """Extract unmapped enrichment keys into additional_details JSON."""
        return {
            k: v
            for k, v in enriched_company.items()
            if k not in ClientEnrichmentService._COMPANY_MAPPED_KEYS
        }

    @staticmethod
    def build_company_enrichment_update(
        enriched_company: dict[str, Any],
        existing_client: dict[str, Any] | None = None,
        existing_additional_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build client update from webhook enriched_company.
        Only updates fields allowed in Update API. Does not overwrite non-empty
        existing top-level fields with empty enrichment values."""
        try:
            if not enriched_company or not isinstance(enriched_company, dict):
                return {}

            update: dict[str, Any] = {}
            update.update(ClientEnrichmentService._build_simple_company_updates(enriched_company))
            update.update(
                ClientEnrichmentService._build_social_and_market_updates(enriched_company)
            )
            update.update(ClientEnrichmentService._build_tech_comm_linked_updates(enriched_company))
            update.update(
                ClientEnrichmentService._build_key_people_products_updates(enriched_company)
            )

            merged_additional = existing_additional_data or {}
            merged_additional["enriched_company"] = enriched_company
            merged_additional["additional_details"] = (
                ClientEnrichmentService._build_company_additional_details(enriched_company)
            )
            update["additional_data"] = merged_additional
            # Set enrichment status to completed (field name: enrichment_status)
            update["enrichment_status"] = ClientEnrichmentStatus.COMPLETED.value
            update["enrichment_done"] = True
            update["last_enriched_at"] = datetime.now(timezone.utc)

            # Never overwrite non-empty existing with empty enrichment
            merged = _merge_update_without_overwriting_empty(update, existing_client)
            if "social_pages" in merged and existing_client is not None:
                merged["social_pages"] = _merge_social_pages_by_platform(
                    enriched_social_pages=merged.get("social_pages") or [],
                    existing_social_pages=existing_client.get("social_pages"),
                )
            return merged
        except Exception as e:
            logger.error(
                "Error building company enrichment update",
                extra={"error": str(e)},
            )
            return {}

    async def process_company_enrichment_webhook(
        self, conn: asyncpg.Connection, body: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Process company enrichment webhook:
        find client by request_id, apply enriched data. Idempotent.

        Returns:
            (client_id, organization_id) on success, None when not processed.
        """
        request_id = body.get("request_id") if body else None
        if not request_id or not isinstance(request_id, str):
            return None
        enriched_company = body.get("enriched_company") if body else None
        if not enriched_company or not isinstance(enriched_company, dict):
            return None

        repo = CompaniesRepository(conn)
        existing = await repo.get_company_for_update_by_enrichment_request_id(
            enrichment_request_id=request_id
        )
        if not existing:
            logger.error(
                "Enrichment webhook: no company found for request_id",
                extra={"request_id": request_id},
            )
            return None
        company_id = existing["id"]
        organization_id = existing["organization_id"]
        existing_additional_raw = existing.get("additional_data")
        existing_additional = safe_json_loads(existing_additional_raw)

        update_data = self.build_company_enrichment_update(
            enriched_company,
            existing_client=existing,
            existing_additional_data=existing_additional,
        )
        _normalize_webhook_update_payload(update_data, COMPANY_JSONB_COLUMNS)
        await repo.update_company(
            company_id=str(company_id),
            organization_id=str(organization_id),
            update_data=update_data,
        )

        # Add new addresses from enrichment (do not override existing; only add)
        new_address_rows = ClientEnrichmentService._map_addresses_from_company(enriched_company)
        if new_address_rows:
            addresses_data = [{"company_id": str(company_id), **row} for row in new_address_rows]
            await repo.create_company_addresses(addresses_data)

        logger.info(
            "Company updated from enrichment webhook",
            extra={"company_id": str(company_id), "request_id": request_id},
        )
        return (str(company_id), str(organization_id))

    # Person enrichment webhook (mapping + processing)
    @staticmethod
    def _map_work_history_from_enriched(work_history: Any) -> list[dict[str, Any]]:
        """Map enrichment workHistory[] to client work_history schema
        (job_title, company, start_date, end_date, current)."""
        if not isinstance(work_history, list):
            return []
        out: list[dict[str, Any]] = []
        for item in work_history:
            if not isinstance(item, dict):
                continue
            company_name = (item.get("companyName") or "").strip()
            title = (item.get("title") or "").strip()
            start_date = item.get("startDate")
            end_date = item.get("endDate")
            start_str = str(start_date).strip() if start_date is not None else ""
            end_str = str(end_date).strip() if end_date is not None else None
            current = end_date is None or (isinstance(end_date, str) and not end_date.strip())
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "job_title": title or "",
                    "company": company_name or "",
                    "start_date": start_str,
                    "end_date": end_str,
                    "current": current,
                }
            )
        return out

    @staticmethod
    def _map_education_from_enriched(education: Any) -> list[dict[str, Any]]:
        """Map enrichment education[] to client educational_history schema
        (university, degree, field_of_study, start_date, end_date)."""
        if not isinstance(education, list):
            return []
        out: list[dict[str, Any]] = []
        for item in education:
            if not isinstance(item, dict):
                continue
            school = (item.get("school") or "").strip()
            degree = (item.get("degree") or "").strip()
            field = (item.get("field") or "").strip()
            year_start = item.get("yearStart")
            year_end = item.get("yearEnd")
            start_str = str(year_start).strip() if year_start is not None else ""
            end_str = str(year_end).strip() if year_end is not None else None
            out.append(
                {
                    "id": str(uuid.uuid4()),
                    "university": school or "",
                    "degree": degree or "",
                    "field_of_study": field,
                    "start_date": start_str,
                    "end_date": end_str,
                }
            )
        return out

    # Keys from enriched_profile that we map to first-level (rest -> additional_info)
    _PERSON_MAPPED_KEYS = frozenset(
        {
            "personalInfo",
            "companyInfo",
            "socialProfiles",
            "skills",
            "workHistory",
            "education",
        }
    )

    @staticmethod
    def _build_person_additional_info(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Extract unmapped enrichment keys into additional_info JSON."""
        return {
            k: v
            for k, v in enriched_profile.items()
            if k not in ClientEnrichmentService._PERSON_MAPPED_KEYS
        }

    @staticmethod
    def _build_person_simple_updates(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Build name, industry, websites from enriched_profile (person)."""
        update: dict[str, Any] = {}
        personal = enriched_profile.get("personalInfo") or {}
        if isinstance(personal, dict):
            first = (personal.get("firstName") or "").strip()
            last = (personal.get("lastName") or "").strip()
            name = build_full_name(first, last).strip()
            if name:
                update["name"] = name
        company_info = enriched_profile.get("companyInfo") or {}
        if isinstance(company_info, dict):
            industry = company_info.get("industry")
            if isinstance(industry, str) and industry.strip():
                update["industry"] = industry.strip()
            website = company_info.get("website")
            if isinstance(website, str) and website.strip():
                update["websites"] = [
                    {
                        "id": str(uuid.uuid4()),
                        "url": website.strip(),
                        "type": "primary",
                        "is_primary": True,
                    }
                ]
        return update

    @staticmethod
    def _build_person_social_skills_updates(enriched_profile: dict[str, Any]) -> dict[str, Any]:
        """Build social_pages and skills from enriched_profile (person)."""
        update: dict[str, Any] = {}
        social = enriched_profile.get("socialProfiles")
        if social is not None:
            update["social_pages"] = ClientEnrichmentService._map_social_profiles(social)
        skills = enriched_profile.get("skills")
        if isinstance(skills, list):
            update["skills"] = ClientEnrichmentService._map_string_list(skills)
        return update

    @staticmethod
    def build_person_enrichment_update(
        enriched_profile: dict[str, Any],
        existing_client: dict[str, Any] | None = None,
        existing_additional_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build client update from webhook enriched_profile (person).
        Only updates fields allowed in Update API. Exact schema: work_history,
        educational_history, websites, social_pages, skills. Does not overwrite
        non-empty existing top-level fields with empty enrichment values."""
        if not enriched_profile or not isinstance(enriched_profile, dict):
            return {}

        update: dict[str, Any] = {}
        update.update(ClientEnrichmentService._build_person_simple_updates(enriched_profile))
        update.update(ClientEnrichmentService._build_person_social_skills_updates(enriched_profile))

        work_history = enriched_profile.get("workHistory")
        if work_history is not None:
            update["work_history"] = ClientEnrichmentService._map_work_history_from_enriched(
                work_history
            )
        education = enriched_profile.get("education")
        if education is not None:
            update["educational_history"] = ClientEnrichmentService._map_education_from_enriched(
                education
            )

        merged_additional = existing_additional_data or {}
        merged_additional["enriched_profile"] = enriched_profile
        merged_additional["additional_info"] = (
            ClientEnrichmentService._build_person_additional_info(enriched_profile)
        )
        update["additional_data"] = merged_additional
        # Set enrichment status to completed (field name: enrichment_status)
        update["enrichment_status"] = ClientEnrichmentStatus.COMPLETED.value
        update["enrichment_done"] = True
        update["last_enriched_at"] = datetime.now(timezone.utc)

        merged = _merge_update_without_overwriting_empty(update, existing_client)
        if "social_pages" in merged and existing_client is not None:
            merged["social_pages"] = _merge_social_pages_by_platform(
                enriched_social_pages=merged.get("social_pages") or [],
                existing_social_pages=existing_client.get("social_pages"),
            )
        return merged

    async def process_person_enrichment_webhook(
        self, conn: asyncpg.Connection, body: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Process person enrichment webhook:
        find client by request_id, apply enriched_profile. Idempotent.

        Returns:
            (client_id, organization_id) on success, None when not processed.
        """
        request_id = body.get("request_id") if body else None
        if not request_id or not isinstance(request_id, str):
            return None
        enriched_profile = body.get("enriched_profile") if body else None
        if not enriched_profile or not isinstance(enriched_profile, dict):
            return None

        repo = ContactsRepository(conn)
        existing = await repo.get_contact_for_update_by_enrichment_request_id(
            enrichment_request_id=request_id
        )
        if not existing:
            logger.error(
                "Enrichment webhook: no contact found for request_id",
                extra={"request_id": request_id},
            )
            return None
        contact_id = existing["id"]
        organization_id = existing["organization_id"]
        existing_additional_raw = existing.get("additional_data")
        existing_additional = safe_json_loads(existing_additional_raw)

        # Store enrichment-provided profile image in our CDN (R2) and persist the object key.
        # Best-effort: failures must not break enrichment updates.
        profile_photo_key: str | None = None
        try:
            profile_photo_key = await self._maybe_store_profile_photo_from_enrichment(
                enriched_profile=enriched_profile,
                contact_id=str(contact_id),
                organization_id=str(organization_id),
                existing_profile_photo_url=existing.get("profile_photo_url"),
            )
        except Exception as e:
            logger.warning(
                "Profile photo storage threw; continuing enrichment update",
                extra={
                    "contact_id": str(contact_id),
                    "organization_id": str(organization_id),
                    "request_id": str(request_id),
                    "error": str(e),
                },
            )
        update_data = self.build_contact_enrichment_update(
            enriched_profile,
            existing_contact=existing,
            existing_additional_data=existing_additional,
        )
        if profile_photo_key:
            update_data["profile_photo_url"] = profile_photo_key
        _normalize_webhook_update_payload(update_data, CONTACT_JSONB_COLUMNS)
        await repo.update_contact(
            contact_id=str(contact_id),
            organization_id=str(organization_id),
            update_data=update_data,
        )

        logger.info(
            "Contact updated from enrichment webhook",
            extra={"contact_id": str(contact_id), "request_id": request_id},
        )
        return (str(contact_id), str(organization_id))

    @staticmethod
    def _is_safe_public_http_url(url: str) -> bool:
        """Return True if URL is http(s) and not obviously local/private.

        This is a basic SSRF guard intended for enrichment-provided URLs.
        DNS names are allowed (no resolution performed).
        """
        if not url or len(url) > 4096:
            return False
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname or ""
        if not host:
            return False
        if host in {"localhost"} or host.endswith(".localhost"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if any(
                (
                    ip.is_private,
                    ip.is_loopback,
                    ip.is_link_local,
                    ip.is_multicast,
                    ip.is_reserved,
                    ip.is_unspecified,
                )
            ):
                return False
        except ValueError:
            # Not an IP literal (DNS name). Allow.
            pass
        return True

    @staticmethod
    def _ext_and_content_type_from_response(resp: httpx.Response) -> tuple[str, str]:
        """Infer safe (ext, content_type) for an image response."""
        content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        mapping = {
            "image/jpeg": ("jpg", "image/jpeg"),
            "image/jpg": ("jpg", "image/jpeg"),
            "image/png": ("png", "image/png"),
            "image/webp": ("webp", "image/webp"),
            "image/gif": ("gif", "image/gif"),
        }
        if content_type in mapping:
            return mapping[content_type]
        # Default: store as jpeg key; keep content-type generic to avoid surprises.
        return ("jpg", "image/jpeg")

    @staticmethod
    def _extract_profile_photo_url(enriched_profile: dict[str, Any]) -> str | None:
        """Return trimmed `personalInfo.profileUrl` from an enriched profile."""
        if not isinstance(enriched_profile, dict):
            return None
        personal = enriched_profile.get("personalInfo")
        if not isinstance(personal, dict):
            return None
        raw_url = personal.get("profileUrl")
        if not isinstance(raw_url, str):
            return None
        url = raw_url.strip()
        return url or None

    async def _download_profile_photo(self, *, remote_url: str) -> tuple[bytes, str, str] | None:
        """Download image bytes and return (bytes, ext, content_type)."""
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"user-agent": "house-of-apps-legal-ai/1.0"},
        ) as client:
            async with client.stream("GET", remote_url) as resp:
                resp.raise_for_status()
                ext, content_type = self._ext_and_content_type_from_response(resp)
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > _MAX_PROFILE_PHOTO_BYTES:
                        raise ValueError("profile photo exceeds size limit")
                image_bytes = bytes(buf)
                if not image_bytes:
                    return None
        return (image_bytes, ext, content_type)

    @staticmethod
    def _build_profile_photo_object_key(*, organization_id: str, contact_id: str, ext: str) -> str:
        """Build deterministic-ish object key for stored profile photos."""
        return f"contacts/{organization_id}/{contact_id}/profile_{uuid.uuid4().hex}.{ext}"

    async def _upload_profile_photo_to_r2(
        self,
        *,
        bucket: str,
        object_key: str,
        image_bytes: bytes,
        content_type: str,
    ) -> None:
        """Upload bytes to R2 using synchronous boto3 client (threaded)."""

        def _upload_sync() -> None:
            r2_client = get_r2_client()
            r2_client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=image_bytes,
                ContentType=content_type,
            )

        await asyncio.to_thread(_upload_sync)

    async def _maybe_store_profile_photo_from_enrichment(
        self,
        *,
        enriched_profile: dict[str, Any],
        contact_id: str,
        organization_id: str,
        existing_profile_photo_url: Any,
    ) -> str | None:
        """Download enrichment `personalInfo.profileUrl`, upload to R2, return object key.

        Never overwrites an existing non-empty `profile_photo_url`.
        Best-effort: returns None on any failure.
        """
        has_existing = isinstance(existing_profile_photo_url, str) and bool(
            existing_profile_photo_url.strip()
        )
        if has_existing:
            return None

        remote_url = self._extract_profile_photo_url(enriched_profile)
        if remote_url is None:
            return None
        if not self._is_safe_public_http_url(remote_url):
            logger.warning(
                "Skipping unsafe enrichment profileUrl",
                extra={"contact_id": contact_id, "organization_id": organization_id},
            )
            return None

        try:
            downloaded = await self._download_profile_photo(remote_url=remote_url)
            if downloaded is None:
                return None
            image_bytes, ext, content_type = downloaded

            object_key = self._build_profile_photo_object_key(
                organization_id=organization_id,
                contact_id=contact_id,
                ext=ext,
            )
            bucket = shared_settings.cloudflare_r2.bucket_name
            if not bucket:
                return None

            await self._upload_profile_photo_to_r2(
                bucket=bucket,
                object_key=object_key,
                image_bytes=image_bytes,
                content_type=content_type,
            )
            return object_key
        except Exception as e:
            logger.warning(
                "Failed to store enrichment profile photo",
                extra={
                    "contact_id": contact_id,
                    "organization_id": organization_id,
                    "error": str(e),
                },
            )
            return None

    async def fetch_and_store_sales_intelligence_for_request(
        self,
        request_id: str,
        enriched_profile: dict[str, Any] | None = None,
        enriched_company: dict[str, Any] | None = None,
    ) -> None:
        """Fetch sales intelligence for an enrichment request and persist it.

        Single generic entry point used from a background task for both company and
        person webhooks, so the webhook response is not blocked by the external
        sales-intelligence service.

        Pass enriched_company for company webhooks (person_info={}, company_info from
        payload). Pass enriched_profile for person webhooks (person_info and
        company_info from profile). It is safe to call multiple times for the same
        request_id; the latest successful response overwrites sales_intelligence.
        """
        if not request_id or not isinstance(request_id, str):
            return

        # Sales intelligence is stored on companies only.
        _ = enriched_profile  # kept for webhook signature compatibility
        if not (isinstance(enriched_company, dict) and enriched_company):
            return

        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            repo = CompaniesRepository(conn)
            existing = await repo.get_company_for_update_by_enrichment_request_id(
                enrichment_request_id=request_id
            )
            if not existing:
                logger.error(
                    "Sales intelligence: no company found for request_id",
                    extra={"request_id": request_id},
                )
                return

            company_id = existing["id"]
            organization_id = existing["organization_id"]

            await self._store_sales_intelligence_for_company(
                company_id=company_id,
                organization_id=organization_id,
                person_info={},
                company_info=enriched_company,
                conn=conn,
            )

            logger.info(
                "Sales intelligence stored for company",
                extra={"company_id": str(company_id), "request_id": request_id},
            )
