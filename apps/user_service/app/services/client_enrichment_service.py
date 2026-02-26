"""Client enrichment service.

Calls the external enrichment API for person (/enrich) and company (/enrich/company).
Used after client creation to trigger async enrichment; request_id and status
are stored on the client. Webhook handling for company and person enrichment.
Enrichment updates only fields allowed in the Update API; never overwrites
non-empty existing data with empty enrichment values.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.db.repositories import ClientRepository
from apps.user_service.app.db.repositories.client_repository import CLIENT_JSONB_COLUMNS
from apps.user_service.app.schemas.enums import ClientEnrichmentStatus, ClientType
from apps.user_service.app.utils.common_utils import parse_json_field, safe_json_loads
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.logger import get_logger

logger = get_logger("client_enrichment_service")


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
        webhook_url = self._webhook_url
        if client_type == ClientType.PERSON.value:
            body = self._build_person_payload(payload_data, webhook_url=webhook_url)
            response = await self.enrich_person(body)
        else:
            body = self._build_company_payload(
                client_id,
                organization_id,
                payload_data,
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
            return _merge_update_without_overwriting_empty(update, existing_client)
        except Exception as e:
            logger.error(
                "Error building company enrichment update",
                extra={"error": str(e)},
            )
            return {}

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
        existing = await repo.get_client_for_update(enrichment_request_id=request_id)
        if not existing:
            logger.error(
                "Enrichment webhook: no client found for request_id",
                extra={"request_id": request_id},
            )
            return False
        client_id = existing["id"]
        organization_id = existing["organization_id"]
        existing_additional_raw = existing.get("additional_data")
        existing_additional = safe_json_loads(existing_additional_raw)

        update_data = self.build_company_enrichment_update(
            enriched_company, existing_client=existing, existing_additional_data=existing_additional
        )
        _normalize_webhook_update_payload(update_data, CLIENT_JSONB_COLUMNS)
        await repo.update_client(client_id, organization_id, update_data)

        # Add new addresses from enrichment (do not override existing; only add)
        new_address_rows = ClientEnrichmentService._map_addresses_from_company(enriched_company)
        if new_address_rows:
            addresses_data = [{"client_id": client_id, **row} for row in new_address_rows]
            await repo.bulk_create_addresses(addresses_data)

        logger.info(
            "Client updated from enrichment webhook",
            extra={"client_id": client_id, "request_id": request_id},
        )
        return True

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

        return _merge_update_without_overwriting_empty(update, existing_client)

    async def process_person_enrichment_webhook(
        self, conn: asyncpg.Connection, body: dict[str, Any]
    ) -> bool:
        """Process person enrichment webhook:
        find client by request_id, apply enriched_profile. Idempotent."""
        request_id = body.get("request_id") if body else None
        if not request_id or not isinstance(request_id, str):
            return False
        enriched_profile = body.get("enriched_profile") if body else None
        if not enriched_profile or not isinstance(enriched_profile, dict):
            return False

        repo = ClientRepository(conn)
        existing = await repo.get_client_for_update(enrichment_request_id=request_id)
        if not existing:
            logger.error(
                "Enrichment webhook: no client found for request_id",
                extra={"request_id": request_id},
            )
            return False
        client_id = existing["id"]
        organization_id = existing["organization_id"]
        existing_additional_raw = existing.get("additional_data")
        existing_additional = safe_json_loads(existing_additional_raw)
        update_data = self.build_person_enrichment_update(
            enriched_profile, existing_client=existing, existing_additional_data=existing_additional
        )
        _normalize_webhook_update_payload(update_data, CLIENT_JSONB_COLUMNS)
        await repo.update_client(client_id, organization_id, update_data)
        logger.info(
            "Client updated from person enrichment webhook",
            extra={"client_id": client_id, "request_id": request_id},
        )
        return True
