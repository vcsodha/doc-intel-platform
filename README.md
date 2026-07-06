# 📄 Distributed AI Document Intelligence

![CI](https://github.com/vcsodha/doc-intel-platform/actions/workflows/ci.yml/badge.svg)

A polyglot, event-driven microservices platform that ingests unstructured
document images (receipts, invoices) and uses a local vision model to extract
**validated, structured financial data** — with per-field confidence scoring
and automated human-review routing so the system never silently trusts a bad
extraction.

Built to explore enterprise-style system design: a fast ingestion gateway
decoupled from resource-intensive AI processing by a persistent stream, at
least-once delivery with retries and a dead-letter queue, and a reactive
analytics layer feeding a live operations dashboard.

## ✨ Highlights

- 🚀 **High-throughput ingestion** — Go gateway accepts uploads and returns
  `202 Accepted` immediately (~200+ uploads/s locally), decoupled from the
  slower AI stage.
- 📡 **Redis Streams + consumer groups** — at-least-once delivery, explicit
  acknowledgements, and `XAUTOCLAIM`-based recovery of tasks orphaned by a
  crashed worker. Workers scale horizontally (`--scale python-worker=N`).
- 🔁 **Resilient processing** — exponential-backoff retries and a dead-letter
  queue for poison messages; sustained a ~90% task-success rate across a
  100-document concurrent load test.
- 🧠 **Confidence-scored extraction** — combines the model's per-field
  confidence with deterministic validators (amount parses, date parses, line
  items reconcile to the total) to route each document to `COMPLETED` or
  `NEEDS_REVIEW`. The review threshold is calibrated to the observed
  confidence distribution.
- 🧩 **Pluggable AI backend** — local Ollama/LLaVA by default, with a swappable
  hosted-model backend selectable via a single environment variable.
- 📊 **Analytics API + live dashboard** — Spring Boot aggregation endpoints
  (spend by vendor, monthly totals, anomaly flags, review queue) surfaced in a
  real-time, auto-refreshing operations console.
- 🐳 **Fully containerized** — one-command `docker compose up`, health checks,
  and a GitHub Actions CI pipeline building all three services and running
  unit tests.

## 🏗 Architecture

```
          upload (multipart)
                │
        ┌───────▼────────┐   XADD    ┌──────────────┐
        │  Go Gateway     ├──────────►│ Redis Stream │
        │  (ingestion)    │           │  + DLQ       │
        └───────┬─────────┘           └──────┬───────┘
                │ INSERT (QUEUED)             │ XREADGROUP (consumer group)
                │                             ▼
                │                    ┌──────────────────┐
                │                    │  Python Worker(s) │
                │                    │  OpenCV → Vision  │
                │                    │  AI → validate/   │
                │                    │  score → persist  │
                │                    └────────┬──────────┘
                ▼                             ▼
        ┌────────────────────────────────────────────┐
        │              PostgreSQL                     │
        │   documents (status, confidence, …)         │
        │   line_items                                │
        └───────┬─────────────────────────┬───────────┘
                │ read                     │ read
        ┌───────▼─────────┐       ┌────────▼──────────┐
        │ Go /metrics     │       │ Spring Analytics   │
        │ (success rate)  │       │ (aggregations)     │
        └───────┬─────────┘       └────────┬───────────┘
                └──────────────┬───────────┘
                               ▼
                     ┌───────────────────┐
                     │  Dashboard (JS)   │
                     │  live, auto-poll  │
                     └───────────────────┘
```

**Document lifecycle:** `QUEUED → PROCESSING → COMPLETED | NEEDS_REVIEW | FAILED`

## 🧩 Services

| Service | Language | Role |
|---|---|---|
| `go-gateway` | Go | Multipart ingestion, writes `QUEUED` row, publishes to the stream, exposes live `/metrics` and `/healthz`. |
| `python-worker` | Python | Consumes the stream, OpenCV cleanup, vision-model extraction, confidence scoring, retries/DLQ, persistence. |
| `spring-analytics` | Java (Spring Boot) | Read-only aggregation API: documents, review queue, spend-by-vendor, monthly totals, anomalies. |
| `dashboard.html` | Vanilla JS | Live operations console: metrics, charts, review queue, upload. |

## 🛠 Tech Stack

**Backend:** Go, Python, Java / Spring Boot
**AI & data:** Ollama / LLaVA (vision), OpenCV, Redis Streams, PostgreSQL
**Frontend:** HTML5, Vanilla JavaScript, Chart.js
**DevOps:** Docker, Docker Compose, GitHub Actions

## 🚀 Getting Started

**Prerequisites:** Docker + Docker Compose, and [Ollama](https://ollama.com)
running on the host with the vision model pulled:

```bash
ollama pull llava
```

**Run the stack:**

```bash
docker compose up -d --build
```

Open `dashboard.html` in a browser to upload documents and watch them flow
through the pipeline in real time.

> **Using host Ollama on Docker Desktop?** Point the worker at your host with a
> local `docker-compose.override.yml` (gitignored) setting
> `OLLAMA_URL=http://host.docker.internal:11434`.

**Load test** (produces the measured success-rate number):

```bash
pip install requests pillow
python loadtest/loadtest.py --n 100 --concurrency 10 --bad-ratio 0.08 --degraded-ratio 0.2
```

## 🔌 Configuration

Key environment variables (see `docker-compose.yml`):

| Variable | Default | Purpose |
|---|---|---|
| `AI_BACKEND` | `ollama` | Vision backend (`ollama` or a hosted model). |
| `CONFIDENCE_THRESHOLD` | `0.75` | Below this → `NEEDS_REVIEW`. |
| `MAX_ATTEMPTS` | `3` | Retries before dead-lettering. |

## 🔮 Roadmap

- Human-in-the-loop review UI to correct and resolve flagged documents
- Subtotal/tax reconciliation to catch confident under-reads
- Server-Sent Events for push updates (replacing dashboard polling)

## 🧑‍💻 Author

**Vidisha Sodha** — Software / AI Engineer
Built as a hands-on exploration of distributed systems, local AI integration,
and full-stack engineering.