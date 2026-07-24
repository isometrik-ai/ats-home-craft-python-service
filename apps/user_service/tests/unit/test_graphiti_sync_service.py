"""Unit tests for GraphitiSyncService with mocks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.schemas.enums import ContactEventType
from apps.user_service.app.services.graphiti_sync_service import GraphitiSyncService


@pytest.fixture
def graphiti_mock() -> MagicMock:
    """Graphiti client stub with configured flag."""
    client = MagicMock()
    client.is_configured = True
    client.sync_snapshot = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_process_crm_event_missing_org(graphiti_mock: MagicMock) -> None:
    """Missing organization_id is a no-op."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    await service.process_crm_event(conn, {"event_type": "x", "aggregate_id": "a1"})

    graphiti_mock.sync_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_process_crm_event_memory_disabled(graphiti_mock: MagicMock) -> None:
    """Disabled organization_memory skips sync."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
        new=AsyncMock(return_value=False),
    ):
        await service.process_crm_event(
            conn,
            {"organization_id": "org-1", "event_type": ContactEventType.UPDATED.value},
        )

    graphiti_mock.sync_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_process_crm_event_no_targets(graphiti_mock: MagicMock) -> None:
    """Unknown event types produce no sync targets."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.user_service.app.services.graphiti_sync_service.resolve_sync_targets",
            return_value=[],
        ),
    ):
        await service.process_crm_event(
            conn,
            {
                "organization_id": "org-1",
                "event_type": "unknown.event",
                "aggregate_id": "x",
            },
        )

    graphiti_mock.sync_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_sync_entity_calls_graphiti(graphiti_mock: MagicMock) -> None:
    """sync_entity loads snapshot and upserts to Graphiti."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    snapshot = MagicMock()

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch.object(service, "_load_snapshot", new=AsyncMock(return_value=snapshot)),
        patch(
            "apps.user_service.app.services.graphiti_sync_service.container_tag_for_organization",
            return_value="org_tag",
        ),
    ):
        await service.sync_entity(
            conn,
            organization_id="org-1",
            entity_type="contact",
            entity_id="c1",
        )

    graphiti_mock.sync_snapshot.assert_awaited_once_with(group_id="org_tag", snapshot=snapshot)


@pytest.mark.asyncio
async def test_sync_entity_skips_missing_row(graphiti_mock: MagicMock) -> None:
    """Missing entity snapshot skips Graphiti call."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch.object(service, "_load_snapshot", new=AsyncMock(return_value=None)),
    ):
        await service.sync_entity(
            conn,
            organization_id="org-1",
            entity_type="lead",
            entity_id="l1",
        )

    graphiti_mock.sync_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_sync_entity_not_configured(graphiti_mock: MagicMock) -> None:
    """Unconfigured Graphiti client skips sync."""
    graphiti_mock.is_configured = False
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
        new=AsyncMock(return_value=True),
    ):
        await service.sync_entity(
            conn,
            organization_id="org-1",
            entity_type="company",
            entity_id="co1",
        )

    graphiti_mock.sync_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_contact_associations(graphiti_mock: MagicMock) -> None:
    """Contact cascade re-syncs linked companies and leads."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.ContactsRepository"
    ) as repo_cls:
        repo_cls.return_value.get_contact_details = AsyncMock(
            return_value={
                "companies": [{"company_id": "co1", "name": "Acme"}],
                "leads": [{"id": "l1"}],
            }
        )
        await service._cascade_contact_associations(  # pylint: disable=protected-access
            conn,
            organization_id="org-1",
            contact_id="c1",
        )

    assert sync_entity.await_count == 2
    sync_entity.assert_any_await(
        conn,
        organization_id="org-1",
        entity_type="company",
        entity_id="co1",
    )


@pytest.mark.asyncio
async def test_sync_association_targets_from_payload(graphiti_mock: MagicMock) -> None:
    """Payload side-effect lists sync affected companies/contacts."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    await service._sync_association_targets_from_payload(  # pylint: disable=protected-access
        conn,
        organization_id="org-1",
        payload={
            "affected_company_ids": ["co1", "co1"],
            "affected_contact_ids": ["c1"],
        },
    )

    assert sync_entity.await_count == 2


