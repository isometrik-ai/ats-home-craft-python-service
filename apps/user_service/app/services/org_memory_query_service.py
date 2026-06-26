"""Natural-language CRM Q&A scoped to one org via Graphiti snapshot + OpenAI."""

from __future__ import annotations

import asyncpg

from apps.user_service.app.constants.ai_overview_defaults import (
    DEFAULT_OVERVIEW_PROMPTS,
    EntityOverviewType,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.schemas.ai_overview_settings import AiOverviewSettings
from apps.user_service.app.services.organization_service import OrganizationService
from apps.user_service.app.utils.common_utils import parse_json_field
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.graphiti_crm_models import CrmEntityType
from libs.shared_utils.graphiti_service import (
    GraphitiCrmService,
    container_tag_for_organization,
    snapshot_to_synthesis_text,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.openai_chat_service import create_chat_completion

logger = get_logger("org_memory_query_service")

_SYNTH_MAX_TOKENS = 4096
_SYNTH_CONTEXT_CHAR_LIMIT = 14_000
_NO_DATA_BY_ENTITY_TYPE: dict[str, str] = {
    "contact": "We don't have data for this contact.",
    "company": "We don't have data for this company.",
    "lead": "We don't have data for this lead.",
}
_DEFAULT_NO_DATA_MESSAGE = "We don't have data for this record."
_ENTITY_NAME_PLACEHOLDER = "{{entity_name}}"


def _resolve_overview_entity_type(entity_type: str | None) -> EntityOverviewType:
    """Map request entity_type to the overview prompt key (defaults to contact)."""
    normalized = (entity_type or "").strip().lower()
    if normalized in ("lead", "company", "contact"):
        return normalized  # type: ignore[return-value]
    return "contact"


def _overview_prompt_template(
    overview_settings: AiOverviewSettings,
    entity_type: str | None,
) -> str:
    """Return stored prompt for ``entity_type``, or the platform default when missing."""
    key = _resolve_overview_entity_type(entity_type)
    stored = getattr(overview_settings.overview_prompts, key, "")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return DEFAULT_OVERVIEW_PROMPTS[key]


def _build_synth_system_prompt(
    *,
    entity_type: str | None,
    entity_name: str,
    overview_settings: AiOverviewSettings,
) -> str:
    """Org-specific AI Overview agent prompt; falls back to platform defaults."""
    template = _overview_prompt_template(overview_settings, entity_type)
    return template.replace(_ENTITY_NAME_PLACEHOLDER, entity_name)


def _build_synth_user_message(
    *,
    user_message: str,
    notes: str,
    entity_id: str | None,
    entity_type: str | None,
    business_overview: str | None,
) -> str:
    """User turn: org background, scope, question, and retrieved CRM data."""
    parts: list[str] = []
    if business_overview and business_overview.strip():
        parts.append(
            "Organization background (context only — do not invent facts beyond CRM data):\n"
            f"{business_overview.strip()}"
        )
    if entity_id and entity_type:
        parts.append(f"Answer only about this CRM {entity_type} (id {entity_id}).")
    parts.append(f"Question: {user_message}")
    parts.append(
        "Use only the CRM data below. Follow the system instructions for "
        "sections and format.\n\n"
        f"CRM data:\n{notes}"
    )
    return "\n\n".join(parts)


async def _load_effective_ai_overview_settings(
    db_connection: asyncpg.Connection,
    organization_id: str,
) -> AiOverviewSettings:
    """Load org AI overview settings; per-entity prompts fall back to platform defaults."""
    repo = OrganizationRepository(db_connection=db_connection)
    org = await repo.get_organization_by_id(organization_id)
    if not org:
        return OrganizationService.default_ai_overview_settings()
    settings = parse_json_field(org.get("settings"))
    return OrganizationService._resolve_effective_ai_overview_settings(settings)


def _no_data_message(entity_type: str | None) -> str:
    """Return a stable user-facing message when no CRM data exists for the scope."""
    normalized = (entity_type or "").strip().lower()
    return _NO_DATA_BY_ENTITY_TYPE.get(normalized, _DEFAULT_NO_DATA_MESSAGE)


def _normalize_crm_type(entity_type: str | None) -> CrmEntityType | None:
    """Map API entity type strings to canonical CRM entity types."""
    normalized = (entity_type or "").strip().lower()
    if normalized in ("contact", "company", "lead"):
        return normalized  # type: ignore[return-value]
    return None


def _snapshot_section_sort_key(heading: str) -> int:
    """Order CRM sections for synthesis: notes -> leads/companies -> emails -> rest."""
    heading_lower = heading.casefold()
    if heading_lower.startswith("notes"):
        return 0
    if (
        heading_lower.startswith("linked lead")
        or heading_lower.startswith("companies")
        or heading_lower.startswith("crm company")
        or heading_lower.startswith("work history")
    ):
        return 1
    if heading_lower.startswith("email"):
        return 2
    return 3


def _prioritize_intel_sections_in_snapshot(text: str) -> str:
    """Reorder CRM markdown so notes and sales sections precede profile/skills."""
    stripped = text.strip()
    if not stripped:
        return text
    lines = stripped.split("\n")
    section_starts = [idx for idx, line in enumerate(lines) if line.startswith("## ")]
    if not section_starts:
        return text

    preamble = "\n".join(lines[: section_starts[0]]).strip()
    blocks: list[tuple[int, str]] = []
    for idx, start in enumerate(section_starts):
        end = section_starts[idx + 1] if idx + 1 < len(section_starts) else len(lines)
        block = "\n".join(lines[start:end])
        heading = lines[start][3:].strip()
        blocks.append((_snapshot_section_sort_key(heading), block))
    if all(priority == 3 for priority, _ in blocks):
        return text

    blocks.sort(key=lambda item: item[0])
    parts = [preamble] if preamble else []
    parts.extend(block for _, block in blocks)
    return "\n\n".join(parts)


class OrgMemoryQueryService:
    """Entity-scoped Graphiti snapshot JSON → markdown AI Overview."""

    def __init__(self, *, graphiti: GraphitiCrmService | None = None) -> None:
        self._graphiti = graphiti or GraphitiCrmService()

    async def _load_overview_settings(
        self,
        db_connection: asyncpg.Connection | None,
        organization_id: str,
    ) -> AiOverviewSettings:
        """Load org-specific AI overview settings, or defaults when no DB connection."""
        if db_connection is not None:
            return await _load_effective_ai_overview_settings(db_connection, organization_id)
        return OrganizationService.default_ai_overview_settings()

    async def _load_entity_overview_context(
        self,
        *,
        organization_id: str,
        entity_id: str,
        entity_type: str | None,
        user_message: str,
    ) -> tuple[str, str, str]:
        """Load snapshot markdown, supplement markdown, and display name for one entity."""
        group_id = container_tag_for_organization(organization_id)
        crm_type = _normalize_crm_type(entity_type)
        if not crm_type:
            return "", "", user_message

        graph_context = await self._graphiti.get_entity_graph_context(
            group_id=group_id,
            crm_type=crm_type,
            crm_id=entity_id.strip(),
        )

        snapshot_text = ""
        entity_name = user_message
        snapshot = graph_context.snapshot
        if snapshot is not None and str(snapshot.metadata.status).lower() != "deleted":
            snapshot_text = snapshot_to_synthesis_text(snapshot)
            if snapshot.display_name:
                entity_name = snapshot.display_name

        supplement_text = graph_context.supplement_markdown()
        return snapshot_text, supplement_text, entity_name

    @staticmethod
    def _build_notes_context(
        snapshot_text: str,
        supplement_text: str = "",
    ) -> tuple[str, bool]:
        """Assemble CRM context from the entity snapshot plus entity-scoped graph supplements."""
        parts: list[str] = []
        if snapshot_text.strip():
            parts.append(_prioritize_intel_sections_in_snapshot(snapshot_text))
        if supplement_text.strip():
            parts.append(supplement_text.strip())

        if not parts:
            return "", False

        notes = "\n\n---\n\n".join(parts)
        if len(notes) > _SYNTH_CONTEXT_CHAR_LIMIT:
            return notes[:_SYNTH_CONTEXT_CHAR_LIMIT], True
        return notes, False

    async def run(
        self,
        *,
        user_message: str,
        organization_id: str,
        entity_id: str | None = None,
        entity_type: str | None = None,
        db_connection: asyncpg.Connection | None = None,
    ) -> str:
        """Return a sales intelligence markdown briefing for the queried entity."""
        user_message = user_message.strip()
        model = shared_settings.org_memory_llm_model
        overview_settings = await self._load_overview_settings(db_connection, organization_id)
        prompt_entity_type = _resolve_overview_entity_type(entity_type)
        crm_type = _normalize_crm_type(entity_type)

        if not (entity_id and entity_id.strip() and crm_type):
            logger.info(
                "org_memory_query_no_scope organization_id=%s entity_id=%s entity_type=%s",
                organization_id,
                entity_id,
                entity_type,
            )
            return _no_data_message(entity_type)

        snapshot_text, supplement_text, entity_name = await self._load_entity_overview_context(
            organization_id=organization_id,
            entity_id=entity_id.strip(),
            entity_type=entity_type,
            user_message=user_message,
        )

        if not snapshot_text.strip() and not supplement_text.strip():
            logger.info(
                "org_memory_query_no_data organization_id=%s entity_id=%s entity_type=%s",
                organization_id,
                entity_id.strip(),
                crm_type,
            )
            return _no_data_message(entity_type)

        notes, notes_truncated = self._build_notes_context(snapshot_text, supplement_text)

        synth_system = _build_synth_system_prompt(
            entity_type=entity_type,
            entity_name=entity_name,
            overview_settings=overview_settings,
        )
        synth_user = _build_synth_user_message(
            user_message=user_message,
            notes=notes,
            entity_id=entity_id,
            entity_type=entity_type,
            business_overview=overview_settings.business_overview,
        )

        answer = (
            await create_chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": synth_system},
                    {"role": "user", "content": synth_user},
                ],
                max_completion_tokens=_SYNTH_MAX_TOKENS,
            )
        ).strip()

        used_fallback = not bool(answer)
        if used_fallback:
            answer = _no_data_message(entity_type)

        logger.info(
            "org_memory_query organization_id=%s entity_id=%s prompt_entity_type=%s "
            "notes_len=%s notes_truncated=%s used_fallback=%s answer_len=%s",
            organization_id,
            entity_id,
            prompt_entity_type,
            len(notes),
            notes_truncated,
            used_fallback,
            len(answer),
        )

        return answer
