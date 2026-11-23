#!/usr/bin/env python3
"""ClickUp live e2e: create, comment, status, read back (requires credentials + --yes)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.clickup import ClickUpClient, ClickUpError, execute_clickup_action
from config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="ClickUp integration e2e")
    parser.add_argument("--yes", action="store_true", help="Allow creating a real test task")
    args = parser.parse_args()
    settings = Settings.from_env()
    client = ClickUpClient.from_settings(settings)
    if not client.configured:
        print("SKIP: not configured (set CLICKUP_API_TOKEN and CLICKUP_LIST_ID)")
        return 0
    if not args.yes:
        print("SKIP: pass --yes to create a real ClickUp test task")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = f"[Sahir e2e] {stamp}"
    try:
        created = execute_clickup_action(
            client,
            {"action": "create_task", "name": name, "description": "Automated Sahir verification task"},
        )
        tid = created.get("task_id")
        url = created.get("url") or ""
        print(f"Created: {name}")
        print(f"URL: {url}")
        if not tid:
            raise ClickUpError("No task id in create response")
        execute_clickup_action(client, {"action": "add_comment", "task_id": tid, "text": "Sahir e2e comment"})
        execute_clickup_action(client, {"action": "update_status", "task_id": tid, "status": "complete"})
        task = client.get_task(str(tid))
        print(f"Read back: {task.get('name')} status={task.get('status', {}).get('status')}")
        if url:
            print(url)
        return 0
    except ClickUpError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())