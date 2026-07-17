#!/usr/bin/env python3
"""Prepare a browser ffmpeg.wasm splice spike fixture.

This script does not run ffmpeg.wasm. It creates a small review.json fixture and
prints the exact browser URL to open.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a browser splice spike fixture.")
    parser.add_argument("--audio-url", required=True, help="A signed/public audio URL.")
    parser.add_argument("--job-id", default="spike")
    parser.add_argument("--segments", nargs="+", default=["10:40", "60:95"])
    parser.add_argument("--out", default="data/spikes/browser-splice-fixture.json")
    args = parser.parse_args()

    ordered_segments = []
    for raw in args.segments:
        start, end = raw.split(":", 1)
        ordered_segments.append({"start": float(start), "end": float(end)})

    payload = {
        "job_id": args.job_id,
        "audio_url": args.audio_url,
        "ordered_segments": ordered_segments,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print("Open public/splice-spike.html and paste the fixture JSON.")


if __name__ == "__main__":
    main()
