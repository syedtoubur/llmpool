"""Fetch and construct one Hugging Face model catalogue entry."""

from fnmatch import fnmatch
from typing import Any

import httpx


HF_API = "https://huggingface.co/api"
REQUEST_TIMEOUT = 30
CTX_OVERHEAD_BYTES = 1.5 * 1024**3


def _license(info: dict[str, Any]) -> str | None:
    card_data = info.get("cardData") or {}
    if card_data.get("license"):
        return card_data["license"]
    for tag in info.get("tags") or []:
        if tag.startswith("license:"):
            return tag.removeprefix("license:")
    return None


def add_model(client: httpx.Client, spec: dict[str, Any]) -> dict[str, Any]:
    """Fetch *spec* from Hugging Face and return its catalogue entry."""
    repo = spec["repo"]
    try:
        tree = client.get(f"{HF_API}/models/{repo}/tree/main", timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ValueError(f"Could not fetch repository files for {repo}: {exc}") from exc
    if tree.status_code == 404:
        try:
            suggestions = client.get(f"{HF_API}/models", params={"search": repo}, timeout=REQUEST_TIMEOUT).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"Could not find repository suggestions for {repo}: {exc}") from exc
        names = [item.get("id", "?") for item in suggestions[:5]]
        raise ValueError(f"Repository not found: {repo}. Closest matches: {', '.join(names) or '(none)'}")
    try:
        tree.raise_for_status()
        files = tree.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError(f"Could not read repository files for {repo}: {exc}") from exc
    matches = [item for item in files if fnmatch(item.get("path", ""), spec["file_pattern"])]
    if not matches:
        names = [item.get("path", "?") for item in files if item.get("path", "").lower().endswith(".gguf")]
        raise ValueError(f"No file matching {spec['file_pattern']} in {repo}. GGUF files: {', '.join(names) or '(none)'}")
    if len(matches) > 1:
        matches.sort(key=lambda item: item["path"])
    file_info = matches[0]
    lfs = file_info.get("lfs") or {}
    if not lfs.get("oid"):
        raise ValueError(f"File {file_info['path']} in {repo} has no LFS SHA-256")
    try:
        metadata = client.get(f"{HF_API}/models/{repo}", timeout=REQUEST_TIMEOUT)
        metadata.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"Could not fetch metadata for {repo}: {exc}") from exc
    size = int(lfs.get("size", file_info.get("size", 0)))
    return {
        "model_id": spec["model_id"],
        "repo": repo,
        "file": file_info["path"],
        "sha256": lfs["oid"],
        "size_bytes": size,
        "license": _license(metadata.json()),
        "est_bytes_ctx8192": int(size + CTX_OVERHEAD_BYTES),
        "roles": spec["roles"],
    }
