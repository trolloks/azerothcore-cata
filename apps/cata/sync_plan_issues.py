#!/usr/bin/env python3
"""Migrate numbered conversion plans to GitHub issues and verify the issue index."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import time


PLAN_PATTERN = re.compile(r"^(?P<number>\d{2})-.*\.md$")
MARKER_PATTERN = re.compile(r"<!-- acore-cata-plan:(?P<number>\d{2}) -->")
LABEL = "cataclysm-conversion"


@dataclass(frozen=True)
class Plan:
    number: str
    title: str
    body: str
    closed: bool


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    state: str
    url: str
    body: str


def api(repo: str, endpoint: str, *, method: str = "GET", payload: dict | None = None) -> object:
    command = ["gh", "api", "--method", method, f"repos/{repo}/{endpoint}"]
    encoded = None
    if payload is not None:
        command.extend(("--input", "-"))
        encoded = json.dumps(payload)

    last_error = ""
    for attempt in range(3):
        result = subprocess.run(command, input=encoded, text=True, capture_output=True, check=False)
        if result.returncode == 0:
            return json.loads(result.stdout) if result.stdout.strip() else None
        last_error = result.stderr.strip()
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GitHub API failed after three attempts: {last_error}")


def issue_marker(number: str) -> str:
    return f"<!-- acore-cata-plan:{number} -->"


def issue_body(plan: Plan, source: Path) -> str:
    return (
        f"{issue_marker(plan.number)}\n"
        f"Migrated from `{source.as_posix()}`. This issue is the canonical Plan {int(plan.number)} record.\n\n"
        "---\n\n"
        f"{plan.body.rstrip()}\n"
    )


def plan_is_closed(body: str) -> bool:
    status = next((line for line in body.splitlines() if line.startswith("Status:")), "").lower()
    return "complete" in status or "implemented and verified" in status


def read_plans(plan_dir: Path) -> list[tuple[Plan, Path]]:
    plans: list[tuple[Plan, Path]] = []
    for path in sorted(plan_dir.glob("[0-9][0-9]-*.md")):
        match = PLAN_PATTERN.match(path.name)
        if not match:
            continue
        body = path.read_text(encoding="utf-8")
        title = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
        if not title:
            raise RuntimeError(f"{path} has no H1 title")
        if "Canonical issue:" in body:
            raise RuntimeError(f"{path} is already an issue-reference stub; refusing to overwrite GitHub")
        plans.append((Plan(match.group("number"), title, body, plan_is_closed(body)), path))
    if not plans:
        raise RuntimeError(f"no numbered plan documents found in {plan_dir}")
    return plans


def list_issues(repo: str) -> list[Issue]:
    raw: list[dict] = []
    page = 1
    while True:
        batch = api(repo, f"issues?state=all&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise RuntimeError("GitHub issue list did not return an array")
        raw.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [
        Issue(item["number"], item["title"], item["state"], item["html_url"], item.get("body") or "")
        for item in raw
        if "pull_request" not in item
    ]


def ensure_label(repo: str) -> None:
    labels = api(repo, "labels?per_page=100")
    if isinstance(labels, list) and any(item.get("name") == LABEL for item in labels):
        return
    api(
        repo,
        "labels",
        method="POST",
        payload={"name": LABEL, "color": "5319E7", "description": "Cataclysm conversion plan"},
    )


def marker_map(issues: list[Issue]) -> dict[str, Issue]:
    result: dict[str, Issue] = {}
    for issue in issues:
        match = MARKER_PATTERN.search(issue.body)
        if not match:
            continue
        number = match.group("number")
        if number in result:
            raise RuntimeError(f"duplicate GitHub issues carry Plan {number} marker")
        result[number] = issue
    return result


def sync(repo: str, plan_dir: Path) -> None:
    ensure_label(repo)
    existing = marker_map(list_issues(repo))
    rows: list[tuple[str, int, str, str, str]] = []

    for plan, source in read_plans(plan_dir):
        desired_state = "closed" if plan.closed else "open"
        payload = {
            "title": plan.title,
            "body": issue_body(plan, source),
            "labels": [LABEL],
            "state": desired_state,
        }
        if plan.closed:
            payload["state_reason"] = "completed"

        issue = existing.get(plan.number)
        if issue is None:
            created = api(repo, "issues", method="POST", payload=payload)
            if not isinstance(created, dict):
                raise RuntimeError(f"GitHub did not return the created Plan {plan.number} issue")
            if created["state"] != desired_state:
                created = api(repo, f"issues/{created['number']}", method="PATCH", payload=payload)
                if not isinstance(created, dict):
                    raise RuntimeError(f"GitHub did not return the reconciled Plan {plan.number} issue")
            issue = Issue(
                created["number"], created["title"], created["state"], created["html_url"], created["body"],
            )
            action = "created"
        else:
            changed = issue.title != plan.title or issue.body != payload["body"] or issue.state != desired_state
            if changed:
                updated = api(repo, f"issues/{issue.number}", method="PATCH", payload=payload)
                if not isinstance(updated, dict):
                    raise RuntimeError(f"GitHub did not return the updated Plan {plan.number} issue")
                issue = Issue(
                    updated["number"], updated["title"], updated["state"], updated["html_url"], updated["body"],
                )
                action = "updated"
            else:
                action = "unchanged"

        print(f"Plan {int(plan.number)}: {action} {issue.url}", file=sys.stderr)
        rows.append((plan.number, issue.number, issue.state, issue.title, issue.url))

    print("plan\tissue\tstate\ttitle\turl")
    for row in rows:
        print("\t".join(map(str, row)))


def read_index(path: Path) -> list[tuple[str, int, str, str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "plan\tissue\tstate\ttitle\turl":
        raise RuntimeError(f"{path} has an invalid header")
    rows = []
    for line in lines[1:]:
        number, issue, state, title, url = line.split("\t")
        rows.append((number, int(issue), state, title, url))
    return rows


def check(repo: str, index: Path) -> None:
    issues = {issue.number: issue for issue in list_issues(repo)}
    rows = read_index(index)
    if [row[0] for row in rows] != [f"{number:02d}" for number in range(1, len(rows) + 1)]:
        raise RuntimeError("plan issue index is not contiguous from Plan 1")
    for plan, number, state, title, url in rows:
        issue = issues.get(number)
        if issue is None:
            raise RuntimeError(f"Plan {int(plan)} issue #{number} is missing")
        expected = (state, title, url, issue_marker(plan))
        actual = (issue.state, issue.title, issue.url, issue_marker(plan) if issue_marker(plan) in issue.body else "")
        if actual != expected:
            raise RuntimeError(f"Plan {int(plan)} issue #{number} does not match the index")
    print(f"PASS: {len(rows)} plan issues match {index}")


def self_check() -> None:
    assert plan_is_closed("# Plan\n\nStatus: complete.\n")
    assert plan_is_closed("# Plan\n\nStatus: implemented and verified.\n")
    assert not plan_is_closed("# Plan\n\nStatus: ready for implementation.\n")
    issues = [Issue(8, "Plan 8", "open", "https://example/8", issue_marker("08"))]
    assert marker_map(issues)["08"].number == 8
    try:
        marker_map(issues * 2)
    except RuntimeError:
        pass
    else:
        raise AssertionError("duplicate plan markers must fail")
    print("self-check: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("self-check")

    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--repo", required=True)
    sync_parser.add_argument("--plan-dir", type=Path, default=Path("plan"))

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--repo", required=True)
    check_parser.add_argument("--index", type=Path, default=Path("plan/github-issues.tsv"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-check":
        self_check()
    elif args.command == "sync":
        sync(args.repo, args.plan_dir)
    else:
        check(args.repo, args.index)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"plan issue sync failed: {error}", file=sys.stderr)
        raise SystemExit(1)
