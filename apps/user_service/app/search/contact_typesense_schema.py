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
        # Person profile enrichment / resume-like fields
        {"name": "skills", "type": "string[]", "optional": True},
        {"name": "work_history_companies", "type": "string[]", "optional": True},
        {"name": "work_history_titles", "type": "string[]", "optional": True},
        {"name": "educational_institutions", "type": "string[]", "optional": True},
        {"name": "educational_degrees", "type": "string[]", "optional": True},
        {"name": "social_urls", "type": "string[]", "optional": True},
        {"name": "websites", "type": "string[]", "optional": True},
        # Address facets (derived from addresses[])
        {"name": "address_cities", "type": "string[]", "facet": True, "optional": True},
        {"name": "address_states", "type": "string[]", "facet": True, "optional": True},
        {"name": "address_countries", "type": "string[]", "facet": True, "optional": True},
        {"name": "address_postal_codes", "type": "string[]", "facet": True, "optional": True},
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
        "full_name,email,phone_numbers,company_names,title,tags,"
        "skills,work_history_companies,work_history_titles,"
        "educational_institutions,educational_degrees,"
        "websites,social_urls,"
        "address_cities,address_states,address_countries,address_postal_codes,"
        "custom_field_values"
    ),
    # Must match the number of comma-separated fields in `query_by`.
    "query_by_weights": "15,12,10,10,6,5,4,3,2,2,1,2,1,1,1,1,1,1",
    "num_typos": 2,
    "typo_tokens_threshold": 1,
    "min_len_1typo": 4,
    "min_len_2typo": 7,
    "prefix": True,
    "sort_by": "_text_match:desc,updated_at:desc",
    "facet_by": "status,tags,address_countries,address_states,address_cities",
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
