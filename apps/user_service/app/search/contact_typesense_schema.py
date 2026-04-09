"""Typesense schema + search params for the v2 contacts collection.

This is a *dedicated* collection schema (contact-native fields). It intentionally
does not reuse the shared "client" naming (e.g. primary_contact_*, client_type).
"""

from typing import Any


CONTACTS_COLLECTION_SCHEMA: dict[str, Any] = {
    "enable_nested_fields": True,
    "token_separators": ["+", "-", "@", ".", "(", ")"],
    "default_sorting_field": "updated_at",
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "organization_id", "type": "string", "facet": True},
        {"name": "status", "type": "string", "facet": True},
        # Contact identity (first/last are display-only; search uses full_name)
        {"name": "first_name", "type": "string", "index": False, "optional": True},
        {"name": "last_name", "type": "string", "index": False, "optional": True},
        {"name": "full_name", "type": "string"},
        {"name": "title", "type": "string", "optional": True},
        # Contact details
        {"name": "email", "type": "string", "optional": True},
        {"name": "phone_numbers", "type": "string[]", "optional": True},
        # Stored for list/search response parity (not used for query_by)
        {"name": "phones_display", "type": "object[]", "index": False, "optional": True},
        {"name": "tags", "type": "string[]", "facet": True, "optional": True},
        # All associated companies (multi) - aligns with list API shape (company_names[])
        {"name": "company_ids", "type": "string[]", "index": False, "optional": True},
        {"name": "company_names", "type": "string[]", "optional": True},
        # Custom fields
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


CONTACT_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": (
        "full_name,email,phone_numbers,company_names,title,tags,custom_field_values"
    ),
    "query_by_weights": "15,12,10,10,6,5,1",
    "num_typos": 2,
    "typo_tokens_threshold": 1,
    "min_len_1typo": 4,
    "min_len_2typo": 7,
    "prefix": True,
    "sort_by": "_text_match:desc,updated_at:desc",
    "facet_by": "status,tags",
    "max_facet_values": 25,
}


CONTACT_EMAIL_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "email",
    "num_typos": 0,
    "prefix": False,
}


CONTACT_PHONE_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "phone_numbers",
    "num_typos": 0,
    "prefix": False,
}

