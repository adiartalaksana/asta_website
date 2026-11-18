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
| `apps.audit` | HMAC-SHA256 hash-chain audit records and verification |

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
| `/` | Company landing page and platform overview | Public |
| `/ledger/` | General Ledger dashboard | Authenticated user |
| `/admin/` | Django administration and login | Authorized admin |
| `/health/` | Service health response | Platform monitoring |

Landing-page CTAs point to `/ledger/`. Unauthenticated users are redirected to `/admin/login/` before accessing the dashboard.

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

```bash
cd ledger_platform
docker compose -f compose/docker-compose.yml up -d
docker compose -f compose/docker-compose.yml ps
```

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

1. Create a Django superuser for authorized General Ledger access.
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
