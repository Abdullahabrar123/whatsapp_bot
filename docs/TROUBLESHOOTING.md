# Troubleshooting & Diagnostics Runbook

## 1. Common Issues & Solutions

### Issue: Meta Webhook Verification Fails (HTTP 403)
- **Cause**: Verify token in Meta Developer Portal does not match `META_WEBHOOK_VERIFY_TOKEN` in `.env`.
- **Solution**: Confirm both strings match exactly. Ensure `hub.mode=subscribe`.

### Issue: Inbound Messages Rejected with "Invalid Signature"
- **Cause**: `META_APP_SECRET` in `.env` is incorrect or missing, or reverse proxy modified the raw request body.
- **Solution**: Check App Secret from App Settings -> Basic. Ensure reverse proxy passes raw binary body without decompression or mutation.

### Issue: Ollama Model Unreachable or Slow
- **Cause**: Ollama container not running or model not pulled.
- **Solution**: Run `ollama pull qwen3:4b`. Verify connectivity with `curl http://localhost:11434/api/tags`. The bot will automatically use `DeterministicClient` while Ollama is offline.

### Issue: "Re-engagement Required" Meta Error (131047)
- **Cause**: Attempted to send a free-form message outside Meta's 24-hour service window.
- **Solution**: Send an approved template message instead (`appointment_reminder_v1`).

---

## 2. Diagnostics Commands

```bash
# Check configuration health
python scripts/validate_config.py

# Check API readiness
curl http://localhost:8000/health/ready

# Check Prometheus metrics
curl http://localhost:8000/metrics

# Check open escalations
curl http://localhost:8000/admin/escalations -H "X-Admin-API-Key: YOUR_KEY"
```
