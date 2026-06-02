"""Populate organization AI Overview business_overview via Isometrik strands agents."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.constants.ai_overview_defaults import (
    AI_OVERVIEW_SETTINGS_KEY,
    DEFAULT_OVERVIEW_PROMPTS,
    OVERVIEW_PROMPT_ENTITY_TYPES,
)
from apps.user_service.app.db.repositories import OrganizationRepository
from apps.user_service.app.schemas.enums import KafkaTopics, OrganizationEventType
from apps.user_service.app.services.ai_overview_settings_ops import (
    merge_ai_overview_settings_into_settings,
)
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.kafka_event_service import get_kafka_event_service
from apps.user_service.app.utils.common_utils import (
    parse_json_field,
    serialize_pydantic_models,
    validate_uuid_format,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_db.drivers.asyncpg_client import AcquireConnection, get_pool
from libs.shared_utils.isometrik_strands_client import call_strands_agent
from libs.shared_utils.logger import get_logger
from libs.shared_utils.openai_chat_service import create_chat_completion

logger = get_logger("org_business_overview_enrichment")

_STRANDS_ERROR_BODY_MAX_LEN = 2000


def _log_strands_agent_failure(event: str, exc: BaseException, **context: Any) -> None:
    """Log Strands HTTP failures with message, context, and traceback.

    Extra context fields are included in the log line, not as structured extras.
    """
    parts = [f"{event}: {exc}"]
    if context:
        parts.append(f"context={context}")
    if isinstance(exc, httpx.HTTPStatusError):
        parts.append(f"status_code={exc.response.status_code}")
        try:
            body = exc.response.text
            if body:
                trimmed = body[:_STRANDS_ERROR_BODY_MAX_LEN]
                if len(body) > _STRANDS_ERROR_BODY_MAX_LEN:
                    trimmed += "...(truncated)"
                parts.append(f"response_body={trimmed}")
        except Exception:
            pass
    elif isinstance(exc, httpx.RequestError) and exc.request is not None:
        parts.append(f"request_url={exc.request.url}")
    logger.error(" | ".join(parts), exc_info=True)


_MAX_BUSINESS_OVERVIEW_LEN = 2000
_MAX_ORG_NAME_LEN = 255
_MIN_DOMAIN_CONFIDENCE = 50
_PROMPT_GEN_MAX_TOKENS = 1600

# How to steer future overview agents by industry (not full prompts—pattern guidance only).
_INDUSTRY_ADAPTATION_SCENARIOS = """
Industry adaptation patterns (pick what matches the business overview; combine if hybrid):

• Technology / SaaS — In Overview emphasize product/module fit, deployment, integrations,
  seat or usage model, renewal vs new business. In Key Insights emphasize technical evaluators,
  security/compliance blockers, POC or pilot timing, champion vs economic buyer.

• Construction / field services — In Overview emphasize project type, site or region,
  GC vs subcontractor role, contract size, schedule. In Key Insights emphasize permits,
  labor or material constraints, change orders, safety, billing milestones.

• Legal / professional services — In Overview emphasize matter or practice area,
  jurisdiction, client segment, fee model, responsible partner. In Key Insights emphasize
  conflicts, filing or court deadlines, scope, referral source, engagement status.

Generated prompts must tell a future agent what kinds of CRM facts to prioritize when
they appear—they must not invent industry details that are not in the data.
"""

_PROMPT_GEN_SYSTEM = (
    """You author stored agent-instruction prompts for CRM AI Overviews.

Return ONLY valid JSON.

Output constraints:
- JSON object with exactly keys: contact, company, lead
- Each value is one STRING (multi-line allowed) that is a complete agent prompt
- Each prompt MUST contain "{{entity_name}}" at least once (literal placeholder)
- Keep the same section headers, EXAMPLE blocks, and RULES block as the platform default
- Keep entity-specific section order:
  contact → Overview, Key Insights, Leads, Companies
  lead → Overview, Key Insights, Contacts, Companies
  company → Overview, Key Insights, Contacts, Leads
- Adapt role framing, vocabulary, and what to stress in Overview / Key Insights to the
  organization's industry and business model (from the user message business overview)
- Do not remove sections or rules; do not add keys; no markdown fences around JSON

"""
    + _INDUSTRY_ADAPTATION_SCENARIOS
    + """

