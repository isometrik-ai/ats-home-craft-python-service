"""Client Typesense collection schema, search parameters, and collection name."""

from typing import Any

CLIENTS_COLLECTION_NAME = "isometrik-clients"


def _schema_field_names(schema: dict[str, Any]) -> list[str]:
    """Return a list of field names from the schema."""
    fields = schema.get("fields") or []
    return [f["name"] for f in fields if isinstance(f, dict) and f.get("name")]


def build_document_from_schema(
    *,
    schema: dict[str, Any],
    raw_document: dict[str, Any],
) -> dict[str, Any]:
    """Return a document containing exactly the schema's field names.

    This is intentionally a light-touch normalizer (no casting/validation) so we can
    keep existing document-building logic unchanged while preventing accidental
    schema drift (extra fields, typos) from being indexed.
    """
    allowed = set(_schema_field_names(schema))
    return {k: v for k, v in raw_document.items() if k in allowed}


# ---------------------------------------------------------------------------
# Collection Schema
#
# Design principles:
#   • Every indexed field must earn its place: it must improve search recall
#     or enable a filter/facet that the product actually uses.
#   • URLs are never indexed — they produce noisy tokens, inflate index size,
#     carry PII, and add zero search value. Serve them from PostgreSQL.
#   • first_name / last_name are stored as index:False display fields.
#     All name searching goes through primary_contact_full_name.
#   • Faceted fields use lowercase values to avoid duplicate facet buckets.
#   • sort:True is only set on fields used for ORDER BY (int64 / int32).
# ---------------------------------------------------------------------------

CLIENT_COLLECTION_SCHEMA: dict[str, Any] = {
    "name": "clients",
    "enable_nested_fields": True,
    "token_separators": ["+", "-", "@", ".", "(", ")"],
    "default_sorting_field": "updated_at",
    "fields": [
        # ── Identity / Tenancy ─────────────────────────────────────────────
        # organization_id is facet:True solely to support filter expressions;
        # it is never exposed as a UI facet.
        {"name": "id", "type": "string"},
        {"name": "organization_id", "type": "string", "facet": True},
        {"name": "client_type", "type": "string", "facet": True},
        {"name": "status", "type": "string", "facet": True},
        # ── Core Name Fields ───────────────────────────────────────────────
        # name: company name for company clients; full display name for person clients.
        # company_name: employer name on person documents (enables "find person by employer").
        {"name": "name", "type": "string"},
        {"name": "company_name", "type": "string", "optional": True},
        # ── Primary Contact ────────────────────────────────────────────────
        # first/last are display-only (index:False): rendering only, never searched.
        # primary_contact_full_name is the single authoritative search field for names.
        {"name": "primary_contact_first_name", "type": "string", "index": False, "optional": True},
        {"name": "primary_contact_last_name", "type": "string", "index": False, "optional": True},
        {"name": "primary_contact_full_name", "type": "string", "optional": True},
        {"name": "primary_contact_title", "type": "string", "optional": True},
        {"name": "email", "type": "string", "optional": True},
        # ── Contact Details ────────────────────────────────────────────────
        # phone_numbers: flattened E.164 strings (e.g. "+919876543210").
        # token_separators above ensure "+", "-", "(", ")" split correctly.
        {"name": "phone_numbers", "type": "string[]", "optional": True},
        # ── Categorical / Facets ───────────────────────────────────────────
        {"name": "tags", "type": "string[]", "facet": True, "optional": True},
        {"name": "industry", "type": "string", "facet": True, "optional": True},
        # ── Company-Specific Text ──────────────────────────────────────────
        {"name": "description", "type": "string", "optional": True},
        {"name": "target_market_segments", "type": "string[]", "optional": True},
        {"name": "current_tech_stack", "type": "string[]", "optional": True},
        # industry_specific_terminologies: indexed but outside primary query_by.
        # Promote to query_by at weight 1 if domain-jargon recall proves insufficient.
        {"name": "industry_specific_terminologies", "type": "string[]", "optional": True},
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
        {"name": "work_history_companies", "type": "string[]", "optional": True},
        {"name": "work_history_titles", "type": "string[]", "optional": True},
        # educational_institutions: indexed, excluded from primary query_by.
        # Add to query_by at weight 1 if education-based search is required.
        {"name": "educational_institutions", "type": "string[]", "optional": True},
        # ── Address ────────────────────────────────────────────────────────
        # address_cities: in query_by (geographic text search).
        # address_states / address_postal_codes: indexed for filter expressions only.
        # address_countries: facet for country-filter UI; not a free-text search term.
        {"name": "address_cities", "type": "string[]", "optional": True},
        {"name": "address_states", "type": "string[]", "optional": True},
        {"name": "address_countries", "type": "string[]", "facet": True, "optional": True},
        {"name": "address_postal_codes", "type": "string[]", "optional": True},
        # ── Lead / Pipeline ────────────────────────────────────────────────
        {"name": "lead_status", "type": "string", "facet": True, "optional": True},
        {"name": "lead_score", "type": "int32", "sort": True, "optional": True},
        {"name": "intake_stage", "type": "string", "facet": True, "optional": True},
        # ── Custom Fields ──────────────────────────────────────────────────
        # custom_field_values: all values from clients.custom_fields flattened to string[].
        # custom_field_keys: all keys — used for filter expressions, not free-text search.
        {"name": "custom_field_values", "type": "string[]", "optional": True},
        {"name": "custom_field_keys", "type": "string[]", "optional": True},
        # ── Enrichment ─────────────────────────────────────────────────────
        {"name": "enrichment_done", "type": "bool", "optional": True},
        {
            "name": "embedding",
            "type": "float[]",
            "num_dim": 3072,
            # Required for vector_query. If the collection already exists without
            # a vector index, you must recreate it (Typesense cannot always
            # retrofit vector indexing onto an existing float[] field).
            "vec_index": True,
            "optional": True,
        },
        # ── Sort Keys ──────────────────────────────────────────────────────
        {"name": "created_at", "type": "int64", "sort": True},
        {"name": "updated_at", "type": "int64", "sort": True},
        # ── Display-Only (index:False) ─────────────────────────────────────
        # company_id: UUID of the linked company for person clients; frontend link only.
        {"name": "company_id", "type": "string", "index": False, "optional": True},
    ],
}


