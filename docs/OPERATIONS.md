# Operations & Operator API Runbook

The platform includes a protected Operator API under `/admin/*` requiring the `X-Admin-API-Key` header.

---

## 1. Emergency Pause Switches

### Global Bot Pause
To immediately stop all automated responses across the entire business:
```bash
curl -X POST http://localhost:8000/admin/bot/pause \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "System maintenance", "actor": "alice"}'
```

To resume automated responses:
```bash
curl -X POST http://localhost:8000/admin/bot/resume \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY"
```

### Single Contact Pause (Human Takeover)
When a human operator takes over a specific conversation:
```bash
curl -X POST http://localhost:8000/admin/contacts/+447700900001/pause \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Human agent chatting in Meta Business Suite"}'
```

To release the contact back to the bot:
```bash
curl -X POST http://localhost:8000/admin/contacts/+447700900001/resume \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY"
```

---

## 2. Managing Escalations

### List Open Escalations
```bash
curl http://localhost:8000/admin/escalations \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY"
```

### Acknowledge Escalation
```bash
curl -X POST http://localhost:8000/admin/escalations/1/acknowledge \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "support_agent_bob"}'
```

### Resolve Escalation (Resumes Bot)
```bash
curl -X POST http://localhost:8000/admin/escalations/1/resolve \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"note": "Called patient and booked appointment manually.", "actor": "support_agent_bob"}'
```

---

## 3. Delivery Monitoring & Stats

```bash
# Get 24-hour delivery statistics
curl http://localhost:8000/admin/stats/delivery?hours=24 \
  -H "X-Admin-API-Key: YOUR_ADMIN_KEY"
```
