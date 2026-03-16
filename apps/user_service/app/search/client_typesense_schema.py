"""Client Typesense schema."""

from typing import Any

CLIENT_COLLECTION_SCHEMA: dict[str, Any] = {
    "name": "clients",
    "enable_nested_fields": True,
    "token_separators": ["+", "-", "@", ".", "(", ")"],
    "default_sorting_field": "updated_at",
    "fields": [
        # ── Identity / Tenancy ─────────────────────────────────────────────
        {"name": "id", "type": "string"},
        {"name": "organization_id", "type": "string", "facet": True},
        {"name": "client_type", "type": "string", "facet": True},
        {"name": "status", "type": "string", "facet": True},
        # ── Core Name Fields ───────────────────────────────────────────────
        {"name": "name", "type": "string"},
        {"name": "company_name", "type": "string", "optional": True},
        # ── Primary Contact ────────────────────────────────────────────────
        {
            "name": "primary_contact_first_name",
            "type": "string",
            "optional": True,
        },
        {
            "name": "primary_contact_last_name",
            "type": "string",
            "optional": True,
        },
        {
            "name": "primary_contact_full_name",
            "type": "string",
            "optional": True,
        },
        {
            "name": "primary_contact_title",
            "type": "string",
            "optional": True,
        },
        {"name": "email", "type": "string", "optional": True},
        # ── Contact Details ────────────────────────────────────────────────
        {"name": "phone_numbers", "type": "string[]", "optional": True},
        # ── Categorical / Facets ───────────────────────────────────────────
        {"name": "tags", "type": "string[]", "facet": True, "optional": True},
        {"name": "industry", "type": "string", "facet": True, "optional": True},
        # ── Company-Specific Text ──────────────────────────────────────────
        {"name": "description", "type": "string", "optional": True},
        {
            "name": "target_market_segments",
            "type": "string[]",
            "optional": True,
        },
        {
            "name": "current_tech_stack",
            "type": "string[]",
            "optional": True,
        },
        {
            "name": "industry_specific_terminologies",
            "type": "string[]",
            "optional": True,
        },
        {
            "name": "preferred_communication_channels",
            "type": "string[]",
            "facet": True,
            "optional": True,
        },
        {"name": "key_people_names", "type": "string[]", "optional": True},
        {"name": "product_names", "type": "string[]", "optional": True},
        # ── Person-Specific Text ───────────────────────────────────────────
        {"name": "skills", "type": "string[]", "optional": True},
        {
            "name": "work_history_companies",
            "type": "string[]",
            "optional": True,
        },
        {
            "name": "work_history_titles",
            "type": "string[]",
            "optional": True,
        },
        {
            "name": "educational_institutions",
            "type": "string[]",
            "optional": True,
        },
        # ── Social / Web ───────────────────────────────────────────────────
        {"name": "social_page_urls", "type": "string[]", "optional": True},
        {"name": "website_urls", "type": "string[]", "optional": True},
        # ── Address ────────────────────────────────────────────────────────
        {"name": "address_cities", "type": "string[]", "optional": True},
        {"name": "address_states", "type": "string[]", "optional": True},
        {
            "name": "address_countries",
            "type": "string[]",
            "facet": True,
            "optional": True,
        },
        {
            "name": "address_postal_codes",
            "type": "string[]",
            "optional": True,
        },
        # ── Lead / Pipeline ────────────────────────────────────────────────
        {"name": "lead_status", "type": "string", "facet": True, "optional": True},
        {
            "name": "lead_score",
            "type": "int32",
            "sort": True,
            "optional": True,
        },
        {
            "name": "intake_stage",
            "type": "string",
            "facet": True,
            "optional": True,
        },
        # ── Custom Fields ──────────────────────────────────────────────────
        {"name": "custom_field_values", "type": "string[]", "optional": True},
        {"name": "custom_field_keys", "type": "string[]", "optional": True},
        # ── Enrichment ─────────────────────────────────────────────────────
        {"name": "enrichment_done", "type": "bool", "optional": True},
        # ── Sort Keys ──────────────────────────────────────────────────────
        {"name": "created_at", "type": "int64", "sort": True},
        {"name": "updated_at", "type": "int64", "sort": True},
        # ── Display Only (not indexed) ─────────────────────────────────────
        {
            "name": "image_url",
            "type": "string",
            "index": False,
            "optional": True,
        },
        {
            "name": "company_id",
            "type": "string",
            "index": False,
            "optional": True,
        },
        {
            "name": "profile_photo_url",
            "type": "string",
            "index": False,
            "optional": True,
        },
    ],
}


SEARCH_PARAMS: dict[str, Any] = {
    "query_by": (
        "name,primary_contact_full_name,email,phone_numbers,company_name,"
        "primary_contact_title,tags,industry,description,address_cities,"
        "work_history_companies,work_history_titles,skills,"
        "target_market_segments,current_tech_stack,key_people_names,"
        "product_names,social_page_urls,custom_field_values"
    ),
    "query_by_weights": ("15,12,12,10,10,6,5,5,4,3,3,3,3,2,2,2,2,1,1"),
    "num_typos": 2,
    "typo_tokens_threshold": 1,
    "min_len_1typo": 4,
    "min_len_2typo": 7,
    "prefix": True,
    "sort_by": "_text_match:desc,updated_at:desc",
    "facet_by": "client_type,status,tags,industry,lead_status,intake_stage,address_countries",
    "max_facet_values": 25,
}


EMAIL_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "email",
    "num_typos": 0,
    "prefix": False,
}


PHONE_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "phone_numbers",
    "num_typos": 0,
    "prefix": False,
}