@pytest.mark.asyncio
async def test_process_crm_event_syncs_contact_with_cascade(graphiti_mock: MagicMock) -> None:
    """Contact events sync entity and cascade associations."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    cascade = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]
    service._cascade_contact_associations = cascade  # type: ignore[method-assign]

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.user_service.app.services.graphiti_sync_service.resolve_sync_targets",
            return_value=[("contact", "c1")],
        ),
    ):
        await service.process_crm_event(
            conn,
            {
                "organization_id": "org-1",
                "event_type": ContactEventType.UPDATED.value,
                "aggregate_id": "c1",
                "event_id": "e1",
                "payload": {"affected_company_ids": ["co1"]},
            },
        )

    sync_entity.assert_awaited()
    cascade.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_crm_event_company_and_lead_cascade(graphiti_mock: MagicMock) -> None:
    """Company and lead events invoke their cascade helpers."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    service.sync_entity = AsyncMock()  # type: ignore[method-assign]
    cascade_company = AsyncMock()
    cascade_lead = AsyncMock()
    service._cascade_company_associations = cascade_company  # type: ignore[method-assign]
    service._cascade_lead_associations = cascade_lead  # type: ignore[method-assign]

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.user_service.app.services.graphiti_sync_service.resolve_sync_targets",
            side_effect=[
                [("company", "co1")],
                [("lead", "l1")],
            ],
        ),
    ):
        await service.process_crm_event(
            conn,
            {
                "organization_id": "org-1",
                "event_type": "companies.updated",
                "aggregate_id": "co1",
            },
        )
        await service.process_crm_event(
            conn,
            {
                "organization_id": "org-1",
                "event_type": "leads.updated",
                "aggregate_id": "l1",
            },
        )

    cascade_company.assert_awaited_once()
    cascade_lead.assert_awaited_once()


@pytest.mark.asyncio
async def test_cascade_contact_no_details(graphiti_mock: MagicMock) -> None:
    """Contact cascade no-ops when contact row is missing."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    service.sync_entity = AsyncMock()  # type: ignore[method-assign]

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.ContactsRepository"
    ) as repo_cls:
        repo_cls.return_value.get_contact_details = AsyncMock(return_value=None)
        await service._cascade_contact_associations(  # pylint: disable=protected-access
            conn,
            organization_id="org-1",
            contact_id="c1",
        )

    service.sync_entity.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_company_associations(graphiti_mock: MagicMock) -> None:
    """Company cascade re-syncs linked contacts."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.CompaniesRepository"
    ) as repo_cls:
        repo_cls.return_value.get_company_details = AsyncMock(
            return_value={"contacts": [{"id": "c1"}, {"id": "c2"}]}
        )
        await service._cascade_company_associations(  # pylint: disable=protected-access
            conn,
            organization_id="org-1",
            company_id="co1",
        )

    assert sync_entity.await_count == 2


@pytest.mark.asyncio
async def test_cascade_lead_associations(graphiti_mock: MagicMock) -> None:
    """Lead cascade re-syncs linked companies and contacts."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    with patch("apps.user_service.app.services.graphiti_sync_service.LeadRepository") as repo_cls:
        repo_cls.return_value.get_lead_detail_with_contacts_by_id = AsyncMock(
            return_value={
                "companies": [{"company_id": "co1"}],
                "contacts": [{"contact_id": "c1"}],
            }
        )
        await service._cascade_lead_associations(  # pylint: disable=protected-access
            conn,
            organization_id="org-1",
            lead_id="l1",
        )

    assert sync_entity.await_count == 2


@pytest.mark.asyncio
async def test_sync_entity_raises_on_graphiti_failure(graphiti_mock: MagicMock) -> None:
    """Graphiti sync errors propagate after logging."""
    graphiti_mock.sync_snapshot = AsyncMock(side_effect=RuntimeError("graphiti down"))
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch.object(service, "_load_snapshot", new=AsyncMock(return_value=MagicMock())),
        patch(
            "apps.user_service.app.services.graphiti_sync_service.container_tag_for_organization",
            return_value="org_tag",
        ),
    ):
        with pytest.raises(RuntimeError, match="graphiti down"):
            await service.sync_entity(
                conn,
                organization_id="org-1",
                entity_type="contact",
                entity_id="c1",
            )


@pytest.mark.asyncio
async def test_sync_contact_with_associations(graphiti_mock: MagicMock) -> None:
    """sync_contact_with_associations returns snapshot and linked ids."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    snapshot = MagicMock()
    snapshot.linked_companies = [MagicMock(company_id="co1")]
    snapshot.linked_leads = [MagicMock(lead_id="l1")]

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.build_contact_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch.object(service, "sync_entity", new=AsyncMock()),
        patch.object(service, "_cascade_contact_associations", new=AsyncMock()),
    ):
        result = await service.sync_contact_with_associations(
            conn,
            organization_id="org-1",
            contact_id="c1",
        )

    assert result["contact_id"] == "c1"
    assert result["company_ids"] == ["co1"]
    assert result["lead_ids"] == ["l1"]
    assert result["snapshot"] is snapshot


