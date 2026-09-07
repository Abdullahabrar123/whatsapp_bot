# Configuration & Customization Guide

## 1. Directory Structure

Configuration is strictly separated into:

- `config/business.yaml`: Everything specific to the business (name, services, pricing, hours, location, policies, brand voice, escalation contacts).
- `config/bot.yaml`: Bot behavior (confidence thresholds, enabled intents, quiet hours, campaign limits, stop words).
- `config/templates.yaml`: Registry of approved Meta WhatsApp templates.
- `.env`: Deployment secrets, database URLs, Meta credentials, and LLM endpoints.

---

## 2. Business Configuration (`config/business.yaml`)

```yaml
business_id: "brightsmile_dental"
name: "BrightSmile Dental Care"
industry: "Healthcare / Dental"
description: "Premier family and cosmetic dental practice in Central London."
website: "https://www.brightsmile-example.co.uk"
timezone: "Europe/London"
default_language: "en"

brand_voice:
  tone: "Warm, professional, reassuring, and clear"
  formality: "professional"
  personality: "Caring healthcare provider"

business_hours:
  monday: { open: "08:30", close: "17:30" }
  tuesday: { open: "08:30", close: "17:30" }
  wednesday: { open: "08:30", close: "17:30" }
  thursday: { open: "08:30", close: "19:00" }
  friday: { open: "08:30", close: "17:00" }
  saturday: { open: "09:00", close: "13:00" }
  sunday: { closed: true }

services:
  - id: "svc:checkup"
    name: "Routine Check-up"
    description: "Full dental examination including scale and polish assessment."
    price: "£65.00"
    duration_minutes: 30
    booking_required: true

faqs:
  - question: "Are you accepting new NHS patients?"
    answer: "We currently accept new private patients and children under 18 on the NHS."
    category: "general"
```

---

## 3. Bot Configuration (`config/bot.yaml`)

```yaml
enabled_intents:
  - greeting
  - product_inquiry
  - pricing_inquiry
  - availability
  - booking_request
  - location_hours
  - support_request
  - human_agent_request
  - opt_out
  - opt_in

confidence_thresholds:
  minimum_confidence: 0.60
  human_escalation_threshold: 0.40

quiet_hours:
  enabled: true
  start: "21:00"
  end: "08:00"

campaign_settings:
  batch_size: 25
  delay_between_sends_seconds: 1.0
  failure_threshold_percent: 10.0

stop_words:
  - "STOP"
  - "UNSUBSCRIBE"
  - "CANCEL"
  - "OPT OUT"
  - "QUIT"
  - "END"
```

---

## 4. Switching Between Businesses

To deploy the chatbot for a completely different client (e.g. an e-commerce store or accounting firm):

1. Edit `config/business.yaml` with the new company's details, pricing, and FAQs.
2. Edit `.env` with the new business's `META_WABA_ID`, `META_PHONE_NUMBER_ID`, `META_ACCESS_TOKEN`, and `META_APP_SECRET`.
3. Restart the service (`docker compose restart` or `make run`).
4. Zero code changes required!
