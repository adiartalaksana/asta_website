# Asta Asa Abadi — Autonomous General Ledger Platform

## 1. Purpose

`ledger_platform` is a Django modular monolith for an immutable, double-entry general ledger. It separates untrusted inbound transaction data from posted ledger records and uses PostgreSQL as the source of truth.

## 2. Runtime topology

```text
Authorized browser
        │
        ▼
Django application
  ├─ Landing page: /
  ├─ General Ledger: /ledger/
  ├─ Admin authentication: /admin/
  └─ Health check: /health/
        │
        ├───────────── Redis / Celery
        │
        ▼
PostgreSQL 16 + pgvector
  ├─ Chart of Accounts
  ├─ Raw transaction staging
  ├─ Immutable journal entries
  └─ Hash-chained audit trail
```

The PostgreSQL and Redis containers run on the private `ledger_internal` Docker network. PostgreSQL does not expose a host port.

## 3. Application modules

| Module | Responsibility |
| --- | --- |
| `apps.core` | Shared UUID, timestamp, immutable model primitives, financial precision constants |
| `apps.accounts` | Chart of Accounts, currencies, active-account validation, balance calculation |
| `apps.ledger` | Batches, journal headers and lines, atomic posting, reversals, dashboard |
| `apps.ai_engine` | Raw staging records, private LLM client, vector matching and categorization pipeline |
| `apps.audit` | HMAC-SHA256 hash-chain audit records and verification, security event log |
| `apps.portal_users` | Internal CMS for portal accounts: roles, passwords, activation, login/activity logging |

## 4. Data lifecycle

```text
External source
    ↓
RawTransactionRecord
    ↓
Tier 1 rules → Tier 2 vector search → Tier 3 private LLM
    ↓
Confidence and human-review triage
    ↓
JournalHeader + JournalLine draft
    ↓
Atomic posting and double-entry validation
    ↓
Immutable journal lines + audit hash-chain event
```

`RawTransactionRecord` is the staging boundary. Incoming JSON payloads, source IDs, SHA-256 deduplication hashes, inferred account, confidence, and review status remain there until approved for journal creation.

## 5. Ledger invariants

1. Monetary values use `Decimal(18, 4)`; float types are not used.
2. A posting uses `transaction.atomic()` and PostgreSQL `REPEATABLE READ`.
3. Every posted entry has at least two lines and total debit equals total credit.
4. Only active accounts can receive posted journal lines.
5. A PostgreSQL trigger rejects `UPDATE` and `DELETE` operations on posted lines.
6. Corrections use `reversal_entry()`, which creates equal and opposite new lines.
7. Every post creates an HMAC-SHA256 audit event linked to the preceding event hash.

## 6. General Ledger navigation

| URL | Purpose | Access |
| --- | --- | --- |
| `/` | Company landing page (login popup at `/#portal-login`) | Public |
| `/login/`, `/logout/` | Portal sign-in (keeps `?next=`) and sign-out (POST) | Public / signed-in |
| `/ledger/` | General Ledger dashboard | Any portal account |
| `/ledger/journals/`, `/ledger/accounts/`, `/ledger/bank-statements/`, `/ledger/reconciliation/`, `/ledger/audit/` | Ledger pages | Any portal account (actions need role permissions) |
| `/<CONTROL_URL_PATH>/` (secret, from `.env`) | Internal CMS: client portal accounts, AI agents, activity (see section 10). Separate from the portal, no login | Access token only |
| `/ledger/account/password/` | Change own password | Any portal account |
| `/<ADMIN_URL_PATH>/` (secret, from `.env`) | Django administration (Chart of Accounts, currencies) | Access token, then Administrator / Akuntan / Super admin login |
| `/health/` | Service health response | Platform monitoring |

Unauthenticated visitors are sent to the landing-page login popup and returned to the requested page after signing in. `/admin/`, `/ledger/control/` and older CMS links return 404. Only the password-change links `/ledger/users/my-password/` and `/ledger/pengguna/kata-sandi-saya/` still redirect.

## 7. Database and environment

The local connection configuration lives in `.env`, which is ignored by Git. Use `.env.example` as the safe template for another environment.

```dotenv
POSTGRES_DB=ledger_platform
POSTGRES_USER=ledger
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
```

`compose/postgres/init/01-enable-extensions.sql` enables the `vector` extension and creates the `collection` schema for ingestion-related data.

### Starting internal services

The project lives in `/home/obey/Documents/Asta Asa Abadi/Website` (quote the path; it contains spaces).

```bash
cd "/home/obey/Documents/Asta Asa Abadi/Website/compose"
docker compose up -d                          # start (migrations run automatically)
docker compose ps                             # status
docker compose restart                        # restart
docker compose logs -f web                    # errors / logs
docker compose up -d --force-recreate web     # after editing .env
docker compose up -d --build                  # after changing requirements/ or Dockerfile
docker compose down                           # stop (never add -v: it deletes the database)
```

The site runs at http://localhost:8000. Uploaded documents are stored in `media/` (ignored by Git).

