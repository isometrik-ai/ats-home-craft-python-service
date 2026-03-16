"""Typesense service for client search."""

import time
from collections.abc import Mapping
from typing import Any

import typesense

from apps.user_service.app.search.client_typesense_schema import (
    CLIENT_COLLECTION_SCHEMA,
)
from libs.shared_config.app_settings import SharedAppSettings, shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger("typesense_service")


class TypesenseService:
    """Thin, reusable wrapper around the Typesense Python client.

    This service is responsible for:
    - Creating the `clients` collection with the configured schema if it does not exist.
    - Providing simple helpers to upsert, delete, and search documents in that collection.
    - Generating scoped search keys locked to a single `organization_id` for multi-tenant isolation.
    """

    def __init__(self, client: typesense.Client, collection_name: str) -> None:
        """Initialize the service with an existing Typesense client and collection name.

        Args:
            client: Low-level `typesense.Client` instance configured with nodes and admin API key.
            collection_name: The logical name of the collection this service will operate on.
        """
        self._client = client
        self._collection_name = collection_name

    @classmethod
    def from_settings(
        cls,
        collection_name: str,
        settings: SharedAppSettings | None = None,
    ) -> "TypesenseService":
        """Create a `TypesenseService` instance from shared application settings.

        This factory method wires up the Typesense client using `SharedAppSettings`,
        ensures that the target collection exists (creating it with `CLIENT_COLLECTION_SCHEMA`
        if necessary), and returns a ready-to-use service instance.

        Args:
            settings: Optional settings object; defaults to `shared_settings` when omitted.
            collection_name: The name of the collection to use.
        """
        settings = settings or shared_settings
        typesense_settings = settings.typesense

        client = typesense.Client(
            {
                "nodes": [
                    {
                        "host": typesense_settings.host,
                        "port": typesense_settings.port,
                        "protocol": typesense_settings.protocol,
                    }
                ],
                "api_key": typesense_settings.admin_api_key,
                "connection_timeout_seconds": typesense_settings.connection_timeout_seconds,
                "num_retries": typesense_settings.num_retries,
                "retry_interval_seconds": typesense_settings.retry_interval_seconds,
            }
        )
        service = cls(client=client, collection_name=collection_name)
        service.ensure_collection()
        return service

    @property
    def collection(self) -> typesense.collections.Collection:
        """Return the underlying Typesense collection handle for the configured name."""
        return self._client.collections[self._collection_name]

    def ensure_collection(self) -> None:
        """Create the collection if it is missing; no-op if it already exists.

        Uses `CLIENT_COLLECTION_SCHEMA` as the base schema and overrides the `name`
        with the configured collection name. Safe to call multiple times at startup.
        """
        try:
            self.collection.retrieve()
            return
        except typesense.exceptions.ObjectNotFound:
            logger.info("typesense_collection_missing_creating", collection=self._collection_name)

        schema = dict(CLIENT_COLLECTION_SCHEMA)
        schema["name"] = self._collection_name
        self._client.collections.create(schema)
        logger.info("typesense_collection_created", collection=self._collection_name)

    def upsert_document(self, document: Mapping[str, Any]) -> None:
        """Insert or update a single document in the collection.

        Delegates to Typesense's `documents.upsert`, which performs an idempotent
        write keyed by the document's `id` field.

        Args:
            document: Mapping representing a fully-formed Typesense document.
        """
        self.collection.documents.upsert(document)
        logger.debug("typesense_upsert", client_id=document.get("id"))

    def upsert_documents_bulk(self, documents: list[Mapping[str, Any]]) -> None:
        """Insert or update multiple documents in a single bulk operation.

        Uses Typesense's `documents.import_` API with `action=upsert`, which is
        both idempotent and significantly more efficient than many single-row
        upserts for batch operations.

        Args:
            documents: List of fully-formed Typesense documents.
        """
        if not documents:
            return

        self.collection.documents.import_(
            documents,
            {"action": "upsert"},
        )
        logger.debug("typesense_bulk_upsert", count=len(documents))

    def delete_document(self, client_id: str) -> None:
        """Delete a single document from the collection by its client identifier.

        Args:
            client_id: The document `id` / client UUID to delete from the index.
        """
        self.collection.documents[client_id].delete()
        logger.debug("typesense_delete", client_id=client_id)

    def search(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Execute a search query against the collection.

        Args:
            params: Raw Typesense search parameters (query, filter_by, sort_by, etc.).

        Returns:
            The raw search response dict as returned by the Typesense client.
        """
        return self.collection.documents.search(params)

    def generate_scoped_search_key(self, organization_id: str) -> str:
        """Generate a short-lived, search-only API key locked to a single organization.

        The generated key embeds a `filter_by` clause on `organization_id` and is used
        by downstream services to query Typesense without ever exposing the admin key.

        Args:
            organization_id: The tenant identifier to lock into the scoped key.

        Returns:
            A scoped search API key string suitable for search-only operations.
        """

        return self._client.keys.generate_scoped_search_key(
            search_api_key=shared_settings.typesense.search_only_api_key,
            parameters={
                "filter_by": f"organization_id:={organization_id}",
                "expires_at": int(time.time()) + 3600,
            },
        )