@pytest.mark.asyncio
async def test_sync_contact_with_associations_not_found(graphiti_mock: MagicMock) -> None:
    """Missing contact raises LookupError."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.build_contact_snapshot",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(LookupError, match="Contact not found"):
            await service.sync_contact_with_associations(
                conn,
                organization_id="org-1",
                contact_id="missing",
            )


@pytest.mark.asyncio
async def test_load_snapshot_by_entity_type(graphiti_mock: MagicMock) -> None:
    """_load_snapshot delegates to the correct builder."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    contact_snap = MagicMock()
    company_snap = MagicMock()
    lead_snap = MagicMock()

    with (
        patch(
            "apps.user_service.app.services.graphiti_sync_service.build_contact_snapshot",
            new=AsyncMock(return_value=contact_snap),
        ) as build_contact,
        patch(
            "apps.user_service.app.services.graphiti_sync_service.build_company_snapshot",
            new=AsyncMock(return_value=company_snap),
        ) as build_company,
        patch(
            "apps.user_service.app.services.graphiti_sync_service.build_lead_snapshot",
            new=AsyncMock(return_value=lead_snap),
        ) as build_lead,
    ):
        assert (
            await service._load_snapshot(  # pylint: disable=protected-access
                conn, organization_id="org-1", entity_type="contact", entity_id="c1"
            )
            is contact_snap
        )
        assert (
            await service._load_snapshot(  # pylint: disable=protected-access
                conn, organization_id="org-1", entity_type="company", entity_id="co1"
            )
            is company_snap
        )
        assert (
            await service._load_snapshot(  # pylint: disable=protected-access
                conn, organization_id="org-1", entity_type="lead", entity_id="l1"
            )
            is lead_snap
        )

    build_contact.assert_awaited_once()
    build_company.assert_awaited_once()
    build_lead.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_contact_snapshot(graphiti_mock: MagicMock) -> None:
    """load_contact_snapshot wraps build_contact_snapshot."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    snap = MagicMock()

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.build_contact_snapshot",
        new=AsyncMock(return_value=snap),
    ):
        result = await service.load_contact_snapshot(
            conn,
            organization_id="org-1",
            contact_id="c1",
        )

    assert result is snap


@pytest.mark.asyncio
async def test_sync_association_skips_blank_ids(graphiti_mock: MagicMock) -> None:
    """Blank affected ids are ignored."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    await service._sync_association_targets_from_payload(  # pylint: disable=protected-access
        conn,
        organization_id="org-1",
        payload={"affected_company_ids": ["", "co1"], "affected_contact_ids": [""]},
    )

    sync_entity.assert_awaited_once()


@pytest.mark.asyncio
async def test_cascade_company_invalid_contacts(graphiti_mock: MagicMock) -> None:
    """Company cascade skips non-dict contacts and missing ids."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.CompaniesRepository"
    ) as repo_cls:
        repo_cls.return_value.get_company_details = AsyncMock(
            return_value={"contacts": ["bad", {"id": None}, {"id": "c1"}]}
        )
        await service._cascade_company_associations(  # pylint: disable=protected-access
            conn,
            organization_id="org-1",
            company_id="co1",
        )

    sync_entity.assert_awaited_once()


@pytest.mark.asyncio
async def test_cascade_lead_invalid_entries(graphiti_mock: MagicMock) -> None:
    """Lead cascade skips malformed company/contact rows."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()
    sync_entity = AsyncMock()
    service.sync_entity = sync_entity  # type: ignore[method-assign]

    with patch("apps.user_service.app.services.graphiti_sync_service.LeadRepository") as repo_cls:
        repo_cls.return_value.get_lead_detail_with_contacts_by_id = AsyncMock(
            return_value={
                "companies": ["bad", {"id": "co-fallback"}],
                "contacts": [None, {"contact_id": "c1"}],
            }
        )
        await service._cascade_lead_associations(  # pylint: disable=protected-access
            conn,
            organization_id="org-1",
            lead_id="l1",
        )

    assert sync_entity.await_count == 2


@pytest.mark.asyncio
async def test_sync_entity_memory_disabled_mid_sync(graphiti_mock: MagicMock) -> None:
    """sync_entity returns early when org memory disabled."""
    service = GraphitiSyncService(graphiti=graphiti_mock)
    conn = MagicMock()

    with patch(
        "apps.user_service.app.services.graphiti_sync_service.is_organization_memory_enabled",
        new=AsyncMock(return_value=False),
    ):
        await service.sync_entity(
            conn,
            organization_id="org-1",
            entity_type="contact",
            entity_id="c1",
        )

    graphiti_mock.sync_snapshot.assert_not_called()