Structural reference — one complete lead prompt (contact and company must mirror this
structure, tone, RULES, and example-block style with their section order):

"""
    + json.dumps({"lead": DEFAULT_OVERVIEW_PROMPTS["lead"]}, indent=2, ensure_ascii=False)
    + """

Respond with one JSON object: contact, company, lead.
"""
)

_ENTITY_NAME_PLACEHOLDER = "{{entity_name}}"


def strands_enrichment_enabled() -> bool:
    """Return True when org enrichment feature and strands agents are configured."""
    iso = shared_settings.isometrik
    return bool(
        iso.org_business_overview_on_create_enabled
        and iso.strands_auth_token.strip()
        and iso.domain_discovery_agent_id.strip()
        and iso.business_overview_agent_id.strip()
    )


def _parse_json_object_text(text: str) -> dict[str, Any]:
    """Parse JSON object from agent/LLM text (may include markdown fences)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("response JSON must be an object")
    return payload


def _strands_response_text(body: dict[str, Any]) -> str | None:
    """Extract non-empty ``text`` from a strands chat response body."""
    raw_text = body.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    return raw_text.strip()


def _has_stored_overview_prompts(settings: dict[str, Any] | None) -> bool:
    """Return True when all entity overview prompts are stored and non-empty."""
    if not isinstance(settings, dict):
        return False
    ai_settings = settings.get(AI_OVERVIEW_SETTINGS_KEY)
    if not isinstance(ai_settings, dict):
        return False
    prompts = ai_settings.get("overview_prompts")
    if not isinstance(prompts, dict):
        return False
    return all(
        isinstance(prompts.get(entity_type), str) and prompts[entity_type].strip()
        for entity_type in OVERVIEW_PROMPT_ENTITY_TYPES
    )


def _sanitize_org_name(name: str) -> str:
    """Trim and bound organization name length for agent payloads."""
    return name.strip()[:_MAX_ORG_NAME_LEN]


