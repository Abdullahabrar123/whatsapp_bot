# Compliance, Consent & Legal Controls

## 1. Regulatory Frameworks

The platform is designed to enforce strict compliance with:
- **Meta WhatsApp Business Policy** (24-hour service window, template approvals, recipient opt-in rules)
- **GDPR / UK GDPR** (Lawful basis for processing, explicit consent evidence, right to erasure, data minimization)
- **TCPA / PECR** (Prior express written consent for marketing, immediate opt-out enforcement)

---

## 2. Core Compliance Guarantees

### 1. 24-Hour Customer Service Window
- Free-form conversational replies can only be sent within 24 hours of the customer's last inbound message (`last_inbound_at`).
- Outside this window, the bot refuses to send free-form messages, preventing Meta re-engagement policy violations. Only approved templates are allowed.

### 2. Marketing vs Utility Segregation
- Utility messages (e.g. appointment reminders) must never contain marketing copy.
- Marketing templates require explicit recorded opt-in (`ConsentStatus.OPTED_IN`).

### 3. Immediate Opt-Out & Permanent Suppression
- If a customer sends any recognized stop word (`STOP`, `UNSUBSCRIBE`, `CANCEL`, `OPT OUT`, `QUIT`, `END`), the system:
  1. Sets contact status to `OPTED_OUT`.
  2. Creates an audit record in `consent_records`.
  3. Adds the contact hash to `suppression_list`.
  4. Returns a single confirmation message.
  5. Completely blocks all future outbound marketing campaigns.
- Both writes occur atomically in the same database transaction.

### 4. Recipient Quiet Hours
- Campaigns evaluate recipient timezones against `bot.yaml` quiet hours (e.g. 21:00 to 08:00).
- If quiet hours are active, messages are postponed or rejected.
