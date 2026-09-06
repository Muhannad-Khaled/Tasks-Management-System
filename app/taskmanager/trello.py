"""Trello adapter.

Trello models a project as a board of lists and cards. The platform's richer
metadata (evidence chunks, grounding, provenance) has nowhere structured to
live, so it is rendered into the card description and encoded as labels; the
authoritative copy stays in PostgreSQL (brief section 25).
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.taskmanager.base import BoardTask, CardSnapshot, TaskManagerInterface
from app.taskmanager.labels import label_color

logger = logging.getLogger(__name__)

API = "https://api.trello.com/1"

LISTS = ["Backlog", "Commercial", "Technical", "Operations", "Blocked", "Testing", "Done"]

class TrelloError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        # Carried so callers can tell "this card is gone" from "Trello is
        # broken". Matching on the text of the message would break the first
        # time the wording changed.
        self.status_code = status_code


class TrelloAdapter(TaskManagerInterface):
    name = "trello"

    def __init__(self, api_key: str | None = None, token: str | None = None, timeout: float = 20.0):
        settings = get_settings()
        self.api_key = api_key or settings.trello_api_key
        self.token = token or settings.trello_api_token
        if not self.api_key or not self.token:
            raise TrelloError(
                "TRELLO_API_KEY and TRELLO_API_TOKEN must be set in .env to push to Trello."
            )
        # In a header rather than the query string: httpx logs request URLs at
        # INFO, so credentials in the URL are one stray log level away from
        # being printed in full to a console or a log file.
        self.http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": (
                    f'OAuth oauth_consumer_key="{self.api_key}", '
                    f'oauth_token="{self.token}"'
                )
            },
        )

    def _request(self, method: str, path: str, **params):
        response = self.http.request(method, f"{API}{path}", params=params)
        if response.status_code >= 400:
            raise TrelloError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}",
                response.status_code,
            )
        return response.json() if response.content else None

    def create_board(self, project_name: str) -> tuple[str, str]:
        board = self._request(
            "POST",
            "/boards/",
            name=project_name[:16384],
            defaultLists="false",
            desc="Generated from the SOW by the SOW-to-Project platform.",
        )
        board_id = board["id"]
        # Trello returns lists in creation order; create them in board order.
        for position, list_name in enumerate(LISTS):
            self._request("POST", "/lists", name=list_name, idBoard=board_id, pos=position + 1)
        return board_id, board["url"]

    def _list_ids(self, board_id: str) -> dict[str, str]:
        return {lst["name"]: lst["id"] for lst in self._request("GET", f"/boards/{board_id}/lists")}

    def _ensure_labels(self, board_id: str, names: set[str]) -> dict[str, str]:
        existing = {
            lbl["name"]: lbl["id"]
            for lbl in self._request("GET", f"/boards/{board_id}/labels", limit=100)
            if lbl["name"]
        }
        for name in sorted(names - set(existing)):
            created = self._request(
                "POST",
                "/labels",
                name=name,
                color=label_color(name),
                idBoard=board_id,
            )
            existing[name] = created["id"]
        return existing

    def expected_location(self, task: BoardTask) -> str:
        """The list a task's card belongs in, by team."""
        target = task.team.capitalize()
        return target if target in LISTS else "Backlog"

    def card_snapshots(self, board_id: str) -> dict[str, CardSnapshot]:
        """Every card on the board, as it stands right now.

        Two calls whatever the board holds, so checking a whole project costs
        the same as checking one task. The name and description ride along with
        the list for free, which is what makes hand edits visible at all.
        """
        lists = {lst["id"]: lst["name"] for lst in self._request("GET", f"/boards/{board_id}/lists")}
        # /cards/all, not /cards: the plain endpoint hides archived cards, and
        # an archived card would then be indistinguishable from a deleted one.
        # It is not — it comes back whole.
        cards = (
            self._request(
                "GET", f"/boards/{board_id}/cards/all", fields="idList,name,desc,closed"
            )
            or []
        )
        return {
            card["id"]: CardSnapshot(
                location=lists.get(card["idList"], ""),
                title=card.get("name", ""),
                description=card.get("desc", ""),
                archived=bool(card.get("closed")),
            )
            for card in cards
        }

    def restore_card(self, board_id: str, external_id: str) -> bool:
        """Un-archive a card, keeping its comments, ticks and history."""
        try:
            self._request("PUT", f"/cards/{external_id}", closed="false")
        except TrelloError as exc:
            if exc.status_code == 404:
                # Archived is recoverable; deleted is not, and this is how the
                # difference finally shows up.
                logger.info("Card %s cannot be restored; it is gone", external_id)
                return False
            raise
        logger.info("Restored archived card %s", external_id)
        return True

    def push_tasks(self, board_id: str, tasks: list[BoardTask]) -> dict[str, str]:
        list_ids = self._list_ids(board_id)
        label_ids = self._ensure_labels(board_id, {lbl for t in tasks for lbl in t.labels()})

        created: dict[str, str] = {}
        for task in tasks:
            # Through expected_location, so that the list a card is put in and
            # the list drift is measured against can never fall out of step.
            list_id = list_ids.get(self.expected_location(task)) or list_ids["Backlog"]
            params = {
                "idList": list_id,
                "name": task.title[:16384],
                "desc": task.rendered_description()[:16384],
                "idLabels": ",".join(
                    label_ids[lbl] for lbl in task.labels() if lbl in label_ids
                ),
            }
            if task.due_date:
                params["due"] = task.due_date.isoformat()
            card = self._request("POST", "/cards", **params)
            created[task.task_id] = card["id"]
            self._add_checklists(card["id"], task)
            logger.info("Created Trello card %s for task %s", card["id"], task.task_id)
        return created

    def update_task(self, board_id: str, external_id: str, task: BoardTask) -> bool:
        """Rewrite an existing card from the task as it now stands."""
        label_ids = self._ensure_labels(board_id, set(task.labels()))
        params = {
            "name": task.title[:16384],
            "desc": task.rendered_description()[:16384],
            "idLabels": ",".join(label_ids[lbl] for lbl in task.labels() if lbl in label_ids),
            # Sent even when there is no date: an empty string clears it.
            # Omitting the key would leave last week's due date on a card whose
            # task has since had its schedule taken away.
            "due": task.due_date.isoformat() if task.due_date else "",
        }
        try:
            self._request("PUT", f"/cards/{external_id}", **params)
        except TrelloError as exc:
            if exc.status_code == 404:
                logger.info("Card %s no longer exists on the board", external_id)
                return False
            raise
        self._sync_checklists(external_id, task)
        logger.info("Updated Trello card %s for task %s", external_id, task.task_id)
        return True

    def _add_checklists(self, card_id: str, task: BoardTask) -> None:
        """Attach the task's checklists to a card.

        Trello has no field for acceptance criteria, but a checklist is the
        right shape for them: one tickable line per condition, with Trello
        keeping the completed count on the card front.
        """
        for name, items in task.checklists():
            self._create_checklist(card_id, name, items)

    def _create_checklist(self, card_id: str, name: str, items: list[str]) -> None:
        """Items are created one call each, which is the only way the API offers."""
        checklist = self._request("POST", "/checklists", idCard=card_id, name=name)
        for position, item in enumerate(items, start=1):
            self._request(
                "POST",
                f"/checklists/{checklist['id']}/checkItems",
                name=item[:16384],
                pos=position,
                checked="false",
            )

    def _sync_checklists(self, card_id: str, task: BoardTask) -> None:
        """Bring a card's checklists in line with the task, item by item.

        Items are matched by name. One that is already on the card is left
        exactly as it is — ticks included — and only genuine additions,
        removals and reorderings are written. Rebuilding a whole checklist
        because a single line changed would reset every tick on it, and those
        ticks are the QA record of what has actually been verified.
        """
        existing = (
            self._request(
                "GET",
                f"/cards/{card_id}/checklists",
                checkItems="all",
                checkItem_fields="name,pos",
            )
            or []
        )
        by_name = {checklist["name"]: checklist for checklist in existing}

        for name, items in task.checklists():
            current = by_name.pop(name, None)
            if current is None:
                self._create_checklist(card_id, name, items)
            else:
                self._sync_items(card_id, current, items)

        # A checklist the task no longer has — every test case was removed, say
        # — has to go, or the card keeps asserting something the plan dropped.
        for leftover in by_name.values():
            self._request("DELETE", f"/checklists/{leftover['id']}")

    def _sync_items(self, card_id: str, checklist: dict, wanted: list[str]) -> None:
        """Add, remove and reorder the lines of one checklist, touching no others."""
        on_card = {item["name"]: item for item in checklist.get("checkItems", [])}
        expected = set(wanted)

        for name, item in on_card.items():
            if name not in expected:
                self._request("DELETE", f"/cards/{card_id}/checkItem/{item['id']}")

        kept = [name for name in wanted if name in on_card]
        # Trello spaces its positions out (16384, 32768, …) rather than
        # numbering them, so only the relative order can be compared. Positions
        # are rewritten when the order moved or a line was inserted, because a
        # new item numbered 2 would otherwise sort ahead of an old one at 16384.
        settled = sorted(kept, key=lambda name: on_card[name]["pos"])
        renumber = settled != kept or len(kept) != len(wanted)

        for position, name in enumerate(wanted, start=1):
            item = on_card.get(name)
            if item is None:
                self._request(
                    "POST",
                    f"/checklists/{checklist['id']}/checkItems",
                    name=name[:16384],
                    pos=position,
                    checked="false",
                )
            elif renumber:
                # A PUT keeps the item's state, so its tick moves with the line.
                self._request(
                    "PUT", f"/cards/{card_id}/checkItem/{item['id']}", pos=position
                )

    def close(self) -> None:
        self.http.close()