def _is_safe_http_url(url: str) -> bool:
    """Return True for http(s) URLs with a non-empty host."""
    parsed = urlparse(url.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _normalize_website_url(url: str | None) -> str | None:
    """Normalize company website to https and validate host (matches create-time rules)."""
    if not isinstance(url, str):
        return None
    value = url.strip()
    if not value:
        return None
    if value.startswith("https://"):
        normalized = value
    elif value.startswith("http://"):
        normalized = f"https://{value[len('http://') :]}"
    else:
        normalized = f"https://{value}"
    return normalized if _is_safe_http_url(normalized) else None


def _parse_overview_prompts_response(raw: str) -> dict[str, str] | None:
    """Parse LLM JSON into validated overview_prompts, or None if invalid."""
    try:
        payload = _parse_json_object_text(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    prompts: dict[str, str] = {}
    for entity_type in OVERVIEW_PROMPT_ENTITY_TYPES:
        value = payload.get(entity_type)
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or _ENTITY_NAME_PLACEHOLDER not in text:
            return None
        prompts[entity_type] = text
    return prompts


class OrgBusinessOverviewEnrichmentService:
    """Discover company website and business overview after org creation."""

    @staticmethod
    def should_enqueue_after_organization_created(
        *,
        organization_id: str,  # pylint: disable=unused-argument
        organization_name: str,
        settings: dict[str, Any] | None,
    ) -> bool:
        """Return True when an enrichment job should be published to Kafka."""
        if not strands_enrichment_enabled():
            return False
        name = (organization_name or "").strip()
        if not name:
            return False
        if OrgBusinessOverviewEnrichmentService._has_business_overview(settings):
            return False
        if _has_stored_overview_prompts(settings):
            return False
        return True

    @staticmethod
    def _has_business_overview(settings: dict[str, Any] | None) -> bool:
        """Return True when settings already contain a non-empty business overview."""
        if not isinstance(settings, dict):
            return False
        ai_settings = settings.get(AI_OVERVIEW_SETTINGS_KEY)
        if not isinstance(ai_settings, dict):
            return False
        overview = ai_settings.get("business_overview")
        return isinstance(overview, str) and bool(overview.strip())

    @staticmethod
    async def enqueue_enrichment_requested(
        *,
        organization_id: str,
        organization_name: str,
        organization_website: str | None = None,
        settings: dict[str, Any] | None,
        actor_user_id: str | None,
    ) -> None:
        """Publish ``organizations.enrichment.requested`` to Kafka (best-effort).

        Never raises; callers must not depend on enrichment for org create success.
        """
        try:
            if not OrgBusinessOverviewEnrichmentService.should_enqueue_after_organization_created(
                organization_id=organization_id,
                organization_name=organization_name,
                settings=settings,
            ):
                return
            if not app_settings.kafka.enabled:
                logger.warning(
                    "org_enrichment_kafka_disabled",
                    extra={"organization_id": organization_id},
                )
                return

            event_payload: dict[str, Any] = {
                "organization_name": _sanitize_org_name(organization_name),
            }
            normalized_website = _normalize_website_url(organization_website)
            if normalized_website:
                event_payload["organization_website"] = normalized_website

            event = EventService().build_event(
                event_type=OrganizationEventType.ENRICHMENT_REQUESTED.value,
                aggregate_id=organization_id,
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                payload=event_payload,
            )
            kafka = get_kafka_event_service()
            await kafka.produce_event(
                event=event,
                key=organization_id,
                topics=[KafkaTopics.ORG_ENRICHMENT.value],
            )
            logger.info(
                "org_enrichment_enqueued",
                extra={"organization_id": organization_id, "event_id": event["event_id"]},
            )
        except Exception:
            logger.exception(
                "org_enrichment_enqueue_failed",
                extra={"organization_id": organization_id},
            )

    @staticmethod
    async def process_enrichment_event(
        *,
        organization_id: str,
        organization_name: str,
        organization_website: str | None = None,
    ) -> None:
        """Run enrichment pipeline (Kafka consumer entrypoint)."""
        if not strands_enrichment_enabled():
            return

        try:
            validate_uuid_format(organization_id, "organization_id")
        except Exception:
            logger.warning(
                "org_business_overview_invalid_organization_id",
                extra={"organization_id": organization_id},
            )
            return

        org_name = _sanitize_org_name(organization_name)
        if not org_name:
            return

        if await OrgBusinessOverviewEnrichmentService._enrichment_already_persisted(
            organization_id
        ):
            logger.info(
                "org_business_overview_skip_already_enriched",
                extra={"organization_id": organization_id},
            )
            return
        website_hint = _normalize_website_url(organization_website)
        if website_hint is None:
            website_hint = await OrgBusinessOverviewEnrichmentService._load_organization_website(
                organization_id
            )

        await OrgBusinessOverviewEnrichmentService._run_enrichment_pipeline(
            organization_id,
            org_name,
            organization_website=website_hint,
        )

    @staticmethod
    async def _load_organization_website(organization_id: str) -> str | None:
        """Read website from org row (domain) or settings.website_url."""
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            repo = OrganizationRepository(db_connection=conn)
            org = await repo.get_organization_by_id(organization_id)
            if not org:
                return None
            domain = _normalize_website_url(org.get("domain"))
            if domain:
                return domain
            settings = parse_json_field(org.get("settings"))
            if isinstance(settings, dict):
                return _normalize_website_url(settings.get("website_url"))
        return None

    @staticmethod
    async def _resolve_website_url(
        *,
        organization_id: str,
        organization_name: str,
        organization_website: str | None,
    ) -> tuple[str | None, str]:
        """Resolve website for agent 2: use provided URL or run domain-discovery agent."""
        provided = _normalize_website_url(organization_website)
        if provided:
            logger.info(
                "org_business_overview_website_provided",
                extra={"organization_id": organization_id, "website": provided},
            )
            return provided, "provided"

        discovered = await OrgBusinessOverviewEnrichmentService._discover_official_website(
            organization_name
        )
        if discovered and _is_safe_http_url(discovered):
            logger.info(
                "org_business_overview_website_discovered",
                extra={"organization_id": organization_id, "website": discovered},
            )
            return discovered.strip(), "discovered"

        return None, "none"

    @staticmethod
    async def _enrichment_already_persisted(organization_id: str) -> bool:
        """Idempotency guard for duplicate Kafka deliveries."""
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            repo = OrganizationRepository(db_connection=conn)
            org = await repo.get_organization_by_id(organization_id)
            if not org:
                return True
            settings = parse_json_field(org.get("settings"))
            return _has_stored_overview_prompts(settings)

    @staticmethod
    async def _discover_official_website(company_name: str) -> str | None:
        """Run domain-discovery agent; return official website URL or None."""
        agent_id = shared_settings.isometrik.domain_discovery_agent_id
        try:
            body = await call_strands_agent(
                agent_id=agent_id,
                message=company_name,
                stream=False,
            )
        except httpx.HTTPError as exc:
            _log_strands_agent_failure(
                "domain_discovery_agent_request_failed",
                exc,
                agent_id=agent_id,
                company_name=company_name,
            )
            return None
        except Exception as exc:
            logger.error(
                "domain_discovery_agent_request_failed: %s | agent_id=%s company_name=%s",
                exc,
                agent_id,
                company_name,
                exc_info=True,
            )
            return None
        raw_text = _strands_response_text(body)
        if raw_text is None:
            logger.info(
                "domain_discovery_agent_empty_text | agent_id=%s company_name=%s body_keys=%s",
                agent_id,
                company_name,
                list(body.keys()) if isinstance(body, dict) else None,
            )
            return None
        try:
            parsed = _parse_json_object_text(raw_text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.info(
                "domain_discovery_agent_invalid_json: %s | raw_text_preview=%s",
                exc,
                raw_text[:500],
            )
            return None

        website = parsed.get("official_website")
        if not isinstance(website, str) or not website.strip():
            return None

        confidence = parsed.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < _MIN_DOMAIN_CONFIDENCE:
            logger.info("domain_discovery_low_confidence", extra={"confidence": confidence})
            return None

        return website.strip()

    @staticmethod
    async def _fetch_business_overview(website_url: str) -> str | None:
        """Run business-overview agent; return ``business_overview`` text or None."""
        agent_id = shared_settings.isometrik.business_overview_agent_id
        try:
            body = await call_strands_agent(
                agent_id=agent_id,
                message=website_url,
                stream=False,
            )
        except httpx.HTTPError as exc:
            _log_strands_agent_failure(
                "business_overview_agent_request_failed",
                exc,
                agent_id=agent_id,
                website_url=website_url,
            )
            return None
        except Exception as exc:
            logger.error(
                "business_overview_agent_request_failed: %s | agent_id=%s website_url=%s",
                exc,
                agent_id,
                website_url,
                exc_info=True,
            )
            return None
        raw_text = _strands_response_text(body)
        if raw_text is None:
            logger.info(
                "business_overview_agent_empty_text | agent_id=%s website_url=%s body_keys=%s",
                agent_id,
                website_url,
                list(body.keys()) if isinstance(body, dict) else None,
            )
            return None
        try:
            parsed = _parse_json_object_text(raw_text)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.info(
                "business_overview_agent_invalid_json: %s | raw_text_preview=%s",
                exc,
                raw_text[:500],
            )
            return None

        overview = parsed.get("business_overview")
        if not isinstance(overview, str) or not overview.strip():
            logger.info(
                "business_overview_agent_missing_field | website_url=%s parsed_keys=%s",
                website_url,
                list(parsed.keys()) if isinstance(parsed, dict) else None,
            )
            return None
        return overview.strip()

    @staticmethod
    async def _run_enrichment_pipeline(
        organization_id: str,
        organization_name: str,
        *,
        organization_website: str | None = None,
    ) -> None:
        """Resolve website, fetch overview, generate prompts, and persist settings."""
        try:
            (
                website,
                website_source,
            ) = await OrgBusinessOverviewEnrichmentService._resolve_website_url(
                organization_id=organization_id,
                organization_name=organization_name,
                organization_website=organization_website,
            )
            if not website:
                logger.info(
                    "org_business_overview_skip_no_website",
                    extra={
                        "organization_id": organization_id,
                        "website_source": website_source,
                    },
                )
                await OrgBusinessOverviewEnrichmentService._persist_default_prompts(organization_id)
                return

            overview = await OrgBusinessOverviewEnrichmentService._fetch_business_overview(website)
            if not overview:
                logger.info(
                    "org_business_overview_skip_no_overview | organization_id=%s website=%s "
                    "website_source=%s (see prior business_overview_agent_* logs)",
                    organization_id,
                    website,
                    website_source,
                )
                await OrgBusinessOverviewEnrichmentService._persist_default_prompts(organization_id)
                return

            truncated = overview[:_MAX_BUSINESS_OVERVIEW_LEN]
            prompts = await OrgBusinessOverviewEnrichmentService._generate_overview_prompts(
                business_overview=truncated,
                organization_name=organization_name,
                website_url=website,
            )
            if prompts is None:
                logger.info(
                    "org_business_overview_prompt_gen_failed_using_defaults",
                    extra={"organization_id": organization_id},
                )
                prompts = DEFAULT_OVERVIEW_PROMPTS
            await OrgBusinessOverviewEnrichmentService._persist_ai_settings(
                organization_id,
                business_overview=truncated,
                overview_prompts=prompts,
            )
            logger.info(
                "org_business_overview_enriched",
                extra={
                    "organization_id": organization_id,
                    "website": website,
                    "overview_length": len(truncated),
                },
            )
        except Exception:
            logger.exception(
                "org_business_overview_enrichment_failed",
                extra={"organization_id": organization_id},
            )
            try:
                await OrgBusinessOverviewEnrichmentService._persist_default_prompts(organization_id)
            except Exception:
                logger.exception(
                    "org_business_overview_fallback_prompt_persist_failed",
                    extra={"organization_id": organization_id},
                )

    @staticmethod
    async def _persist_default_prompts(organization_id: str) -> None:
        """Store platform default overview prompts when enrichment cannot run."""
        await OrgBusinessOverviewEnrichmentService._persist_ai_settings(
            organization_id,
            business_overview=None,
            overview_prompts=dict(DEFAULT_OVERVIEW_PROMPTS),
        )

    @staticmethod
    async def _generate_overview_prompts(
        *,
        business_overview: str,
        organization_name: str,
        website_url: str,
    ) -> dict[str, str] | None:
        """Generate per-entity overview agent templates (stored prompts, not live overviews)."""
        model = shared_settings.org_memory_llm_model
        user = (
            "Generate industry-specific overview_prompts for this organization.\n\n"
            "These three strings will be stored and used later when users request AI "
            "Overview on CRM records. Do not output an overview for any real entity now.\n\n"
            f"Organization name: {organization_name.strip()}\n"
            f"Website: {website_url.strip()}\n\n"
            "Business overview (derive industry, buyers, and deal motion from this):\n"
            f"{business_overview.strip()}\n\n"
            "Instructions:\n"
            "- Infer the primary industry (e.g. technology, construction, legal services) "
            "and adapt Overview / Key Insights guidance accordingly—see industry patterns "
            "in the system message.\n"
            "- Name the organization's domain where helpful (products, services, buyers) "
            "so future overviews sound native to this business—not generic sales copy.\n"
            "- Preserve platform section headers, EXAMPLE blocks, and RULES from the "
            "structural reference in the system message.\n"
            "- Return JSON with keys contact, lead, company only.\n"
        )
        try:
            raw = await create_chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": _PROMPT_GEN_SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_completion_tokens=_PROMPT_GEN_MAX_TOKENS,
                timeout_seconds=shared_settings.isometrik.org_overview_openai_timeout_seconds,
            )
        except Exception:
            logger.exception("org_overview_prompt_gen_request_failed")
            return None

        parsed = _parse_overview_prompts_response(raw)
        if parsed is None:
            logger.warning("org_overview_prompt_gen_parse_failed")
        return parsed

    @staticmethod
    async def _persist_ai_settings(
        organization_id: str,
        *,
        business_overview: str | None,
        overview_prompts: dict[str, str],
    ) -> None:
        """Merge AI overview fields into organization settings and save."""
        pool = await get_pool()
        async with AcquireConnection(pool) as conn:
            repo = OrganizationRepository(db_connection=conn)
            org = await repo.get_organization_by_id(organization_id)
            if not org:
                return
            settings = parse_json_field(org.get("settings"))
            if not isinstance(settings, dict):
                settings = {}

            patch: dict[str, Any] = {"overview_prompts": overview_prompts}
            if (
                business_overview is not None
                and not OrgBusinessOverviewEnrichmentService._has_business_overview(settings)
            ):
                patch["business_overview"] = business_overview.strip()

            merge_ai_overview_settings_into_settings(settings, patch)
            serialized_settings = json.dumps(serialize_pydantic_models(settings))
            await repo.update_organization(organization_id, {"settings": serialized_settings})
