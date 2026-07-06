#!/usr/bin/env python3

import argparse
import io
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def degrade(sample_bytes: bytes) -> bytes:
    """A hard-to-read copy: downscaled, blurred, low-quality JPEG.

    Still a valid image, but legible enough only in patches — which makes the
    model return partial / low-confidence data and exercises the NEEDS_REVIEW
    path, the way a bad phone photo of a crumpled receipt would.
    """
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        sys.exit("--degraded-ratio needs Pillow: pip3 install pillow")
    img = Image.open(io.BytesIO(sample_bytes)).convert("RGB")
    w, h = img.size
    img = img.resize((max(1, int(w * 0.18)), max(1, int(h * 0.18))))
    img = img.filter(ImageFilter.GaussianBlur(3.5))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=12)
    return out.getvalue()


def build_payload(sample_bytes: bytes, kind: str):
    """Return (bytes, name) for a clean, degraded, or corrupt upload."""
    if kind == "corrupt":
        return os.urandom(2048), "corrupt.jpg"
    if kind == "degraded":
        return degrade(sample_bytes), "degraded.jpg"
    # Append a few random bytes so each upload is byte-distinct (avoids any
    # accidental dedup) while staying a valid image.
    return sample_bytes + os.urandom(8), "receipt.jpg"


def upload(base_url: str, data: bytes, name: str):
    try:
        r = requests.post(
            f"{base_url}/api/v1/upload",
            files={"document": (name, io.BytesIO(data), "image/jpeg")},
            timeout=30,
        )
        return r.status_code == 202
    except requests.RequestException:
        return False


def get_metrics(base_url: str):
    try:
        return requests.get(f"{base_url}/api/v1/metrics", timeout=5).json()
    except requests.RequestException:
        return {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--bad-ratio", type=float, default=0.1,
                   help="fraction of uploads that are corrupt (-> DLQ)")
    p.add_argument("--degraded-ratio", type=float, default=0.0,
                   help="fraction that are blurred/low-quality (-> NEEDS_REVIEW)")
    p.add_argument("--sample", default="../real_receipt.jpg")
    p.add_argument("--timeout", type=int, default=600,
                   help="seconds to wait for tasks to drain")
    args = p.parse_args()

    if not os.path.exists(args.sample):
        sys.exit(f"sample image not found: {args.sample}")
    sample = open(args.sample, "rb").read()

    def classify(r):
        if r < args.bad_ratio:
            return "corrupt"
        if r < args.bad_ratio + args.degraded_ratio:
            return "degraded"
        return "clean"

    jobs = [classify(random.random()) for _ in range(args.n)]
    n_corrupt = jobs.count("corrupt")
    n_degraded = jobs.count("degraded")
    print(f"Uploading {args.n} docs "
          f"({n_corrupt} corrupt, {n_degraded} degraded) "
          f"at concurrency {args.concurrency}...")

    # The gateway's counters are cumulative across runs, so we snapshot them
    # up front and report only the delta this run produced. That makes the
    # test repeatable without a manual redis FLUSHALL between runs.
    def snapshot():
        m = get_metrics(args.url)
        return {k: m.get(k, 0) for k in
                ("completed", "needs_review", "failed", "retried")}

    base = snapshot()

    def terminal_count(s):
        return s["completed"] + s["needs_review"] + s["failed"]

    start = time.time()
    accepted = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(upload, args.url, *build_payload(sample, c))
                   for c in jobs]
        for f in as_completed(futures):
            accepted += 1 if f.result() else 0
    elapsed = time.time() - start
    print(f"Accepted {accepted}/{args.n} in {elapsed:.1f}s "
          f"({accepted / elapsed:.1f} uploads/s)")

    print("Waiting for workers to drain the queue...")
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        done = terminal_count(snapshot()) - terminal_count(base)
        if done >= accepted:
            break
        print(f"  ...{done}/{accepted} done", end="\r")
        time.sleep(3)

    cur = snapshot()
    d = {k: cur[k] - base[k] for k in cur}
    terminal = d["completed"] + d["needs_review"] + d["failed"]
    rate = (d["completed"] + d["needs_review"]) / terminal if terminal else 1.0
    print("\n── Results (this run) " + "─" * 29)
    print(f"  completed     : {d['completed']}")
    print(f"  needs_review  : {d['needs_review']}")
    print(f"  failed (DLQ)  : {d['failed']}")
    print(f"  retried       : {d['retried']}")
    print(f"  success rate  : {rate * 100:.1f}%")


if __name__ == "__main__":
    main()