### Preparing Django tables

Run Django commands from an application container connected to `ledger_internal`, or provide a development-only PostgreSQL endpoint through environment variables:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

## 8. Current implementation update

- Django settings are split into `base`, `local`, `production`, and `test`.
- Home is a Django landing page with direct General Ledger CTA.
- General Ledger dashboard is available at `/ledger/` after authentication.
- Internal PostgreSQL 16 with pgvector and Redis Compose services are configured.
- The Docker Compose `web` service exposes the Django site at port `8000`.
- Raw transaction staging model is available for future ingestion workers.
- Initial Django migrations are generated and applied, including the PostgreSQL immutability trigger.
- AI inference is intentionally limited to internal Ollama/vLLM endpoints.

- Draft journals are posted (`/ledger/journals/<id>/post/`) and reversed (`.../reverse/`) from the Jurnal page; every post appends to the HMAC hash chain, which `audit_chain_single_successor` keeps linear.
- Manual statement lines can be deleted until the statement is reconciled.
- Chart of Accounts and currencies are managed in `/admin/`; ledger and audit records are read-only there.
- Logout is a POST to `/logout/`; `/login/` preserves the `?next=` page.

### Running tests

Tests need PostgreSQL (the immutability trigger is PostgreSQL-only):

```bash
docker exec compose-web-1 python manage.py test apps
```

## 9. Next operational steps

1. Create portal accounts from the control center (`/<CONTROL_URL_PATH>/users/`) (a superuser can be created with `docker exec -it compose-web-1 python manage.py createsuperuser`).
2. Configure an internal reverse proxy and identity provider before production deployment.
3. Add Celery worker services for asynchronous ingestion and categorization.

## Bank-statement PDF/image reader (Gemini)

OCR (Tesseract/pdfplumber) has been removed. PDF, PNG, JPG, WEBP and TIFF (converted to PDF)
statements are sent to the Google Gemini API (`apps/ai_engine/gemini.py`), which returns a JSON
transcription, a suggested COA code per row and an Indonesian summary. `apps/ledger/document_reader.py`
re-checks everything with Decimal arithmetic (row completeness, running balances, opening + in − out =
closing, printed totals, period dates). A statement can be created from the AI rows only when all
required checks pass and a user approves it; otherwise use "Baca ulang AI" or the manual input fallback.

Configuration (`.env`, then recreate the container with `docker compose up -d`):

- `GEMINI_API_KEY` — required, from Google AI Studio.
- `GEMINI_MODEL` — default `gemini-flash-latest`; pin a specific model for reproducible results.
- `GEMINI_TIMEOUT_SECONDS` — default 180. Reading is synchronous during the upload request.

Documents are sent to Google; use a paid Gemini API tier so the data is not used for training.

## 10. Control center (Pusat Kendali)

A separate internal CMS that holds only internal settings. It is **not part of the client portal**: it has its own layout (`templates/control/base.html`: logo, tabs, theme toggle, **Kunci CMS**), no portal account login, and no link from the public site or the portal. The access token is the only key; actions are logged as **CMS (token)**.

### Hidden route and access token

| `.env` variable | Purpose |
| --- | --- |
| `CONTROL_URL_PATH` | Secret URL prefix of the control center (random slug) |
| `ADMIN_URL_PATH` | Secret URL prefix of Django admin (random slug) |
| `ADMIN_ACCESS_TOKEN` | Token asked once per browser session before either area opens. Empty = both areas closed (404) |
| `ADMIN_UNLOCK_HOURS` | How long an unlock lasts (default 8) |

- Gate: `apps/portal_users/internal_gate.py` (`InternalAreaGate` middleware). Both secret prefixes show the token page; any other guessed path (e.g. `/admin/`, `/ledger/control/`) is a plain **404**. A portal login never opens the CMS. **Kunci CMS** removes the unlock from the browser without signing out of the portal. Django admin: the token page comes before the admin login.
- Token check is constant-time; 5 wrong tokens from one IP lock the form for 15 minutes; unlocks and failures are logged (Aktivitas → "Token akses internal"). Changing `ADMIN_ACCESS_TOKEN` invalidates every existing unlock.
- Secret prefixes are registered as hidden routes, so Django's DEBUG 404 page prints `[hidden]/` instead of the path. Responses carry `X-Robots-Tag: noindex, nofollow` and `Cache-Control: no-store`.
- After editing these values run `docker compose up -d --force-recreate web`. Show your current values with `grep -E 'CONTROL_URL_PATH|ADMIN_URL_PATH|ADMIN_ACCESS_TOKEN' .env`.

### Tabs

| Tab | Route | Purpose |
| --- | --- | --- |
| Ringkasan | `/<CONTROL_URL_PATH>/` | Account counts, Gemini connection status (API key, model, reader on/off, documents read), agent pipeline, latest activity |
| Akun portal | `/<CONTROL_URL_PATH>/users/` | Client portal accounts (below) |
| Agen AI | `/<CONTROL_URL_PATH>/agents/` | One AI agent per process with editable instructions (below) |
| Aktivitas | `/<CONTROL_URL_PATH>/activity/` | Full security log, filter by login / account / agent changes, search by user or IP |

