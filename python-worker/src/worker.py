"""
AI Document Intelligence — processing worker.

Reads tasks from a Redis Stream using a consumer group (at-least-once
delivery, not a fire-and-forget list pop), runs OpenCV cleanup + a local
LLaVA extraction, scores the result, and writes a final state to Postgres.

Design notes
------------
* Stream + consumer group  -> multiple worker replicas share the load and
  every message is explicitly acknowledged. Run more workers with:
      docker compose up -d --scale python-worker=3
* Crash recovery           -> XAUTOCLAIM re-delivers messages that a dead
  worker picked up but never acked.
* Bounded retries          -> transient failures are re-queued with
  exponential backoff up to MAX_ATTEMPTS, then dead-lettered.
* Confidence scoring        -> deterministic validators + the model's own
  per-field confidence decide COMPLETED vs NEEDS_REVIEW, so we never
  silently trust a bad extraction.
"""

import os
import re
import json
import time
import base64
import signal
import socket
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

import cv2
import redis
import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("DocWorker")


REDIS_HOST   = os.getenv("REDIS_HOST", "redis")
REDIS_PORT   = int(os.getenv("REDIS_PORT", "6379"))
DB_HOST      = os.getenv("DB_HOST", "postgres")
DB_NAME      = os.getenv("DB_NAME", "doc_intel")
DB_USER      = os.getenv("DB_USER", "admin")
DB_PASS      = os.getenv("DB_PASSWORD", "password")
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llava")

AI_BACKEND     = os.getenv("AI_BACKEND", "ollama").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL     = "https://generativelanguage.googleapis.com/v1beta/models"

STREAM       = os.getenv("STREAM_KEY", "documents:stream")
DLQ_STREAM   = os.getenv("DLQ_STREAM", "documents:dlq")
GROUP        = os.getenv("CONSUMER_GROUP", "workers")
CONSUMER     = f"{socket.gethostname()}-{os.getpid()}"