# ---------------------------------------------------------------------------
# Default full-text search parameters
#
# query_by field order and query_by_weights MUST stay in sync (18 fields, 18 weights).
# Weights reflect user intent:
#   15  name               — primary identifier
#   12  primary_contact_full_name — person name (exact + prefix)
#   12  email              — near-unique; exact-match fallback via EMAIL_SEARCH_PARAMS
#   10  phone_numbers      — near-unique; exact-match fallback via PHONE_SEARCH_PARAMS
#   10  company_name       — employer lookup for person clients
#    6  primary_contact_title — role-based lookup
#    5  tags               — CRM label search
#    5  industry           — sector discovery
#    4  description        — long-form company text
#    3  address_cities     — geographic search
#    3  work_history_companies — past employer (person)
#    3  work_history_titles    — past role (person)
#    3  skills             — competency search (person)
#    2  target_market_segments — B2B context (company)
#    2  current_tech_stack     — technology affiliation (company)
#    2  key_people_names       — executive lookup (company)
#    2  product_names          — product portfolio (company)
#    1  custom_field_values    — dynamic attribute catch-all
# ---------------------------------------------------------------------------

SEARCH_PARAMS: dict[str, Any] = {
    "query_by": (
        "name,primary_contact_full_name,email,phone_numbers,company_name,"
        "primary_contact_title,tags,industry,description,address_cities,"
        "work_history_companies,work_history_titles,skills,"
        "target_market_segments,current_tech_stack,key_people_names,"
        "product_names,custom_field_values"
    ),
    "query_by_weights": "15,12,12,10,10,6,5,5,4,3,3,3,3,2,2,2,2,1",
    "num_typos": 2,
    "typo_tokens_threshold": 1,
    "min_len_1typo": 4,
    "min_len_2typo": 7,
    "prefix": True,
    "sort_by": "_text_match:desc,updated_at:desc",
    "facet_by": (
        "client_type,status,tags,industry,lead_status,"
        "intake_stage,address_countries,preferred_communication_channels"
    ),
    "max_facet_values": 25,
}

# Used when query string contains '@' — strict email lookup, no typos, no prefix.
EMAIL_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "email",
    "num_typos": 0,
    "prefix": False,
}

# Used when query string is digit-heavy — strict phone lookup, no typos, no prefix.
PHONE_SEARCH_PARAMS: dict[str, Any] = {
    "query_by": "phone_numbers",
    "num_typos": 0,
    "prefix": False,
}
