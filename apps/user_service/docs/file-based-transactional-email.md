# File-Based Transactional Email — Developer Guide

This document describes the **reusable file-template email system** in `apps/user_service`.
Use it whenever you need a new automated transactional email (welcome, reminder, notification, etc.).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Send transport:** `app/utils/email_utils.py` → `send_email()` (Supabase `custom-email` edge function)
- **Example implementation:** [unit-allotment-welcome-email.md](./unit-allotment-welcome-email.md)

> **Scope:** File templates under `app/templates/emails/` with layout + body separation.
> This is separate from the admin **Email Template Builder** (`email_templates` table), which is not
> wired to automated sends today. See [§9 Future: DB override](#9-future-db-override) for a possible hybrid path.

______________________________________________________________________

## 1. Goals

| Goal                     | How                                                   |
| ------------------------ | ----------------------------------------------------- |
| Maintainable copy & HTML | Lives in **template files**, not Python f-strings     |
| Multipart emails         | Every template ships **plain text + HTML**            |
| Consistent branding      | Shared **layout** shell (header, footer, links)       |
| Easy to extend           | One generic sender: `send_templated_email()`          |
| Safe substitution        | HTML values are escaped; plain text is inserted as-is |

### When to use this system

- New **automated** emails triggered by backend services (assign unit, approve request, etc.)
- Emails that need a stable, reviewable HTML design in git

### When **not** to use this system (yet)

- One-off admin-composed emails from the UI → Email Template Builder
- Emails that must be **per-org editable** without a deploy → future hybrid (§9)

______________________________________________________________________

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Service layer (e.g. ContactUnitsService)                       │
│    → helper builds body_context (data only)                     │
│    → send_templated_email() or thin wrapper                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  file_email_renderer.render_email()                             │
│    bodies/{name}.txt + .html  → substitute {{.var}}             │
│    layouts/{layout}.txt + .html → inject {{BODY_CONTENT}}       │
│    subjects/{name}.txt        → subject line                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  email_utils.send_email(to, subject, plain_text, html=...)      │
└─────────────────────────────────────────────────────────────────┘
```

**Separation of concerns:**

| Layer                        | Responsibility                                         |
| ---------------------------- | ------------------------------------------------------ |
| **Template files**           | Copy, layout, styling, conditional blocks              |
| **Helper module**            | Build `dict[str, str]` context from DB rows / settings |
| **`send_templated_email`**   | Render + send + log (generic for all templates)        |
| **Named wrapper** (optional) | Fixed template name + log label for a specific email   |

______________________________________________________________________

## 3. Directory layout

All templates live under:

```
apps/user_service/app/templates/emails/
├── layouts/
│   ├── transactional.html          # HTML shell (default layout)
│   └── transactional.txt           # Plain-text shell
├── bodies/
│   ├── {template_name}.html        # HTML body for one email type
│   └── {template_name}.txt         # Plain-text body (mirror content)
└── subjects/
    └── {template_name}.txt         # Subject line
```

**Naming:** `{template_name}` is the `template=` argument passed to `send_templated_email`.
Use `snake_case` (e.g. `unit_allotment_welcome`, `payment_reminder`).

______________________________________________________________________

## 4. Placeholder convention

Aligned with `EmailTemplateService` (`email_template_service.py`):

| Token                  | Where                 | Meaning                            |
| ---------------------- | --------------------- | ---------------------------------- |
| `{{BODY_CONTENT}}`     | Layout files only     | Replaced by the rendered body      |
| `{{.variable_key}}`    | Body, layout, subject | Substituted from context dict      |
| `{{RAW:variable_key}}` | HTML body/layout only | Inserted **without** HTML escaping |

### Rules

- **Body** files must **not** contain `{{BODY_CONTENT}}`
- **Layout** files **must** contain `{{BODY_CONTENT}}`
- Placeholder keys: lowercase letters, digits, underscores (`first_name`, `ios_app_url`)
- Missing keys log a warning and render as empty string
- **HTML** rendering: `{{.var}}` values are HTML-escaped
- **Plain text** rendering: values inserted as-is (no escaping)
- Use `{{RAW:...}}` sparingly — only when you intentionally pass pre-built HTML from a helper

### Default layout context

`file_email_renderer.default_layout_context()` fills these automatically (override via `layout_context`):

| Key                  | Source                                       |
| -------------------- | -------------------------------------------- |
| `app_name`           | `shared_settings.app_name`                   |
| `company_name`       | `shared_settings.company_name`               |
| `support_email`      | `shared_settings.company_support_email`      |
| `company_website`    | `shared_settings.company_website`            |
| `privacy_policy_url` | `shared_settings.company_privacy_policy_url` |
| `terms_url`          | `shared_settings.company_terms_url`          |
| `current_year`       | Current calendar year                        |

Subject templates receive **both** layout context and body context merged.

______________________________________________________________________

## 5. Core API

### 5.1 Render only — `file_email_renderer.render_email`

**File:** `app/utils/file_email_renderer.py`

```python
plain_text, html, subject = render_email(
    body="unit_allotment_welcome",       # template name
    layout="transactional",              # optional, default "transactional"
    body_context={"first_name": "Jane"},
    layout_context=None,                 # optional overrides
)
```

Returns `(plain_text, html, subject)`.

Raises `EmailTemplateNotFoundError` if a required file is missing.
Raises `ValueError` if a layout file omits `{{BODY_CONTENT}}`.

### 5.2 Send — `email_utils.send_templated_email`

**File:** `app/utils/email_utils.py`

```python
from apps.user_service.app.utils.email_utils import send_templated_email

ok = send_templated_email(
    email="user@example.com",
    template="unit_allotment_welcome",
    body_context={"first_name": "Jane", "community_name": "Green Valley", ...},
    layout="transactional",              # optional
    layout_context=None,                 # optional
    from_name=None,                      # optional; defaults to ROSS_AI_FROM_NAME
    email_type="Unit allotment welcome", # optional; used in log messages
)
```

- Returns `True` on successful send, `False` on failure (never raises to caller)
- Logs success/failure with `email_type` or a humanized template name
- Always sends **multipart** (plain text + HTML)

### 5.3 Named wrapper (recommended for recurring emails)

Keep call sites readable by wrapping the generic sender:

```python
def send_unit_allotment_welcome_email(
    *,
    email: str,
    body_context: dict[str, str],
    layout_context: dict[str, str] | None = None,
) -> bool:
    return send_templated_email(
        email=email,
        template="unit_allotment_welcome",
        body_context=body_context,
        layout_context=layout_context,
        email_type="Unit allotment welcome",
    )
```

______________________________________________________________________

## 6. How to add a new email

Follow this checklist in order.

### Step 1 — Add template files

Create three (or five) files for `{template_name}`:

```
bodies/{template_name}.html
bodies/{template_name}.txt
subjects/{template_name}.txt
```

Reuse `layouts/transactional.*` unless you need a different shell.

**Plain text and HTML should convey the same information.** Plain text can be simpler
(labeled lines instead of tables) but must not omit critical facts.

### Step 2 — Define body context keys

Document every `{{.key}}` used in body/subject templates. Example:

| Variable     | Description      | Example       |
| ------------ | ---------------- | ------------- |
| `first_name` | Greeting name    | `Jane`        |
| `due_date`   | Payment due date | `15 Oct 2026` |

Keep values as **strings**. Format dates, currency, and phone numbers in the helper, not in templates.

### Step 3 — Add a helper module

**Pattern:** `app/utils/{feature}_email_helpers.py`

```python
def build_payment_reminder_body_context(*, contact: dict, invoice: dict) -> dict[str, str]:
    return {
        "first_name": str(contact.get("first_name") or "there").strip(),
        "amount_due": format_currency(invoice["amount"]),
        "due_date": format_date(invoice["due_at"]),
        # include app_name if used in body (layout already has it)
    }
```

Helpers should be **data-only** — no HTML strings unless you use `{{RAW:...}}` intentionally.

### Step 4 — Wire the trigger

In the service that owns the business event:

```python
body_context = build_payment_reminder_body_context(contact=contact, invoice=invoice)
send_templated_email(
    email=contact_email,
    template="payment_reminder",
    body_context=body_context,
    email_type="Payment reminder",
)
```

**Non-blocking pattern:** Email failure should usually **not** fail the API transaction.
Log errors and return success from the primary operation (see unit allotment integration).

### Step 5 — Tests

| Test             | File                          | What to assert                                                  |
| ---------------- | ----------------------------- | --------------------------------------------------------------- |
| Render merge     | `test_file_email_renderer.py` | Subject/body contain expected copy; `{{BODY_CONTENT}}` gone     |
| HTML escape      | `test_file_email_renderer.py` | User input with `<script>` is escaped in HTML                   |
| Missing template | `test_file_email_renderer.py` | `EmailTemplateNotFoundError`                                    |
| Send wiring      | `test_email_utils.py`         | Mock `render_email` + `send_email`; verify args                 |
| Service hook     | `test_*_service.py`           | Trigger calls send helper when email present; skips when absent |

Run unit tests:

```bash
cd ats-home-craft-python-service
ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests/unit/test_file_email_renderer.py -q
ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests/unit/test_email_utils.py -q
```

______________________________________________________________________

## 7. Template authoring tips

### HTML email

- Use **table-based** layout (see `layouts/transactional.html`) for client compatibility
- Inline CSS on elements; avoid external stylesheets
- Test in a real inbox (Gmail, Outlook) for major launches

### Conditional sections

Handle optional content in templates with empty-string placeholders from the helper:

```python
# Helper
"app_download_fallback": (
    "Contact your community office for app download instructions."
    if not ios_url and not android_url
    else ""
),
```

In HTML, wrap optional blocks so empty values do not leave broken layout (hide rows when URL is blank).

### Subject lines

Keep subjects in `subjects/{name}.txt`. They can reference layout vars (`{{.app_name}}`) and body vars (`{{.community_name}}`).

### New layout shell

If `transactional` is not suitable:

1. Add `layouts/{name}.html` and `layouts/{name}.txt` with `{{BODY_CONTENT}}`
1. Pass `layout="{name}"` to `send_templated_email`

______________________________________________________________________

## 8. Reference implementation

The first production email using this system is **unit allotment welcome**:

| Piece          | Location                                                             |
| -------------- | -------------------------------------------------------------------- |
| Templates      | `app/templates/emails/bodies/unit_allotment_welcome.*`               |
| Subject        | `app/templates/emails/subjects/unit_allotment_welcome.txt`           |
| Context helper | `app/utils/unit_allotment_email_helpers.py`                          |
| Send wrapper   | `email_utils.send_unit_allotment_welcome_email`                      |
| Trigger        | `ContactUnitsService._maybe_send_unit_allotment_welcome_email`       |
| Full spec      | [unit-allotment-welcome-email.md](./unit-allotment-welcome-email.md) |

______________________________________________________________________

## 9. Future: DB override

If orgs need editable copy without redeploying:

1. Seed a DB TRIGGER template on org create (body mirrors file default)
1. At send time, try `EmailTemplateService.render_email_template`
1. On missing/draft/invalid template → **fallback to file templates** (this guide)

File templates remain the **source of truth in git**; DB becomes an optional override.

______________________________________________________________________

## 10. Related code

| Concern               | Location                                            |
| --------------------- | --------------------------------------------------- |
| Template renderer     | `app/utils/file_email_renderer.py`                  |
| Generic send          | `app/utils/email_utils.py` → `send_templated_email` |
| Low-level transport   | `app/utils/email_utils.py` → `send_email`           |
| DB template builder   | `app/services/email_template_service.py`            |
| Layout token constant | `app/constants/default_email_layout.py`             |
| Example helper        | `app/utils/unit_allotment_email_helpers.py`         |
| Renderer tests        | `tests/unit/test_file_email_renderer.py`            |
| Send tests            | `tests/unit/test_email_utils.py`                    |

______________________________________________________________________

## 11. Quick copy-paste checklist

```
[ ] bodies/{name}.html + .txt
[ ] subjects/{name}.txt
[ ] build_{name}_body_context() helper
[ ] send_templated_email() or send_{name}_email() wrapper
[ ] Service hook (non-blocking)
[ ] test_file_email_renderer.py cases
[ ] test_email_utils.py send wiring
[ ] test service integration (mock send)
[ ] Manual inbox QA (HTML + plain-text parts)
```