MAX_ATTEMPTS         = int(os.getenv("MAX_ATTEMPTS", "3"))
BACKOFF_BASE_SEC     = float(os.getenv("BACKOFF_BASE_SEC", "2"))
BACKOFF_CAP_SEC      = float(os.getenv("BACKOFF_CAP_SEC", "30"))
BLOCK_MS             = int(os.getenv("BLOCK_MS", "5000"))
CLAIM_IDLE_MS        = int(os.getenv("CLAIM_IDLE_MS", "60000"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.6"))
LINE_ITEM_TOLERANCE  = float(os.getenv("LINE_ITEM_TOLERANCE", "0.05"))  # 5%


M_COMPLETED     = "metrics:completed"
M_NEEDS_REVIEW  = "metrics:needs_review"
M_FAILED        = "metrics:failed"
M_RETRIED       = "metrics:retried"
M_DEAD_LETTERED = "metrics:dead_lettered"

EXTRACTION_PROMPT = (
    "You are a precise receipt/invoice data extractor. Read the image "
    "carefully and respond with ONLY this JSON object and no other text:\n"
    "{\n"
    '  "vendor_name": "<store name>",\n'
    '  "total_amount": 0.00,\n'
    '  "date": "YYYY-MM-DD",\n'
    '  "line_items": [{"description": "<item>", "amount": 0.00}],\n'
    '  "field_confidence": {"vendor_name": 0.0, "total_amount": 0.0, "date": 0.0}\n'
    "}\n"
    "Use null only when a value is truly not visible. Do not guess or invent. "
    "field_confidence (0-1) is how clearly you could read each field."
)


class TransientError(Exception):
    """Recoverable failure (model timeout, DB blip) -> retry."""


class DocumentProcessor:
    def __init__(self):
        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True
        )
        self._running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

    def _stop(self, *_):
        logger.info("Shutdown signal received; finishing current task...")
        self._running = False

  
    def _db(self):
        return psycopg2.connect(
            host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS
        )

    def ensure_group(self):
        """Create the consumer group; ignore if it already exists."""
        try:
            self.redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
            logger.info("Created consumer group '%s' on '%s'", GROUP, STREAM)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def preprocess_image(self, filepath: str) -> None:
        """Light cleanup for a vision LLM.

        Unlike classic OCR, a multimodal model reads a natural image better
        than a hard-binarized one, so we stay gentle: confirm it decodes
        (this is also what flags corrupt uploads for the DLQ) and upscale
        small scans so faint text is legible.
        """
        img = cv2.imread(filepath)
        if img is None:
            raise TransientError(f"OpenCV could not read image: {filepath}")
        h, w = img.shape[:2]
        if max(h, w) < 1200:
            scale = 1200 / max(h, w)
            img = cv2.resize(img, None, fx=scale, fy=scale,
                             interpolation=cv2.INTER_CUBIC)
            cv2.imwrite(filepath, img)

    def extract_with_llava(self, filepath: str) -> dict:
        with open(filepath, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "model": OLLAMA_MODEL,
            "prompt": EXTRACTION_PROMPT,
            "images": [encoded],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/generate", json=payload, timeout=120
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            
            raise TransientError(f"Ollama request failed: {e}") from e

        raw = resp.json().get("response", "{}")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            
            raise TransientError(f"Model returned non-JSON: {e}") from e

    def extract(self, filepath: str) -> dict:
        """Route extraction to the configured vision backend."""
        if AI_BACKEND == "gemini":
            return self.extract_with_gemini(filepath)
        return self.extract_with_llava(filepath)

    def extract_with_gemini(self, filepath: str) -> dict:
        if not GEMINI_API_KEY:
            raise TransientError("GEMINI_API_KEY is not set")
        with open(filepath, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        mime = "image/png" if filepath.lower().endswith(".png") else "image/jpeg"
        url = f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent"
        body = {
            "contents": [{
                "parts": [
                    {"text": EXTRACTION_PROMPT},
                    {"inline_data": {"mime_type": mime, "data": encoded}},
                ]
            }],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        }
        try:
            resp = requests.post(
                url, json=body, timeout=120,
                headers={"x-goog-api-key": GEMINI_API_KEY},
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            status = getattr(e.response, "status_code", "?")
            raise TransientError(f"Gemini request failed: HTTP {status}") from e
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            return json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
            raise TransientError(f"Gemini returned unexpected payload: {e}") from e

    @staticmethod
    def _parse_amount(value):
        if value is None:
            return None
        try:
            cleaned = re.sub(r"[^0-9.\-]", "", str(value))
            return Decimal(cleaned) if cleaned else None
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%B %d, %Y"):
            try:
                return datetime.strptime(str(value).strip(), fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _first(source: dict, *keys):
        """Return the first present, non-empty value among candidate keys."""
        if not isinstance(source, dict):
            return None
        for k in keys:
            v = source.get(k)
            if v not in (None, "", []):
                return v
        return None

    PLACEHOLDER_MARKERS = (
        "exactly as printed", "yyyy-mm-dd", "plain number",
        "store name", "company name", "item name", "vendor name", "<",
    )

    @classmethod
    def _clean_value(cls, value):
        """Normalize a model value to a real string, or None if it is empty
        or an echoed prompt placeholder."""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if any(m in text.lower() for m in cls.PLACEHOLDER_MARKERS):
            return None
        return text

    def score(self, data: dict) -> dict:
        """Turn a raw model payload into a graded, normalized result."""
        reasons = []

        vendor = self._clean_value(self._first(
            data, "vendor_name", "vendor", "merchant", "merchant_name",
            "store", "store_name", "company"))
        total = self._parse_amount(self._first(
            data, "total_amount", "total", "amount", "grand_total",
            "total_due", "amount_due"))
        doc_dt = self._parse_date(self._first(
            data, "date", "transaction_date", "invoice_date", "purchase_date"))

        line_items = []
        for item in (self._first(data, "line_items", "items", "lineItems",
                                 "products") or []):
            if not isinstance(item, dict):
                continue
            amt = self._parse_amount(self._first(item, "amount", "price", "total"))
            line_items.append({
                "description": (self._clean_value(self._first(
                    item, "description", "name", "item")) or "")[:255],
                "amount": amt,
            })

        if not vendor:
            reasons.append("vendor_name missing or empty")
        if total is None or total <= 0:
            reasons.append("total_amount missing or not a positive number")
        if doc_dt is None:
            reasons.append("date missing or unparseable")
        if doc_dt and doc_dt > date.today():
            reasons.append("date is in the future")


        priced = [li["amount"] for li in line_items if li["amount"] is not None]
        if priced and total and total > 0:
            line_sum = sum(priced)
            if line_sum > total * (1 + Decimal(str(LINE_ITEM_TOLERANCE))):
                reasons.append(
                    f"line items ({line_sum}) exceed total ({total})"
                )

        fc = self._first(data, "field_confidence", "confidence", "confidences")
        fc = fc if isinstance(fc, dict) else {}
        conf = {k: self._clamp(fc.get(k)) for k in ("vendor_name", "total_amount", "date")}
        model_conf = sum(conf.values()) / len(conf)

        overall = max(0.0, model_conf - 0.2 * len(reasons))

        if reasons or overall < CONFIDENCE_THRESHOLD:
            status = "NEEDS_REVIEW"
            if not reasons:
                reasons.append(f"overall confidence {overall:.2f} below threshold")
        else:
            status = "COMPLETED"

        return {
            "status": status,
            "vendor_name": vendor,
            "total_amount": total,
            "doc_date": doc_dt,
            "line_items": line_items,
            "confidence": conf,
            "overall_confidence": round(overall, 3),
            "review_reasons": reasons,
        }

    @staticmethod
    def _clamp(v):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5


    def mark_processing(self, task_id: str, filename: str, attempts: int):
        with self._db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (task_id, filename, status, attempts)
                VALUES (%s, %s, 'PROCESSING', %s)
                ON CONFLICT (task_id) DO UPDATE
                  SET status = 'PROCESSING',
                      attempts = EXCLUDED.attempts,
                      updated_at = now()
                """,
                (task_id, filename, attempts),
            )

    def persist_result(self, task_id: str, filename: str, attempts: int,
                       raw: dict, graded: dict):
        with self._db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (
                    task_id, filename, status, attempts, vendor_name,
                    total_amount, doc_date, structured_data, confidence,
                    overall_confidence, review_reasons, error_message, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,now())
                ON CONFLICT (task_id) DO UPDATE SET
                    status             = EXCLUDED.status,
                    attempts           = EXCLUDED.attempts,
                    vendor_name        = EXCLUDED.vendor_name,
                    total_amount       = EXCLUDED.total_amount,
                    doc_date           = EXCLUDED.doc_date,
                    structured_data    = EXCLUDED.structured_data,
                    confidence         = EXCLUDED.confidence,
                    overall_confidence = EXCLUDED.overall_confidence,
                    review_reasons     = EXCLUDED.review_reasons,
                    error_message      = NULL,
                    updated_at         = now()
                """,
                (
                    task_id, filename, graded["status"], attempts,
                    graded["vendor_name"], graded["total_amount"],
                    graded["doc_date"], json.dumps(raw),
                    json.dumps(graded["confidence"]),
                    graded["overall_confidence"], graded["review_reasons"],
                ),
            )
            cur.execute("DELETE FROM line_items WHERE task_id = %s", (task_id,))
            rows = [
                (task_id, li["description"], li["amount"])
                for li in graded["line_items"]
            ]
            if rows:
                psycopg2.extras.execute_values(
                    cur,
                    "INSERT INTO line_items (task_id, description, amount) VALUES %s",
                    rows,
                )

    def mark_failed(self, task_id: str, filename: str, attempts: int, error: str):
        with self._db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (task_id, filename, status, attempts, error_message)
                VALUES (%s, %s, 'FAILED', %s, %s)
                ON CONFLICT (task_id) DO UPDATE
                  SET status = 'FAILED',
                      attempts = EXCLUDED.attempts,
                      error_message = EXCLUDED.error_message,
                      updated_at = now()
                """,
                (task_id, filename, attempts, error[:2000]),
            )


    def process(self, fields: dict):
        task_id  = fields["task_id"]
        filepath = fields["filepath"]
        filename = fields.get("filename") or os.path.basename(filepath)
        attempts = int(fields.get("attempts", "0")) + 1

        self.mark_processing(task_id, filename, attempts)
        logger.info("Processing %s (attempt %d/%d)", task_id, attempts, MAX_ATTEMPTS)

        if filepath.lower().endswith((".png", ".jpg", ".jpeg")):
            self.preprocess_image(filepath)

        raw = self.extract(filepath)
        graded = self.score(raw)
        self.persist_result(task_id, filename, attempts, raw, graded)

        counter = M_COMPLETED if graded["status"] == "COMPLETED" else M_NEEDS_REVIEW
        self.redis.incr(counter)
        logger.info(
            "%s -> %s (confidence %.2f)",
            task_id, graded["status"], graded["overall_confidence"],
        )

    def handle(self, msg_id: str, fields: dict):
        attempts = int(fields.get("attempts", "0")) + 1
        try:
            self.process(fields)
            self.redis.xack(STREAM, GROUP, msg_id)
        except Exception as e:  
            transient = isinstance(e, TransientError)
            logger.warning("Task %s failed (attempt %d): %s",
                           fields.get("task_id"), attempts, e)

            if transient and attempts < MAX_ATTEMPTS:
                delay = min(BACKOFF_BASE_SEC * (2 ** (attempts - 1)), BACKOFF_CAP_SEC)
                logger.info("Retrying in %.1fs", delay)
                time.sleep(delay)
                requeue = dict(fields)
                requeue["attempts"] = str(attempts)
                self.redis.xadd(STREAM, requeue)
                self.redis.incr(M_RETRIED)
            else:
        
                self.dead_letter(fields, attempts, str(e))
            
            self.redis.xack(STREAM, GROUP, msg_id)

    def dead_letter(self, fields: dict, attempts: int, error: str):
        task_id  = fields.get("task_id", "unknown")
        filename = fields.get("filename") or os.path.basename(
            fields.get("filepath", "")
        )
        self.mark_failed(task_id, filename, attempts, error)
        self.redis.xadd(DLQ_STREAM, {
            "task_id": task_id,
            "filepath": fields.get("filepath", ""),
            "attempts": str(attempts),
            "error": error[:500],
        })
        self.redis.incr(M_FAILED)
        self.redis.incr(M_DEAD_LETTERED)
        logger.error("Dead-lettered %s after %d attempts", task_id, attempts)

    def reclaim_stale(self):
        """Re-deliver messages a crashed worker never acked."""
        try:
            _, claimed, _ = self.redis.xautoclaim(
                STREAM, GROUP, CONSUMER, min_idle_time=CLAIM_IDLE_MS,
                start_id="0-0", count=10,
            )
        except redis.ResponseError:
            return
        for msg_id, fields in claimed:
            logger.info("Reclaimed stale task %s", msg_id)
            self.handle(msg_id, fields)

    
    def run(self):
        self.ensure_group()
        logger.info("AI backend: %s", AI_BACKEND)
        logger.info("Worker '%s' listening on stream '%s'", CONSUMER, STREAM)
        while self._running:
            self.reclaim_stale()
            try:
                resp = self.redis.xreadgroup(
                    GROUP, CONSUMER, {STREAM: ">"}, count=1, block=BLOCK_MS
                )
            except redis.RedisError as e:
                logger.error("Redis read error: %s", e)
                time.sleep(2)
                continue
            if not resp:
                continue
            for _stream, messages in resp:
                for msg_id, fields in messages:
                    self.handle(msg_id, fields)
        logger.info("Worker stopped cleanly.")


def _wait_for_deps():
    """Block until Redis answers, so the first read doesn't crash on boot."""
    for _ in range(30):
        try:
            redis.Redis(host=REDIS_HOST, port=REDIS_PORT).ping()
            return
        except redis.RedisError:
            time.sleep(2)
    logger.warning("Proceeding without confirmed Redis connection.")


if __name__ == "__main__":
    _wait_for_deps()
    DocumentProcessor().run()