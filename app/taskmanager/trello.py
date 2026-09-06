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
from app.taskmanager.base import BoardTask, TaskManagerInterface
from app.taskmanager.labels import label_color

logger = logging.getLogger(__name__)

API = "https://api.trello.com/1"

LISTS = ["Backlog", "Commercial", "Technical", "Operations", "Blocked", "Testing", "Done"]

class TrelloError(RuntimeError):
    pass


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
        self.http = httpx.Client(timeout=timeout)

    def _auth(self, **params) -> dict:
        return {"key": self.api_key, "token": self.token, **params}

    def _request(self, method: str, path: str, **params):
        response = self.http.request(method, f"{API}{path}", params=self._auth(**params))
        if response.status_code >= 400:
            raise TrelloError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
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

    def push_tasks(self, board_id: str, tasks: list[BoardTask]) -> dict[str, str]:
        list_ids = self._list_ids(board_id)
        label_ids = self._ensure_labels(board_id, {lbl for t in tasks for lbl in t.labels()})

        created: dict[str, str] = {}
        for task in tasks:
            target_list = task.team.capitalize()
            list_id = list_ids.get(target_list) or list_ids["Backlog"]
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

    def _add_checklists(self, card_id: str, task: BoardTask) -> None:
        """Attach the task's checklists to a card.

        Trello has no field for acceptance criteria, but a checklist is the
        right shape for them: one tickable line per condition, with Trello
        keeping the completed count on the card front. Items are created one
        call each, which is the only way the API offers.
        """
        for name, items in task.checklists():
            checklist = self._request("POST", "/checklists", idCard=card_id, name=name)
            for position, item in enumerate(items, start=1):
                self._request(
                    "POST",
                    f"/checklists/{checklist['id']}/checkItems",
                    name=item[:16384],
                    pos=position,
                    checked="false",
                )

    def close(self) -> None:
        self.http.close()
