"""Unit tests for ProjectsService helpers."""

import pytest

from apps.user_service.app.services.projects_service import ProjectsService


class _FakeProjectsRepo:
    """Minimal repo stub for project code resolution."""

    def __init__(self, *, existing_codes: set[str] | None = None):
        self.existing_codes = existing_codes or set()

    async def project_code_exists(self, *, organization_id: str, code: str) -> bool:
        """Return whether the code is already taken in the org."""
        del organization_id
        return code in self.existing_codes


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Green Valley Phase 1", "green-valley-phase-1"),
        ("  Sunrise Towers!!!  ", "sunrise-towers"),
        ("---", ""),
        ("Project #42", "project-42"),
    ],
)
def test_slugify_project_name(name: str, expected: str):
    """Slugify normalizes names into URL-friendly project codes."""
    assert ProjectsService._slugify_project_name(name) == expected


@pytest.mark.asyncio
async def test_resolve_code_explicit():
    """Provided code is returned unchanged."""
    service = ProjectsService.__new__(ProjectsService)
    service.projects_repo = _FakeProjectsRepo(existing_codes={"custom-code"})

    resolved = await service._resolve_project_code(
        organization_id="org-1",
        name="Ignored Name",
        code="custom-code",
    )

    assert resolved == "custom-code"


@pytest.mark.asyncio
async def test_resolve_code_from_name():
    """Missing code is generated from the project name."""
    service = ProjectsService.__new__(ProjectsService)
    service.projects_repo = _FakeProjectsRepo()

    resolved = await service._resolve_project_code(
        organization_id="org-1",
        name="Sunrise Towers",
        code=None,
    )

    assert resolved == "sunrise-towers"


@pytest.mark.asyncio
async def test_resolve_code_suffix_on_conflict():
    """Generated code gets a numeric suffix when the base slug already exists."""
    service = ProjectsService.__new__(ProjectsService)
    service.projects_repo = _FakeProjectsRepo(existing_codes={"sunrise-towers"})

    resolved = await service._resolve_project_code(
        organization_id="org-1",
        name="Sunrise Towers",
        code=None,
    )

    assert resolved == "sunrise-towers-2"
