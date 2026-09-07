# Security Architecture & Data Protection

## 1. Threat Model & Protections

| Threat | Mitigation in Platform |
| :--- | :--- |
| **Meta Webhook Spoofing** | Strict `X-Hub-Signature-256` HMAC-SHA256 signature verification on raw request body using constant-time digest comparison (`hmac.compare_digest`). |
| **SQL Injection** | Exclusively parameterized SQLAlchemy 2 constructs; zero raw string interpolation or formatted SQL throughout the codebase. |
| **Prompt Injection** | Customer messages are isolated; system prompts strictly constrain LLM structured JSON output schema; deterministic fallback grounds answers directly from business YAML. |
| **PII Data Leakage** | Raw phone numbers are never stored in plain text or written to logs. Phone numbers are stored as HMAC lookup hashes (`phone_hash`), Fernet ciphertexts (`phone_encrypted`), and masks (`+44*******01`). |
| **Credential Exposure** | All access tokens, app secrets, and webhook tokens are typed as Pydantic `SecretStr`, preventing accidental `repr()` or logger exposure. |
| **DDoS / Payload Abuse** | Enforced request body size limits (`max_request_bytes` = 1MB default) and rate-limit circuits on outbound calls. |

---

## 2. Key Derivation & Encryption

Field encryption uses AES-128-CBC / HMAC-SHA256 authenticated encryption via Fernet:
```python
from cryptography.fernet import Fernet
import base64, hashlib

def derive_fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)
```

HMAC hashes for phone lookups:
```python
import hmac, hashlib

def hash_phone(phone_e164: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), phone_e164.encode("utf-8"), hashlib.sha256).hexdigest()
```
