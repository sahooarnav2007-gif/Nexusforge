#!/usr/bin/env python3
"""NexusForge end-to-end demo driver.

Drives the REAL platform through its API:
  register user -> create project -> import demo repo -> create task
  -> run task (streams live SSE events) -> print final report.

Usage:
  python scripts/demo.py [--base-url http://127.0.0.1:8000]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEMO_REPO = Path(__file__).resolve().parent.parent / "demo" / "repo"
REQUIREMENT = (
    "Implement the missing user profile API and ensure all tests pass. "
    "The profile endpoint GET /users/{user_id}/profile should return the "
    "full public profile (user_id, username, email, bio, joined_at) and "
    "return 404 for unknown users."
)

STATE_COLORS = {
    "COMPLETED": "\033[92m", "FAILED": "\033[91m", "CANCELLED": "\033[93m",
    "TIMEOUT": "\033[91m", "SECURITY_BLOCKED": "\033[91m",
}
RESET = "\033[0m"


def _say(msg: str) -> None:
    print(f"\033[90m[demo]\033[0m {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default=None)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    email = args.email or f"demo+{uuid.uuid4().hex[:8]}@example.com"
    password = "DemoPass123!"

    with httpx.Client(base_url=base, timeout=120) as c:
        # 0) health
        h = c.get("/health")
        h.raise_for_status()
        _say(f"API healthy: {h.json()['version']} (provider="
             f"{h.json()['llm_provider']}, sandbox={h.json()['sandbox_backend']})")

        # 1) register
        r = c.post("/api/auth/register", json={
            "email": email, "username": f"demo_{uuid.uuid4().hex[:6]}",
            "password": password})
        r.raise_for_status()
        tok = r.json()
        headers = {"Authorization": f"Bearer {tok['access_token']}"}
        _say(f"registered {email}")

        # 2) project
        r = c.post("/api/projects", json={
            "name": "User Platform (demo)",
            "description": "Demo: half-finished FastAPI app"}, headers=headers)
        r.raise_for_status()
        project = r.json()
        _say(f"created project {project['name']} ({project['id'][:8]})")

        # 3) import repo
        r = c.post(f"/api/projects/{project['id']}/repositories", json={
            "name": "user-platform",
            "source_kind": "local",
            "source_ref": str(DEMO_REPO),
            "index": True,
        }, headers=headers)
        r.raise_for_status()
        repo = r.json()
        _say(f"imported repo ({repo['file_count']} files, "
             f"languages={repo['languages']}, indexed={repo['indexed']})")

        # 4) task
        r = c.post(f"/api/tasks/projects/{project['id']}", json={
            "title": "Implement user profile API",
            "requirement": REQUIREMENT,
            "repository_id": repo["id"],
        }, headers=headers)
        r.raise_for_status()
        task = r.json()
        _say(f"created task: {task['title']}")

        # 5) run
        r = c.post(f"/api/tasks/{task['id']}/run", json={}, headers=headers)
        r.raise_for_status()
        run = r.json()
        _say(f"run started: {run['id'][:8]} — streaming live events...")
        print("-" * 72, flush=True)

        # 6) stream SSE
        terminal_buf: list[str] = []
        final_state = None
        with c.stream("GET",
                      f"/api/tasks/{task['id']}/runs/{run['id']}/stream",
                      headers=headers, timeout=600) as resp:
            resp.raise_for_status()
            event_type = "message"
            for raw in resp.iter_lines():
                if not raw:
                    continue
                if raw.startswith("event:"):
                    event_type = raw[6:].strip()
                    continue
                if raw.startswith("data:"):
                    data = json.loads(raw[5:].strip())
                    _handle_event(event_type, data, terminal_buf)
                    if event_type in ("run_completed", "run_failed",
                                      "run_cancelled", "run_timeout",
                                      "run_security_blocked"):
                        final_state = data.get("state", event_type)
                        break
                elif raw.startswith(":"):
                    continue
        print("-" * 72, flush=True)

        # 7) final detail + report
        r = c.get(f"/api/tasks/{task['id']}", headers=headers)
        r.raise_for_status()
        detail = r.json()
        latest = detail["latest_run"]
        color = STATE_COLORS.get(latest["state"], "")
        _say(f"final state: {color}{latest['state']}{RESET} "
             f"(iterations={latest['iteration']}, llm_calls={latest['llm_calls']}, "
             f"tokens={latest['tokens_used']})")
        if latest.get("error"):
            _say(f"error: {latest['error']}")
        print("\nChanged files:")
        for ch in detail["changes"]:
            print(f"  {ch['action']:8s} {ch['path']}")
        print("\nTest runs:")
        for t in detail["test_runs"]:
            print(f"  iter {t['iteration']}: {t['passed']} passed, "
                  f"{t['failed']} failed, {t['errors']} errors "
                  f"({t['duration_ms']/1000:.1f}s)")
        print(f"\nSecurity findings: {len(detail['security_findings'])}")
        for f in detail["security_findings"][:8]:
            print(f"  [{f['severity']}] {f['rule_id']} {f['path']}:{f['line']} "
                  f"— {f['message'][:70]} ({f['source']})")
        print(f"Review findings: {len(detail['review_findings'])}")
        for f in detail["review_findings"][:8]:
            print(f"  [{f['severity']}] {f['category']}: {f['title']}")

        if detail.get("report"):
            print("\n" + "=" * 72)
            print(detail["report"]["content"])
            print("=" * 72)
            # also fetch the PDF to prove export works
            rp = c.get(f"/api/tasks/{task['id']}/runs/{run['id']}/report/pdf", headers=headers)
            if rp.status_code == 200:
                pdf_path = os.path.join(tempfile.gettempdir(), "nexusforge-demo-report.pdf")
                with open(pdf_path, "wb") as fh:
                    fh.write(rp.content)
                _say(f"PDF report exported ({len(rp.content)} bytes) -> {pdf_path}")
        return 0 if latest["state"] == "COMPLETED" else 1


def _handle_event(event_type: str, data: dict, terminal_buf: list[str]) -> None:
    if event_type == "state_change":
        print(f"\n\033[1m\033[96m▶ {data['state']}\033[0m "
              f"(iteration {data.get('iteration', 0)})", flush=True)
    elif event_type == "step_started":
        print(f"  \033[90m• {data['agent']} started\033[0m", flush=True)
    elif event_type == "step_finished":
        mark = "\033[92m✓\033[0m" if data["status"] == "completed" else "\033[91m✗\033[0m"
        print(f"  {mark} {data['agent']}: {data['summary'][:100]}", flush=True)
        if data.get("error"):
            print(f"    \033[91merror: {data['error'][:200]}\033[0m", flush=True)
    elif event_type == "terminal":
        text = data.get("text", "")
        if text.startswith("$ "):
            print(f"  \033[94m$\033[0m {text[2:]}", flush=True)
        elif text.startswith("[agent]"):
            print(f"  \033[95m{text}\033[0m", flush=True)
        else:
            terminal_buf.append(text)
            # print test output lines (bounded)
            if len(terminal_buf) % 20 == 0 or "passed" in text or "failed" in text:
                print(f"  \033[90m{text[:110]}\033[0m", flush=True)
    elif event_type == "test_result":
        mark = "\033[92mPASS\033[0m" if data["ok"] else "\033[91mFAIL\033[0m"
        print(f"  \033[1m[test] {mark}: {data['passed']} passed, "
              f"{data['failed']} failed, {data['errors']} errors\033[0m", flush=True)
        for ft in data.get("failed_tests", [])[:5]:
            print(f"    \033[91m✗ {ft}\033[0m", flush=True)
    elif event_type == "iteration":
        print(f"\n  \033[1m\033[93m⟳ repair iteration {data['iteration']}\033[0m",
              flush=True)
    elif event_type == "security_result":
        print(f"  \033[93m[security] {data['total']} findings "
              f"({data['by_severity']})\033[0m", flush=True)
    elif event_type == "review_result":
        print(f"  \033[93m[review] {data['total']} findings — "
              f"{data['summary'][:80]}\033[0m", flush=True)
    elif event_type in ("run_completed", "run_failed", "run_cancelled",
                        "run_timeout", "run_security_blocked"):
        if data.get("error"):
            print(f"\n\033[91m[run] {data['error']}\033[0m", flush=True)


if __name__ == "__main__":
    sys.exit(main())