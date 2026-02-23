"""Client enrichment service.

Calls the external enrichment API for person (/enrich) and company (/enrich/company).
Used after client creation to trigger async enrichment; request_id and status
are stored on the client. Webhook handling for company enrichment is separate.
"""

from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.db.repositories import ClientRepository
from apps.user_service.app.schemas.enums import ClientEnrichmentStatus, ClientType
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.logger import get_logger

logger = get_logger("client_enrichment_service")

ENRICHMENT_WEBHOOK_URL = "https://api-v2.houseofapps.ai/v1/webhooks/enrichment"


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

    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    @classmethod
    def from_settings(cls) -> "ClientEnrichmentService":
        """Create an instance from app config."""
        settings = app_settings.enrichment_service
        return cls(base_url=settings.base_url, timeout_seconds=settings.timeout_seconds)

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
        name = build_full_name(data.get("first_name") or "", data.get("last_name") or "").strip()
        phone_isd = data.get("phone_isd_code") or ""
        phone_num = data.get("phone_number") or ""
        phone = f"{phone_isd}{phone_num}".strip() or None
        country = _first_country_from_addresses(data)

        payload: dict[str, Any] = {}
        if name:
            payload["name"] = name
        if data.get("email"):
            payload["email"] = data["email"]
        if data.get("company"):
            payload["company"] = data["company"]
        if country:
            payload["country"] = country
        if phone:
            payload["phone"] = phone
        if not payload:
            payload["name"] = name or "Unknown"
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    def _build_company_payload(
        self,
        client_id: str,
        organization_id: str,
        data: dict[str, Any],
        webhook_url: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Build payload for POST /enrich/company.
        At least one of company_name, website_url, linkedin_url, company_email required."""
        website_url = _first_website_url(data)
        linkedin_url = _linkedin_url_from_social_pages(data)
        location = _first_country_from_addresses(data)
        company_name = (data.get("name") or "").strip() or "Unknown"

        payload: dict[str, Any] = {
            "account_id": organization_id,
            "external_id": client_id,
            "company_name": company_name,
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
        if project_id:
            payload["project_id"] = project_id
        if webhook_url:
            payload["webhook_url"] = webhook_url
        return payload

    async def enrich_person(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /enrich for person. Returns dict with request_id, status, message."""
        return await self._post("/enrich", payload)

    async def enrich_company(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /enrich/company for company. Returns dict with request_id, status, message."""
        return await self._post("/enrich/company", payload)

    async def run_client_enrichment(
        self,
        client_id: str,
        organization_id: str,
        client_type: str,
        payload_data: dict[str, Any],
    ) -> None:
        """Run enrichment after client creation: call API,
        then update client with request_id and status.
        Only runs for client_type 'person' or 'company'. Uses its own DB connection.
        """
        webhook_url = ENRICHMENT_WEBHOOK_URL
        if client_type == ClientType.PERSON.value:
            body = self._build_person_payload(payload_data, webhook_url=webhook_url)
            response = await self.enrich_person(body)
        else:
            body = self._build_company_payload(
                client_id, organization_id, payload_data, webhook_url=webhook_url
            )
            response = await self.enrich_company(body)

        request_id = response.get("request_id") if isinstance(response, dict) else None
        if not request_id:
            logger.error(
                "Enrichment API did not return request_id",
                extra={"client_id": client_id, "response": response},
            )
            return

        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            repo = ClientRepository(conn)
            await repo.update_client(
                client_id,
                organization_id,
                {
                    "enrichment_request_id": request_id,
                    "enrichment_status": ClientEnrichmentStatus.REQUESTED.value,
                },
            )
        logger.info(
            "Client enrichment requested and record updated",
            extra={"client_id": client_id, "enrichment_request_id": request_id},
        )

    # Company enrichment webhook (mapping + processing)
    @staticmethod
    def _map_social_profiles(social: Any) -> list[dict[str, Any]]:
        """Map social profiles to list of dicts."""
        if not isinstance(social, dict):
            return []
        return [
            {"platform": k.lower(), "url": v.strip()}
            for k, v in social.items()
            if k and isinstance(v, str) and v.strip()
        ]

    @staticmethod
    def _map_string_list(value: Any) -> list[str]:
        """Map string list to list of strings."""
        if not isinstance(value, list):
            return []
        return [s for s in value if isinstance(s, str) and s.strip()]

    @staticmethod
    def _map_linked_pages(linked: Any) -> list[dict[str, Any]]:
        """Map linked pages to list of dicts."""
        if not isinstance(linked, list):
            return []
        out: list[dict[str, Any]] = []
        for item in linked:
            if not isinstance(item, dict):
                continue
            name = (item.get("pageName") or "").strip()
            link = (item.get("pageLink") or "").strip()
            if name or link:
                out.append({"page_name": name or link, "page_url": link})
        return out

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
                [{"url": url, "type": "primary", "is_primary": True}] if url else []
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
        """Build update dict for tech stack, communication channels, terminologies, linked_pages."""
        update: dict[str, Any] = {}
        tech = enriched_company.get("technologies")
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
    def build_company_enrichment_update(
        enriched_company: dict[str, Any],
        existing_additional_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build client update from webhook enriched_company.
        Overwrites client fields when present in enriched_company."""
        if not enriched_company or not isinstance(enriched_company, dict):
            return {}

        update: dict[str, Any] = {}
        update.update(ClientEnrichmentService._build_simple_company_updates(enriched_company))
        update.update(ClientEnrichmentService._build_social_and_market_updates(enriched_company))
        update.update(ClientEnrichmentService._build_tech_comm_linked_updates(enriched_company))

        merged_additional = dict(existing_additional_data or {})
        merged_additional["enriched_company"] = enriched_company
        update["additional_data"] = merged_additional
        update["enrichment_status"] = ClientEnrichmentStatus.COMPLETED.value
        update["enrichment_done"] = True
        update["last_enriched_at"] = datetime.now(timezone.utc).isoformat()
        return update

    async def process_company_enrichment_webhook(
        self, conn: asyncpg.Connection, body: dict[str, Any]
    ) -> bool:
        """Process company enrichment webhook:
        find client by request_id, apply enriched data. Idempotent."""
        request_id = body.get("request_id") if body else None
        if not request_id or not isinstance(request_id, str):
            return False
        enriched_company = body.get("enriched_company") if body else None
        if not enriched_company or not isinstance(enriched_company, dict):
            return False

        repo = ClientRepository(conn)
        client = await repo.get_client_by_enrichment_request_id(request_id)
        if not client:
            logger.error(
                "Enrichment webhook: no client found for request_id",
                extra={"request_id": request_id},
            )
            return False
        client_id = client["id"]
        organization_id = client["organization_id"]
        existing = await repo.get_client_for_update(client_id, organization_id)
        existing_additional = (existing or {}).get("additional_data")
        update_data = self.build_company_enrichment_update(enriched_company, existing_additional)
        await repo.update_client(client_id, organization_id, update_data)
        logger.info(
            "Client updated from enrichment webhook",
            extra={"cxlient_id": client_id, "request_id": request_id},
        )
        return True
