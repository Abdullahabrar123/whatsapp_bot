# Production Deployment & Hosting Runbook

## 1. Production Architecture (Docker Compose)

The standard production deployment contains four services:

- **`app`**: FastAPI ASGI API application (serves Webhooks, Health, Operator APIs).
- **`worker`**: Arq background worker (processes webhooks asynchronously, executes outbound campaigns, prunes stale data).
- **`postgres`**: PostgreSQL 16 relational database storing contacts, conversations, messages, consent records, and campaigns.
- **`redis`**: Redis 7 queue broker for arq task queue and caching.
- **`ollama`**: Local Ollama container serving `qwen3:4b`.

---

## 2. Deployment Steps

### Step 1: Clone Repository on Server
```bash
git clone https://github.com/your-org/whatsapp_chat.git
cd whatsapp_chat
```

### Step 2: Configure Environment
```bash
cp .env.example .env
chmod 600 .env
```
Edit `.env` and configure:
```env
APP_ENV=production
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/wa_bot
REDIS_URL=redis://redis:6379/0
OLLAMA_BASE_URL=http://ollama:11434
APP_SECRET_KEY=<generate_random_32_chars>
ADMIN_API_KEY=<generate_random_24_chars>
META_WABA_ID=123456789
META_PHONE_NUMBER_ID=987654321
META_ACCESS_TOKEN=EAAB...
META_APP_SECRET=abcdef...
META_WEBHOOK_VERIFY_TOKEN=my_secure_verify_token
```

### Step 3: Start Services
```bash
docker compose up -d
```

### Step 4: Pull Ollama Model
```bash
docker compose exec ollama ollama pull qwen3:4b
```

### Step 5: Run Database Migrations
```bash
docker compose exec app alembic upgrade head
```

### Step 6: Verify Health
```bash
curl -f http://localhost:8000/health/ready
```

---

## 3. Reverse Proxy & HTTPS Configuration (Caddy / Nginx)

Meta requires webhooks to be delivered via HTTPS with a valid TLS certificate.

### Example Caddyfile:
```caddy
your-bot.example.com {
    reverse_proxy localhost:8000
    encode gzip
}
```
