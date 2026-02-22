# Relay — Fault-Tolerant Payment Webhook Orchestrator

Relay is a production-style backend middleware system that reliably delivers payment-related webhook notifications from internal payment services to external merchant endpoints. It is designed around a single core guarantee: **no payment event is ever lost**.

---

## Overview

When a payment is processed, downstream merchant systems need to be notified — an order must be fulfilled, a receipt must be sent, inventory must be updated. These notifications are called **webhooks**. If they fail silently, merchants are left in the dark.

Relay solves this by acting as a fault-tolerant middleware layer between the payment service and merchant endpoints. It accepts payment events, persists them immediately, and delivers them asynchronously with automatic retries and failure tracking.

**Core goals:**

- Never lose a payment event
- Never block the payment API on webhook delivery
- Survive crashes, retries, and traffic spikes
- Provide full observability into every event's state

---

## Architecture

```
Payment Service
      │
      ▼
┌─────────────────────┐
│  FastAPI (Port 8000) │  ◄── Ingress API
│  POST /events        │      Validates, persists, acknowledges
└──────────┬──────────┘
           │  writes to
           ▼
┌─────────────────────┐
│     PostgreSQL       │  ◄── Source of Truth
│   webhook_events     │      ACID, JSONB payloads, status tracking
└──────────┬──────────┘
           │  event ID pushed to
           ▼
┌─────────────────────┐
│       Redis          │  ◄── Message Broker
│   (Celery broker)    │      Lightweight queue, decouples API from workers
└──────────┬──────────┘
           │  consumed by
           ▼
┌─────────────────────┐
│   Celery Workers     │  ◄── Async Delivery Engine
│   deliver_webhook    │      Retries, backoff, dead-lettering
└──────────┬──────────┘
           │  HTTP POST to
           ▼
  Merchant Webhook Endpoint
```

All four services run in isolated Docker containers on a shared private network managed by Docker Compose.

---

## Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| API Framework | FastAPI | Ingress, validation, status endpoints |
| Database | PostgreSQL 15 | Persistent event storage, source of truth |
| Message Broker | Redis 7 | Task queue between API and workers |
| Worker Framework | Celery | Async delivery, retries, scheduling |
| HTTP Client | httpx | Outbound webhook delivery |
| ORM | SQLAlchemy | Database models and session management |
| Validation | Pydantic | Request/response schema enforcement |
| Containerization | Docker + Docker Compose | Reproducible environment |

---

## Project Structure

```
relay/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, endpoints, startup
│   ├── database.py       # SQLAlchemy engine, session, Base
│   ├── models.py         # WebhookEvent model, EventStatus enum
│   ├── schemas.py        # Pydantic request/response schemas
│   ├── celery_app.py     # Celery instance configuration
│   └── tasks.py          # deliver_webhook task with retry logic
├── docker-compose.yml    # All four services defined here
├── Dockerfile            # Python 3.11-slim image
├── requirements.txt      # Python dependencies
└── .env                  # Environment variables (not committed)
```

---

## Core System Behavior

**1. Ingestion**
A payment event arrives at `POST /events`. Relay validates it via Pydantic, writes it to PostgreSQL with `status = PENDING`, and immediately returns `HTTP 202 Accepted`. The API never waits for delivery to complete.

**2. Queuing**
After the database write, the event's UUID is pushed to Redis via Celery's `.delay()` call. This is a fire-and-forget operation from the API's perspective.

**3. Delivery**
A Celery worker picks up the event ID from Redis, fetches the full event from PostgreSQL, and makes an HTTP POST to the merchant's `target_url` with the payload and an idempotency header (`X-Relay-Event-Id`).

**4. Status Transitions**
Based on the merchant's response, the event status is updated:

| Response | Action |
|---|---|
| `2xx` | Status → `SUCCESS` |
| `4xx` | Status → `FAILED` (no retry) |
| `5xx` | Retry with exponential backoff |
| Timeout / Connection Error | Retry with exponential backoff |
| Max retries exceeded | Status → `DEAD_LETTER` |

**5. Observability**
Every state transition is recorded. Any event can be inspected at any time via `GET /events/{event_id}`.

---

## Event Lifecycle

