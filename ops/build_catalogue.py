"""Build the checked-in model catalogue from Hugging Face metadata."""

import argparse
import json
from pathlib import Path

import httpx

from .add_model import add_model


WANTED_MODELS = [
    {"model_id": "qwen3.5-9b-q4km", "repo": "bartowski/Qwen_Qwen3.5-9B-GGUF", "file_pattern": "*Q4_K_M.gguf", "roles": ["orchestrator", "worker"]},
    {"model_id": "qwen3.5-4b-q4km", "repo": "bartowski/Qwen_Qwen3.5-4B-GGUF", "file_pattern": "*Q4_K_M.gguf", "roles": ["worker", "background"]},
    {"model_id": "qwen3.5-2b-q4km", "repo": "bartowski/Qwen_Qwen3.5-2B-GGUF", "file_pattern": "*Q4_K_M.gguf", "roles": ["background"]},
]


def build_catalogue(client: httpx.Client) -> dict:
    return {"version": "1", "models": [add_model(client, spec) for spec in WANTED_MODELS]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=30) as client:
            catalogue = build_catalogue(client)
    except (httpx.HTTPError, ValueError) as exc:
        raise SystemExit(f"Catalogue build failed: {exc}") from exc
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalogue['models'])} models to {args.out}")


if __name__ == "__main__":
    main()
