"""Typesense schema for the v2 companies collection."""

from typing import Any


COMPANIES_COLLECTION_SCHEMA: dict[str, Any] = {
    "enable_nested_fields": True,
    "token_separators": ["+", "-", "@", ".", "(", ")"],
    "default_sorting_field": "updated_at",
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "organization_id", "type": "string", "facet": True},
        {"name": "status", "type": "string", "facet": True},
        {"name": "name", "type": "string"},
        {"name": "industry", "type": "string", "facet": True, "optional": True},
        # All linked contacts (not just primary). Stored for response parity.
        {"name": "contacts", "type": "object[]", "index": False, "optional": True},
        # Flattened contact facets for search (Typesense can't query nested objects directly).
        {"name": "contact_full_names", "type": "string[]", "optional": True},
        {"name": "contact_titles", "type": "string[]", "optional": True},
        {"name": "contact_emails", "type": "string[]", "optional": True},
        {"name": "contact_phone_numbers", "type": "string[]", "optional": True},
        {"name": "tags", "type": "string[]", "facet": True, "optional": True},
        {"name": "description", "type": "string", "optional": True},
        {"name": "target_market_segments", "type": "string[]", "optional": True},
        {"name": "current_tech_stack", "type": "string[]", "optional": True},
        {"name": "industry_specific_terminologies", "type": "string[]", "optional": True},
        {"name": "preferred_communication_channels", "type": "string[]", "facet": True, "optional": True},
        {"name": "key_people_names", "type": "string[]", "optional": True},
        {"name": "product_names", "type": "string[]", "optional": True},
        {"name": "custom_field_values", "type": "string[]", "optional": True},
        {"name": "custom_field_keys", "type": "string[]", "optional": True},
        {"name": "enrichment_done", "type": "bool", "optional": True},
        {
            "name": "embedding",
            "type": "float[]",
            "num_dim": 3072,
            "vec_index": True,
            "optional": True,
        },
        {"name": "created_at", "type": "int64", "sort": True},
        {"name": "updated_at", "type": "int64", "sort": True},
        {"name": "profile_photo_url", "type": "string", "index": False, "optional": True},
    ],
}


COMPANY_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": (
        "name,contact_full_names,contact_emails,contact_phone_numbers,contact_titles,"
        "tags,industry,description,target_market_segments,current_tech_stack,"
        "key_people_names,product_names,custom_field_values"
    ),
    "query_by_weights": "15,12,12,10,6,5,5,4,2,2,2,2,1",
    "num_typos": 2,
    "typo_tokens_threshold": 1,
    "min_len_1typo": 4,
    "min_len_2typo": 7,
    "prefix": True,
    "sort_by": "_text_match:desc,updated_at:desc",
    "facet_by": "status,tags,industry,preferred_communication_channels",
    "max_facet_values": 25,
}


COMPANY_EMAIL_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "contact_emails",
    "num_typos": 0,
    "prefix": False,
}


COMPANY_PHONE_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "contact_phone_numbers",
    "num_typos": 0,
    "prefix": False,
}