```
Ingested
   │
   ▼
PENDING ──► [delivery attempt]
               │
       ┌───────┼───────┐
       ▼       ▼       ▼
    2xx      4xx     5xx / timeout
       │       │       │
       ▼       ▼       ▼
   SUCCESS  FAILED   retry (backoff)
                        │
                  max retries hit
                        │
                        ▼
                   DEAD_LETTER
```

---

## API Reference

### `POST /events`

Ingest a new payment event.

**Request Body**

```json
{
  "merchant_id": "merchant_abc",
  "event_type": "payment_succeeded",
  "payload": {
    "amount": 9900,
    "currency": "usd",
    "customer_id": "cus_123"
  },
  "target_url": "https://your-merchant-endpoint.com/webhooks"
}
```

| Field | Type | Description |
|---|---|---|
| `merchant_id` | string | Identifier for the merchant |
| `event_type` | string | Event name e.g. `payment_succeeded`, `refund_issued` |
| `payload` | object | Arbitrary event data, stored as JSONB |
| `target_url` | string | The merchant's webhook receiver URL |

**Response — `202 Accepted`**

```json
{
  "id": "a3f1c2e4-...",
  "status": "PENDING",
  "message": "Event received and queued for delivery"
}
```

---

### `GET /events/{event_id}`

Inspect the current state of any event.

**Response — `200 OK`**

```json
{
  "id": "a3f1c2e4-...",
  "merchant_id": "merchant_abc",
  "event_type": "payment_succeeded",
  "status": "SUCCESS",
  "attempts": 1,
  "target_url": "https://your-merchant-endpoint.com/webhooks",
  "created_at": "2026-02-22T10:00:00.000000",
  "updated_at": "2026-02-22T10:00:01.234567"
}
```

**Response — `404 Not Found`**

```json
{
  "detail": "Event not found"
}
```

---

### `GET /`

Health check endpoint.

**Response — `200 OK`**

```json
{
  "status": "ok",
  "service": "relay"
}
```

---

## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed
- [Docker Compose](https://docs.docker.com/compose/) available (`docker compose` or `docker-compose`)
- Git

### Clone the Repository

```bash
git clone https://github.com/Kundhave/Relay-A-Fault-Tolerant-Distributed-Webhook-Orchestrator.git
cd Relay-A-Fault-Tolerant-Distributed-Webhook-Orchestrator
```

### Configure Environment

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://relay_user:relay_pass@postgres:5432/relay_db
REDIS_URL=redis://redis:6379/0
POSTGRES_DB=relay_db
POSTGRES_USER=relay_user
POSTGRES_PASSWORD=relay_pass
```

> **Note:** The hostnames `postgres` and `redis` refer to Docker Compose service names, not `localhost`. Do not change these unless you also change the service names in `docker-compose.yml`.

---

## Running the Project

**Start all services:**

```bash
docker compose up --build
```

This starts four containers: `api`, `postgres`, `redis`, and `worker`. On first boot, the API's startup event automatically creates the `webhook_events` table in PostgreSQL.

**Run in detached mode (background):**

```bash
docker compose up --build -d
```

**Stop all services:**

```bash
docker compose down
```

**View logs for a specific service:**

```bash
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f postgres
```

**Confirm the API is running:**

```bash
curl http://localhost:8000/
# → {"status":"ok","service":"relay"}
```

**Access the interactive API docs:**

Open `http://localhost:8000/docs` in your browser. FastAPI auto-generates a full Swagger UI where you can fire requests directly.

---

## Testing the System

### Happy Path — Successful Delivery

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_id": "merchant_abc",
    "event_type": "payment_succeeded",
    "payload": {"amount": 9900, "currency": "usd"},
    "target_url": "https://httpbin.org/post"
  }'
```

Copy the `id` from the response and poll the status:

```bash
curl http://localhost:8000/events/<event-id>
```

Expected: `"status": "SUCCESS"`, `"attempts": 1`

---

### Failure Path — 4xx (No Retry)

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_id": "merchant_abc",
    "event_type": "payment_failed",
    "payload": {"amount": 5000},
    "target_url": "https://httpbin.org/status/400"
  }'
```

Expected: `"status": "FAILED"`, `"attempts": 1`

---

### Retry Path — 5xx (Exponential Backoff → DEAD_LETTER)

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_id": "merchant_abc",
    "event_type": "refund_issued",
    "payload": {"amount": 2000},
    "target_url": "https://httpbin.org/status/500"
  }'
