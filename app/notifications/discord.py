"""Discord webhook notifications (brief section 34).

Notifications are best-effort. A webhook that is unset, rate-limited, or down
must never fail the pipeline that produced the plan — losing a message is a far
smaller problem than losing the run.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 10.0
DISCORD_LIMIT = 2000


def _post(webhook_url: str, content: str) -> bool:
    try:
        response = httpx.post(
            webhook_url, json={"content": content[:DISCORD_LIMIT]}, timeout=TIMEOUT
        )
    except httpx.RequestError as exc:
        logger.warning("Discord webhook unreachable: %s", exc)
        return False
    if response.status_code >= 400:
        logger.warning("Discord webhook rejected the message: %s", response.status_code)
        return False
    return True


def format_review_ready(
    project_name: str,
    task_counts: dict[str, int],
    assumption_count: int,
    grounding_score: float | None,
    critical_path_length: int,
    failed_stages: list[str],
) -> str:
    lines = [
        "**New project ready for review**",
        f"Project: {project_name}",
        f"Tasks: {sum(task_counts.values())}"
        + (f" ({', '.join(f'{k} {v}' for k, v in task_counts.items())})" if task_counts else ""),
        f"Critical path: {critical_path_length} tasks",
        f"Assumptions: {assumption_count}",
    ]
    if grounding_score is not None:
        lines.append(f"Grounding score: {grounding_score:.0%}")
    lines.append(
        "Validation: " + (f"{', '.join(failed_stages)} failed" if failed_stages else "all stages passed")
    )
    lines.append("Status: awaiting PM approval")
    return "\n".join(lines)


def notify_review_ready(webhook_url: str | None = None, **kwargs) -> bool:
    url = webhook_url or get_settings().discord_webhook_url
    if not url:
        logger.info("No Discord webhook configured; skipping notification")
        return False
    return _post(url, format_review_ready(**kwargs))


def format_board_drift(messages: list[str]) -> str:
    """One message for everything a single check turned up.

    Grouped rather than sent one per difference: a person who moved four cards
    in a row should get one notification about it, not four.
    """
    lines = [f"**The board no longer matches the plan** ({len(messages)} change(s))", ""]
    lines += [f"- {message}" for message in messages]
    lines.append("")
    lines.append("Nothing was changed on the board. Review these in the platform.")
    return "\n".join(lines)


def notify_board_drift(messages: list[str], webhook_url: str | None = None) -> bool:
    """Tell the PM what changed on the board behind the plan's back.

    Returns False when there is no webhook set, and the caller leaves the
    differences unmarked so they are still waiting to be sent if one is
    configured later.
    """
    if not messages:
        return False
    url = webhook_url or get_settings().discord_webhook_url
    if not url:
        logger.info("No Discord webhook configured; board drift recorded but not sent")
        return False
    return _post(url, format_board_drift(messages))
