"""Offline-testable verification harness for a local llama.cpp Windows build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
import tempfile
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Callable

import httpx

REPO = "ggml-org/llama.cpp"
API = f"https://api.github.com/repos/{REPO}/releases"

BUILD_PATTERNS = {
    "cuda12": [r"llama-(?P<tag>b\d+)-bin-win-cuda-12\.\d+-x64\.zip", r"cudart-llama-bin-win-cuda-12\.\d+-x64\.zip"],
    "cuda13": [r"llama-(?P<tag>b\d+)-bin-win-cuda-13\.\d+-x64\.zip", r"cudart-llama-bin-win-cuda-13\.\d+-x64\.zip"],
    "vulkan": [r"llama-(?P<tag>b\d+)-bin-win-vulkan-x64\.zip"],
    "cpu": [r"llama-(?P<tag>b\d+)-bin-win-cpu-x64\.zip"],
}


def select_assets(assets: list[dict[str, Any]], build: str, tag: str) -> list[dict[str, Any]]:
    """Return build asset(s), matching CUDA minor versions without pinning them."""
    if build not in BUILD_PATTERNS:
        raise ValueError(f"Unsupported build: {build}")
    patterns = BUILD_PATTERNS[build]
    selected = []
    for index, pattern in enumerate(patterns):
        regex = re.compile(pattern, re.IGNORECASE)
        matches = [a for a in assets if regex.fullmatch(a.get("name", "")) and (index or a["name"].startswith(f"llama-{tag}-"))]
        if not matches:
            raise ValueError(f"Release asset not found for {build}: {pattern}")
        selected.append(matches[0])
    return selected


def check_gguf(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.read(4) == b"GGUF"


def contains_canary(log: str | Path, canary: str) -> bool:
    if isinstance(log, Path):
        try:
            return canary in log.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return False
    return canary in log


def parse_tool_call(payload: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    try:
        call = payload["choices"][0]["message"]["tool_calls"][0]["function"]
        if call["name"] != "get_weather":
            return False, None
        args = json.loads(call["arguments"])
        return isinstance(args, dict) and "city" in args, args
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return False, None


def server_command(exe: Path, model: Path, port: int, build: str, extra: list[str] | None = None) -> list[str]:
    args = [str(exe), "--host", "127.0.0.1", "--port", str(port), "-m", str(model), "-c", "8192", "-np", "1", "--no-webui", "--no-slots", "--jinja"]
    if build == "cpu":
        args += ["-ngl", "0"]
    return args + (extra or [])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and ((parsed.hostname == "github.com" and parsed.path.startswith(f"/{REPO}/releases/download/")) or parsed.hostname == "objects.githubusercontent.com")


def _safe_extract(archive_path: Path, install_dir: Path) -> None:
    install_dir = install_dir.resolve()
    with tempfile.TemporaryDirectory(dir=install_dir.parent) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                name = member.filename
                if Path(name).is_absolute() or re.match(r"^[A-Za-z]:", name) or name.startswith(("\\\\", "/")):
                    raise ValueError(f"Unsafe archive member: {name}")
                target = (staging / name).resolve()
                if not target.is_relative_to(staging.resolve()):
                    raise ValueError(f"Unsafe archive member: {name}")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"Symlink archive member: {name}")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
        for child in staging.iterdir():
            destination = install_dir / child.name
            if destination.exists():
                shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
            shutil.move(str(child), str(destination))


class LlamaCheck:
    def __init__(self, *, http: Any = httpx, run: Callable = subprocess.run, popen: Callable = subprocess.Popen,
                 sleep: Callable = time.sleep, clock: Callable = time.monotonic, connections: Callable | None = None):
        self.http, self.run, self.popen, self.sleep, self.clock = http, run, popen, sleep, clock
        self.connections = connections
        self.report: dict[str, Any] = {"schema_version": 1, "steps": []}

    def record(self, name: str, status: str, **details: Any) -> dict[str, Any]:
        step = {"name": name, "status": status, "details": details}
        self.report["steps"].append(step)
        return step

    def _get_release(self, tag: str) -> dict[str, Any]:
        url = f"{API}/tags/{tag}" if tag != "latest" else f"{API}/latest"
        response = self.http.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        return response.json()

    def run_all(self, *, build: str, tag: str, model: Path, bin_dir: Path, port: int, out: Path) -> dict[str, Any]:
        self.report.update({"host": socket.gethostname(), "build": build, "model": str(model), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        release = self._get_release(tag)
        actual_tag = release.get("tag_name", tag)
        assets = select_assets(release.get("assets", []), build, actual_tag)
        install_dir = bin_dir / actual_tag / build
        install_dir.mkdir(parents=True, exist_ok=True)
        zips: list[Path] = []
        attestations, hashes = [], {}
        gh_missing = shutil.which("gh") is None
        for asset in assets:
            dest = install_dir / asset["name"]
            url = asset.get("browser_download_url", "")
            if not _download_url_allowed(url):
                self.record("download", "fail", file=dest.name, reason="disallowed URL")
                raise ValueError(f"Disallowed download URL: {url}")
            if not dest.exists():
                partial = dest.with_name(dest.name + ".partial")
                with self.http.stream("GET", url, timeout=httpx.Timeout(30, read=120), follow_redirects=True) as response:
                    response.raise_for_status()
                    with partial.open("wb") as f:
                        for chunk in response.iter_bytes():
                            f.write(chunk)
                if partial.stat().st_size != asset.get("size"):
                    partial.unlink(missing_ok=True)
                    self.record("download", "fail", file=dest.name, reason="size mismatch")
                    raise ValueError(f"Downloaded size mismatch: {dest.name}")
                partial.replace(dest)
            if dest.stat().st_size != asset.get("size"):
                self.record("download", "fail", file=dest.name, reason="size mismatch")
                raise ValueError(f"Downloaded size mismatch: {dest.name}")
            zips.append(dest)
            hashes[dest.name] = _sha256(dest)
            try:
                result = None if gh_missing else self.run(["gh", "attestation", "verify", str(dest), "--repo", REPO], capture_output=True, text=True)
            except FileNotFoundError:
                gh_missing, result = True, None
            attestations.append({"file": dest.name, "returncode": result.returncode if result else None, "stdout": result.stdout if result else "", "stderr": result.stderr if result else "gh missing"})
        provenance_ok = not gh_missing and all(x["returncode"] == 0 for x in attestations)
        self.record("provenance", "pass" if provenance_ok else "fail", attestations=attestations, sha256=hashes)
        if not provenance_ok:
            raise ValueError("Archive attestation verification failed")
        for archive in zips:
            _safe_extract(archive, install_dir)
        self.record("release", "pass", tag=actual_tag, assets=[p.name for p in zips], directory=str(install_dir))

        exe = install_dir / "llama-server.exe"
        sig = self.run(["powershell", "-NoProfile", "-Command", "param($p) (Get-AuthenticodeSignature -LiteralPath $p).Status", "-args", str(exe)], capture_output=True, text=True)
        self.record("authenticode", "info", status=sig.stdout.strip(), stderr=sig.stderr)
        devices = self.run([str(exe), "--list-devices"], capture_output=True, text=True)
        device_raw = devices.stdout + devices.stderr
        mem = [{"line": line, "free_mib": int(m.group(1)), "total_mib": int(m.group(2))} for line in device_raw.splitlines() if (m := re.search(r"(\d+)\s*MiB\s*(?:free\s*/\s*)?of\s*(\d+)\s*MiB", line, re.I))]
        self.record("devices", "pass" if devices.returncode == 0 else "fail", raw=device_raw, memory=mem)

        model_ok = model.is_file() and check_gguf(model)
        self.record("model", "pass" if model_ok else "fail", gguf_magic=model_ok, sha256=_sha256(model) if model.is_file() else None)
        if not model_ok:
            raise ValueError(f"Model is missing or does not start with GGUF: {model}")

        out.parent.mkdir(parents=True, exist_ok=True)
        log_path = out.with_name(f"{out.stem}-server.log")
        process = None
        try:
            process = self._start(exe, model, port, build, log_path)
            self._wait_health(port)
            bound = self._loopback_only(port)
            self.record("server_start", "pass" if bound else "fail", command=server_command(exe, model, port, build), log=str(log_path), loopback_only=bound)
            self._endpoints(port)
            process = self._canary(port, process, log_path, exe, model, build)
            self._disconnect(port)
            process = self._tool_call(port, process, log_path, exe, model, build)
            self._throughput(port)
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        out.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        return self.report

    def _start(self, exe: Path, model: Path, port: int, build: str, log: Path, extra: list[str] | None = None):
        handle = log.open("w", encoding="utf-8")
        return self.popen(server_command(exe, model, port, build, extra), stdout=handle, stderr=subprocess.STDOUT)

    def _wait_health(self, port: int, timeout: float = 300):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            try:
                if self.http.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                    return
            except Exception:
                pass
            self.sleep(1)
        raise TimeoutError("llama-server /health did not return 200 within 300 seconds")

    def _loopback_only(self, port: int) -> bool:
        if self.connections:
            entries = self.connections()
        else:
            import psutil
            entries = psutil.net_connections(kind="inet")
        listeners = [c for c in entries if c.status == "LISTEN" and c.laddr.port == port]
        return bool(listeners) and all(c.laddr.ip == "127.0.0.1" for c in listeners)

    def _request(self, method: str, url: str, **kwargs: Any):
        try:
            return self.http.request(method, url, timeout=5, **kwargs)
        except Exception as exc:
            return type("FailedResponse", (), {"status_code": None, "text": str(exc), "json": lambda: {}})()

    def _endpoints(self, port: int):
        routes = [("GET", "/"), ("GET", "/slots"), ("POST", "/slots/0?action=save"), ("GET", "/props"), ("POST", "/props"), ("GET", "/metrics"), ("POST", "/completion"), ("POST", "/tokenize"), ("POST", "/embedding"), ("GET", "/lora-adapters")]
        results = []
        for method, path in routes:
            response = self._request(method, f"http://127.0.0.1:{port}{path}", json={})
            bad = path in ("/slots", "/metrics") and response.status_code == 200
            results.append({"method": method, "path": path, "status_code": response.status_code, "status": "fail" if bad else "info"})
        self.record("endpoint_exposure", "fail" if any(r["status"] == "fail" for r in results) else "info", endpoints=results)

    def _chat(self, port: int, **payload: Any):
        response = self.http.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    def _canary(self, port: int, process: Any, log: Path, exe: Path, model: Path, build: str):
        token = str(uuid.uuid4())
        self._chat(port, model="local", messages=[{"role": "user", "content": f"Repeat exactly: {token}"}], max_tokens=30)
        process.terminate()
        process.wait(timeout=10)
        leaked = contains_canary(log, token)
        hidden_by = None
        if leaked:
            process = self._start(exe, model, port, build, log, ["--log-verbosity", "1"])
            try:
                self._wait_health(port)
                self._chat(port, model="local", messages=[{"role": "user", "content": f"Repeat exactly: {token}"}], max_tokens=30)
            finally:
                process.terminate()
                process.wait(timeout=10)
            hidden_by = not contains_canary(log, token)
        self.record("canary_privacy", "fail" if leaked else "pass", canary=token, found_in_log=leaked, verbosity_1_hides=hidden_by)
        return self._start(exe, model, port, build, log)

    def _disconnect(self, port: int):
        base = f"http://127.0.0.1:{port}"
        payload = {"model": "local", "messages": [{"role": "user", "content": "Write a detailed 1500-token essay."}], "max_tokens": 1500, "stream": True}
        seen = 0
        try:
            with self.http.stream("POST", f"{base}/v1/chat/completions", json=payload, timeout=60) as response:
                for _ in response.iter_lines():
                    seen += 1
                    if seen >= 5:
                        break
        except Exception:
            pass
        start = self.clock()
        self._chat(port, model="local", messages=[{"role": "user", "content": "Reply OK"}], max_tokens=4)
        latency = self.clock() - start
        self.record("cancel_on_disconnect", "pass" if latency <= 10 else "fail", chunks_before_disconnect=seen, tiny_request_seconds=latency)

    def _tool_call(self, port: int, process: Any, log: Path, exe: Path, model: Path, build: str):
        payload = {"model": "local", "messages": [{"role": "user", "content": "What is the weather in Dhaka? Use the tool."}], "tools": [{"type": "function", "function": {"name": "get_weather", "description": "Get weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}], "tool_choice": "auto", "max_tokens": 128}
        result = self._chat(port, **payload)
        ok, args = parse_tool_call(result)
        retry_ok = None
        if not ok:
            process.terminate()
            process.wait(timeout=10)
            retry = self._start(exe, model, port, build, log, ["--reasoning-format", "deepseek"])
            try:
                self._wait_health(port)
                retry_payload = self._chat(port, **payload)
                retry_ok, args = parse_tool_call(retry_payload)
            finally:
                retry.terminate()
                retry.wait(timeout=10)
        else:
            process.terminate()
            process.wait(timeout=10)
        self.record("tool_call_result", "pass" if ok or retry_ok else "fail", parsed_arguments=args, reasoning_format_fixed=retry_ok)
        return self._start(exe, model, port, build, log)

    def _throughput(self, port: int):
        start = self.clock()
        result = self._chat(port, model="local", messages=[{"role": "user", "content": "Generate exactly 256 short numbered words."}], max_tokens=256)
        elapsed = max(self.clock() - start, 1e-9)
        usage = result.get("timings", {})
        self.record("throughput", "info", predicted_per_second=usage.get("predicted_per_second"), prompt_per_second=usage.get("prompt_per_second"), response_seconds=elapsed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, choices=BUILD_PATTERNS)
    parser.add_argument("--tag", default="latest")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--bin-dir", type=Path, default=Path("bin"))
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = args.out or Path("reports") / f"llama-check-{socket.gethostname()}-{args.build}.json"
    report = LlamaCheck().run_all(build=args.build, tag=args.tag, model=args.model, bin_dir=args.bin_dir, port=args.port, out=out)
    failed = [step["name"] for step in report["steps"] if step["status"] == "fail"]
    print(f"llama-server check: {'FAIL' if failed else 'PASS'} ({len(failed)} failed checks). Report: {out}")
    for name in failed:
        print(f"  FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