### Portal accounts

Roles are Django groups, created and refreshed automatically by `apps/portal_users/roles.py` after every `migrate`.

| Role | Group | Can do |
| --- | --- | --- |
| Super admin | `is_superuser` | Everything; only another super admin can edit it |
| Administrator | Administrator Portal | Full portal access: Chart of Accounts (Django admin), journals, statements, audit. Does **not** manage accounts or agents (CMS only) |
| Akuntan | Akuntan | Import statements, create/post/reverse journals, edit Chart of Accounts |
| Klien (lihat saja) | Klien (lihat saja) | View only |

| Route | Action |
| --- | --- |
| `/<CONTROL_URL_PATH>/users/` | List, search, filter by role/status |
| `/<CONTROL_URL_PATH>/users/new/` | Create an account (generated password shown once, or set manually) |
| `/<CONTROL_URL_PATH>/users/<id>/` | Edit name, email, role, active status |
| `/<CONTROL_URL_PATH>/users/<id>/password/` | Reset password (signs the account out everywhere) |
| `/<CONTROL_URL_PATH>/users/<id>/status/` | Activate / deactivate (POST) |
| `/<CONTROL_URL_PATH>/users/<id>/delete/` | Delete (POST; refused when the account already recorded data — deactivate instead) |
| `/ledger/account/password/` | Any user changes their own password |

Safety rules: the last active super admin cannot be deactivated or deleted; passwords follow Django validators (min. 10 characters, not common, not numeric-only, not similar to the username). Logins, failed logins, logouts, account changes and agent changes are written to `audit.SecurityEventLog` with the client IP. View-only accounts do not see action buttons, and forbidden actions show the branded `templates/403.html` page.

### AI agents (instruction per process)

Models `ai_engine.AgentProfile` and `AgentInstructionVersion`; logic in `apps/ai_engine/agents.py`. Default agents are created on `migrate`.

| # | Process | Agent | Role in the Gemini request |
| --- | --- | --- | --- |
| 01 | `statement_reader` | Pembaca rekening koran | Transcription rules; owns the model and temperature. Switching it off disables PDF/image reading |
| 02 | `account_mapper` | Pemeta akun COA | Rules for `suggested_account_code`; receives the active Chart of Accounts. Off = no suggestions |
| 03 | `statement_summarizer` | Perangkum & rekap | Summary style for the review page. Off = no summary |

- All three instructions are composed (in this order) into **one** Gemini call per document, after a fixed **SYSTEM CONTRACT** (JSON schema, amount/date formats, money in/out). The contract is locked in code so edited instructions cannot break the server-side balance checks.
- Every save creates a new version with an optional change note; any earlier version can be restored (restore also creates a new version). "Pratinjau prompt lengkap" (`/<CONTROL_URL_PATH>/agents/preview/`) shows the exact text sent to Gemini.
- Each AI-read document stores the agent versions that read it (`ai_result.agents`), shown on the document review page.
- Model name is validated (e.g. `gemini-flash-latest`, `gemini-2.5-pro`); empty = `GEMINI_MODEL` from `.env`. Temperature 0–1 (0 recommended for financial figures).

## 11. Branding, themes and language

- **Logo:** vector mark from the brand draft (`Draft Llogo AAA 2.pdf`). Inline SVG partials `templates/partials/logo_full.html` and `logo_mark.html` follow the CSS `color`; static files in `static/img/` (`asta-asa-abadi-logo.svg`, `-mark.svg`, `-logo-dark.png`, `-logo-white.png`, favicons). The logo also appears in the Django admin header and PDF/PNG statement exports.
- **Landing page:** `templates/landing/index.html`, `static/css/landing.css`, `static/js/company.js` — immersive hero with the logo mark, growth-pulse canvas, scroll reveals, solid navbar; motion is disabled for `prefers-reduced-motion`.
- **Day / dark theme:** `templates/partials/theme_init.html` applies the theme before first paint; `static/js/theme.js` handles the toggle (public header and portal sidebar). Default follows the browser/OS setting; a manual choice is stored in `localStorage` (`aaa-theme`).
- **Language (ID/EN):** follows the browser language (Indonesian → ID, everything else → EN) until the visitor uses the toggle (`aaa-language-choice`). Portal pages are Indonesian.
- **Portal styles:** `static/css/platform.css` (light theme plus `:root[data-theme="dark"]` overrides at the end of the file).

## 12. Source control

- Remote: `https://github.com/adiartalaksana/asta_webiste` (branch `main`).
- Git ignores `.env`, `.venv/`, caches, `staticfiles/` and **`media/`** (client bank statements must never be committed). `.env.example` holds placeholders only.
- Pushing needs GitHub credentials on this machine (SSH key or a fine-grained token with *Contents: Read and write*), then `git push -u origin main`.