```

Watch the worker retry in real time:

```bash
docker compose logs -f worker
```

Poll the status endpoint every few seconds. Attempts will increment. After 5 retries, expected: `"status": "DEAD_LETTER"`, `"attempts": 6`

---

### Inspect the Database Directly

```bash
docker compose exec postgres psql -U relay_user -d relay_db
```

```sql
-- View all events
SELECT id, merchant_id, event_type, status, attempts, created_at
FROM webhook_events
ORDER BY created_at DESC;

-- View only failed or dead-lettered events
SELECT id, event_type, status, attempts
FROM webhook_events
WHERE status IN ('FAILED', 'DEAD_LETTER');
```

---

## Resilience & Retry Logic

Relay implements **at-least-once delivery semantics** with the following retry strategy:

**Maximum retries:** 5

**Backoff formula:** `countdown = 2 ^ attempt_number` seconds

| Attempt | Delay Before Retry |
|---|---|
| 1st retry | 1 second |
| 2nd retry | 2 seconds |
| 3rd retry | 4 seconds |
| 4th retry | 8 seconds |
| 5th retry | 16 seconds |
| After 5th | → `DEAD_LETTER` |

**Why exponential backoff?** A `5xx` response typically means the merchant's server is struggling — hammering it with immediate retries makes the problem worse. Increasing delays give the server time to recover while still ensuring eventual delivery.

**Why not retry 4xx?** A `4xx` response means the merchant's server understood the request and explicitly rejected it. The URL may be wrong, authentication may be missing, or the request format is invalid. Retrying won't change the outcome — it should be flagged for manual investigation instead.

**Idempotency:** Every delivery attempt includes an `X-Relay-Event-Id` header containing the event's UUID. Merchant servers can use this to deduplicate events in the rare case a delivery succeeds but the status update fails, causing a retry.

---

## Database Schema

**Table: `webhook_events`**

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| `id` | UUID | No | `uuid4()` | Primary key |
| `merchant_id` | VARCHAR | No | — | Merchant identifier |
| `event_type` | VARCHAR | No | — | Event name |
| `payload` | JSON | No | — | Raw event data |
| `target_url` | VARCHAR | No | — | Merchant webhook URL |
| `status` | ENUM | No | `PENDING` | Current delivery state |
| `attempts` | INTEGER | No | `0` | Delivery attempt count |
| `created_at` | TIMESTAMP | No | `utcnow` | Ingestion time |
| `updated_at` | TIMESTAMP | No | `utcnow` | Last state change |

**EventStatus ENUM values:** `PENDING`, `SUCCESS`, `FAILED`, `DEAD_LETTER`

---

## Design Decisions

**Why write to PostgreSQL before pushing to Redis?**
If the API wrote to Redis first and then crashed before writing to PostgreSQL, the event would exist in the queue but have no persistent record. By writing to PostgreSQL first, the event is durable from the moment the API acknowledges it. Redis is treated as ephemeral — PostgreSQL is the truth.

**Why return 202 instead of 200?**
HTTP `200 OK` implies the request was fully processed. HTTP `202 Accepted` explicitly means "I received this and will handle it asynchronously." This is semantically accurate and signals to callers that delivery confirmation will come via a separate mechanism.

**Why pass only the event ID through Redis, not the full payload?**
Redis is a message broker, not a data store for large objects. Passing only the UUID keeps the queue lean, avoids serialization complexity, and means the worker always reads the freshest data from PostgreSQL rather than a potentially stale copy serialized at ingestion time.

**Why separate 4xx from 5xx in retry logic?**
These represent fundamentally different failure modes. A `5xx` is a transient infrastructure failure — retrying is the right response. A `4xx` is a permanent client-side error — retrying wastes resources and could mask a misconfiguration that needs human attention.

---

## Build History

This project was built incrementally in five commits, each representing a distinct layer of the system:

| Commit | Description |
|---|---|
| `feat: scaffold project foundation` | Docker Compose, Dockerfile, FastAPI health check |
| `feat: add persistence layer` | SQLAlchemy models, PostgreSQL integration, table auto-creation |
| `feat: add POST /events ingestion endpoint` | Pydantic schemas, 202 response, DB write |
| `feat: add async delivery engine` | Celery, Redis, outbound HTTP delivery, status transitions |
| `feat: add retry logic and dead-letter handling` | Exponential backoff, DEAD_LETTER, GET /events/{id} |

----
