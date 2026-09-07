# Enterprise WhatsApp Business Platform Chatbot

A generic, configuration-driven, production-ready WhatsApp Business Platform chatbot built on the official Meta Cloud API, local LLMs (Ollama with `qwen3:4b`), async SQLAlchemy 2, PostgreSQL / SQLite, Redis / arq, and FastAPI.

---

## Key Highlights

- **Zero-Code Multi-Tenancy**: Deploy the exact same codebase for any business (dental clinic, e-commerce, real estate, accounting, etc.) simply by editing `config/business.yaml`, `config/bot.yaml`, `config/templates.yaml`, and `.env`.
- **Local Private LLM (Ollama)**: Uses local Ollama (`qwen3:4b` default, `qwen3:1.7b` fallback) with thinking disabled for fast responses. Features a deterministic rule-based client for 100% offline testing and graceful degradation during model outages.
- **Dynamic Meta Identity**: Switch Meta Phone Number IDs, WABA IDs, access tokens, and webhook secrets seamlessly in `.env` without touching Python code.
- **Consent & Compliance Engine**: Enforces Meta's 24-hour customer service window, explicit marketing opt-in evidence, permanent suppression lists, instant opt-out detection (`STOP`, `UNSUBSCRIBE`, etc.), and recipient quiet hours.
- **Human Escalation & Pluggable CRM**: Automatically escalates low-confidence conversations, complaints, and human requests; pauses the bot per contact; prepares human briefing summaries; and interfaces cleanly with CRMs (GoHighLevel, HubSpot, Salesforce).
- **Outbound Campaign Workflow**: CLI and API workflow with pre-send compliance staging, rate pacing, auto-pausing on failure thresholds, dry-run simulation, and delivery status tracking.

---

## Architecture Overview

```
                          ┌──────────────────────────┐
                          │   Meta WhatsApp Cloud    │
                          └─────────────┬────────────┘
                                        │ Webhook POST / Send REST
                                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Application Gateway                     │
│  - Webhook Verification & HMAC-SHA256 Signature Validation             │
│  - Correlation ID Middleware & Request Hardening                       │
│  - Prometheus Metrics & Health Readiness Probes                        │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
       ┌────────────────────────┐       ┌────────────────────────┐
       │ Inbound Orchestration  │       │   Operator / Admin API │
       │ (ConversationService)  │       │ (Pause, Escalations,   │
       └────────────┬───────────┘       │  Campaigns, Consent)   │
                    │                   └────────────────────────┘
                    ├────────────────────────────────┐
                    ▼                                ▼
     ┌────────────────────────────┐    ┌───────────────────────────┐
     │ Knowledge Retrieval Engine │    │  LLM / Intent Extraction  │
     │ - Lexical / BM25 Index     │    │ - Ollama (qwen3:4b)       │
     │ - Business Config + Docs   │    │ - Deterministic Fallback  │
     └────────────────────────────┘    └───────────────────────────┘
                    │
                    ▼
     ┌────────────────────────────┐
     │  Compliance & Storage Gate │
     │ - 24-Hour Window Checks    │
     │ - Consent & Suppression    │
     │ - Encrypted PII Storage    │
     └────────────────────────────┘
```

---

## Quick Start (Beginner Friendly)

### 1. Prerequisites
- Python 3.12+ installed
- (Optional) Docker & Docker Compose
- (Optional for local LLM) [Ollama](https://ollama.ai) installed and running

### 2. Local Setup
```bash
# Clone and enter the repository
cd whatsapp_chat

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.\.venv\Scripts\activate
# On Linux/macOS:
# source .venv/bin/activate

# Install dependencies in editable mode with development tools
pip install -e ".[dev]"
```

### 3. Configure Environment
```bash
# Copy example configuration to .env
cp .env.example .env
```
Open `.env` and fill in your details. For offline development and testing, the default SQLite and deterministic LLM settings work immediately without any API keys.

### 4. Validate Configuration
```bash
python scripts/validate_config.py
```

### 5. Run Database Migrations
```bash
alembic upgrade head
```

### 6. Run the Test Suite
```bash
pytest --cov=app --cov-report=term-missing tests/
```

### 7. Start the Server
```bash
python main.py
# The application starts at http://localhost:8000
# OpenAPI Docs available at http://localhost:8000/docs
```

---

## Configuring for a New Business

To brand the bot for a new business, you never need to edit Python code:

1. **Business Identity & Knowledge**: Edit `config/business.yaml` (set business name, operating hours, prices, products/services, FAQs, policies, and emergency escalation).
2. **Bot Rules & Tone**: Edit `config/bot.yaml` (set enabled intents, confidence thresholds, stop words, and campaign rate limits).
3. **Template Registry**: Edit `config/templates.yaml` (map approved Meta template names, languages, and category parameters).
4. **Meta Credentials**: Update `.env` with the new business's `META_WABA_ID`, `META_PHONE_NUMBER_ID`, `META_ACCESS_TOKEN`, and `META_APP_SECRET`.
5. **Restart**: Restart the application (`docker compose restart` or `python main.py`). The bot immediately reflects the new brand!

---

## Outbound Campaigns CLI

### Step 1: Import Recipients
```bash
python scripts/import_contacts.py --file data/recipients.csv
```

### Step 2: Validate Pre-Send Eligibility
```bash
python scripts/validate_recipients.py --file data/recipients.csv
```

### Step 3: Run Campaign Dry-Run Simulation
```bash
python scripts/send_campaign.py --name "june_promotions" --template "appointment_reminder_v1" --dry-run
```

### Step 4: Dispatch Approved Live Campaign
```bash
python scripts/send_campaign.py --name "june_promotions" --template "appointment_reminder_v1" --confirm
```

---

## Documentation Index

Detailed engineering and operational guides are available in the [`docs/`](file:///c:/Users/Skylinks/Desktop/whatsapp_chat/docs) directory:

- [Architecture & Design Decisions](docs/ARCHITECTURE.md)
- [Configuration & Rebranding Guide](docs/CONFIGURATION.md)
- [Meta Cloud API Setup & Webhook Guide](docs/META_SETUP.md)
- [Deployment & Production Runbook](docs/DEPLOYMENT.md)
- [Operations & Operator API Guide](docs/OPERATIONS.md)
- [Compliance, Consent & Legal Guide](docs/COMPLIANCE.md)
- [Security, Encryption & Hardening](docs/SECURITY.md)
- [Troubleshooting & Diagnostics](docs/TROUBLESHOOTING.md)
- [Acceptance Tests & Verification](docs/ACCEPTANCE_TESTS.md)
