# Acceptance Tests & Verification Matrix

| Criterion | Expected Outcome | Verification Method |
| :--- | :--- | :--- |
| **Multi-Tenancy** | Zero code change to switch between two different businesses. | Automated test in `tests/e2e/test_multi_business_switch.py`. |
| **Webhook Signature Verification** | Valid signatures accepted (200), tampered signatures rejected (403). | `tests/integration/test_webhooks_api.py`. |
| **Idempotency** | Duplicate webhook deliveries are processed exactly once. | `tests/integration/test_webhooks_api.py`. |
| **Grounded Answering** | Bot answers services and pricing directly from `business.yaml` without hallucinations. | `tests/unit/test_knowledge_service.py` & `test_response_service.py`. |
| **Instant Opt-Out** | STOP keyword immediately suppresses contact and confirms opt-out. | `tests/integration/test_conversation_flow.py`. |
| **Human Escalation** | Low confidence or human requests create escalation and pause the bot. | `tests/integration/test_conversation_flow.py`. |
| **Outbound Campaign Staging** | Only opted-in recipients pass compliance; suppressed/unknown are blocked. | `tests/integration/test_campaign_execution.py`. |
| **Operator API Control** | Pause/resume bot globally and per contact; resolve escalations. | `tests/integration/test_admin_api.py`. |
| **Security & Redaction** | Raw phone numbers and secrets never appear in logs or exception traces. | `tests/unit/test_phone_security.py`. |
