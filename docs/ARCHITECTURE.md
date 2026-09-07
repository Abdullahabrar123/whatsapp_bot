# Architecture & Design

## 1. System Philosophy

The WhatsApp Bot Platform is built around four foundational principles:

1. **Configuration-Driven Multi-Tenancy**: The application core contains zero business-specific knowledge, prompt strings, or hardcoded rules. All business logic is driven by validated YAML schemas (`config/business.yaml`, `config/bot.yaml`, `config/templates.yaml`).
2. **Deterministic Compliance & Consent First**: Consent is evaluated before invoking LLMs or composing replies. Opt-out (`STOP`, `UNSUBSCRIBE`) takes effect immediately and writes to a permanent suppression list in the same transaction.
3. **Pluggable, Offline-Capable LLM Layer**: The default provider is a local Ollama server running `qwen3:4b` with schema-constrained JSON mode. If Ollama is unreachable, the system gracefully falls back to a deterministic rule-based extractor that grounds answers directly from business configuration without hallucinations.
4. **Resilient Async Messaging**: Webhooks are acknowledged within milliseconds after claiming idempotency; processing occurs asynchronously via background workers (`arq` / Redis or in-memory workers).

---

## 2. Layered Architecture

```
app/
├── api/             # HTTP boundary (FastAPI): Webhooks, Health, Operator Admin API
├── core/            # Configuration loaders, async DB engine, logging, security cipher, exceptions
├── domain/          # SQLAlchemy 2 models, Pydantic schemas, compliance rules, phone normalization
├── integrations/    # WhatsApp Cloud API client, Webhook parser, Ollama & Deterministic LLM clients
├── prompts/         # Base prompt templates for intent extraction and response generation
├── repositories/    # Clean SQL data access (contacts, conversations, messages, consent, campaigns)
├── services/        # Business logic orchestration (conversation, knowledge, intent, response, consent, campaign, escalation)
└── workers/         # Arq background tasks and lifecycle runtime
```

---

## 3. Inbound Processing Pipeline

1. **Webhook Ingestion**:
   - Meta calls `POST /webhooks/meta`.
   - The raw request body is verified against `X-Hub-Signature-256` using constant-time HMAC-SHA256 comparison.
   - The payload is defensively parsed into `InboundMessage` or `StatusUpdate` objects.
2. **Idempotency Claim**:
   - The unique `meta_message_id` is atomically claimed in the `webhook_events` database table.
   - If already processed or claimed, the webhook returns HTTP 200 immediately without duplicate processing.
3. **Task Dispatch**:
   - Enqueued to Redis via `arq` or dispatched locally.
4. **Consent & Pause Evaluation**:
   - If the message contains an opt-out phrase (`STOP`), the contact is updated to `OPTED_OUT`, added to `suppression_list`, and a single confirmation is sent.
   - If the bot is paused globally or per contact, processing stops without sending an automated reply.
5. **Retrieval & Intent Extraction**:
   - The knowledge retrieval engine scores relevant business chunks from YAML and Markdown knowledge files.
   - The LLM client (Ollama or Deterministic) classifies the customer intent and extracts parameters into a typed `IntentResult`.
6. **Response Composition & Grounding**:
   - `ResponseService` formats the grounded response using verified business facts.
7. **Escalation & Send**:
   - If confidence is below threshold, or the customer requests a human or expresses a complaint, an escalation is created, the bot is paused, and a summary brief is prepared.
   - If inside Meta's 24-hour service window, the reply is sent via `WhatsAppClient.send_text`.
