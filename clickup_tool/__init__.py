from __future__ import annotations

"""ClickUp Tool – comprehensive wrapper
======================================
This module exposes **three CrewAI‑compatible tools** that cover the core
functionality you shared:

* ``ClickUpCreateTool``   – create **or update** a task (and subtasks)
* ``ClickUpAddComment``   – add a comment to an existing task
* ``ClickUpListTasks``    – fetch every (non‑archived) task in a list

The code is self‑contained – no external helper files are required – so you can
publish it as a single‑file tool package.  If you later want to break helpers
into separate modules, just move the blocks out and adjust the imports.

Environment variables
---------------------
* ``CLICKUP_API_TKN``   – your personal ClickUp token (Settings ▸ Apps ▸ API)
* ``CLICKUP_LIST_ID``     – the default list ID; can be overridden at runtime

You can set them globally in the shell that will run the crew, or pass them when
instantiating the tools.
"""

# ---------------------------------------------------------------------------
# Standard library & third‑party imports
# ---------------------------------------------------------------------------
import os
import json
import datetime as _dt
from datetime import timezone
from typing import Any, Dict, List, Optional, Type

import requests
from pydantic import BaseModel, Field
from crewai.tools import BaseTool, tool

# ---------------------------------------------------------------------------
# ░░░░░  Helpers & constants  ░░░░░
# ---------------------------------------------------------------------------

PRIORITY_MAP: Dict[str, int] = {
    "urgent": 1,
    "high": 2,
    "normal": 3,
    "low": 4,
}

DEFAULT_STATUSES = {
    "to do": "to do",
    "in progress": "in progress",
    "complete": "complete",
}

BASE_URL = "https://api.clickup.com/api/v2"


class _ClickUpError(RuntimeError):
    """Wraps any API‑level failure so Agent sees concise message."""


def _ms_timestamp(dt: _dt.datetime) -> int:
    """Return *00:00 UTC* of the given date as a millisecond epoch."""

    dt_utc = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return int(dt_utc.timestamp() * 1000)


# ---------------------------------------------------------------------------
# ░░░░░  Base Pydantic schemas  ░░░░░
# ---------------------------------------------------------------------------

class _CreateOrUpdateInput(BaseModel):
    """Arguments for creating **or** updating a task."""

    title: str = Field(..., description="Task title – ignored on update if omitted")
    description: Optional[str] = Field(None, description="Task description (Markdown)")
    status: Optional[str] = Field(None, description="Status name, e.g. 'to do'")
    priority: Optional[str | int] = Field(
        None,
        description="Priority (1–4 or urgent/high/normal/low)",
    )
    due_date: Optional[str] = Field(
        None,
        description="YYYY‑MM‑DD – stored noon‑UTC in ClickUp",
    )
    parent_id: Optional[str] = Field(
        None,
        description="If set, create as a sub‑task of this task ID",
    )
    task_id: Optional[str] = Field(
        None,
        description="If set, *update* this task instead of creating a new one",
    )


class _CommentInput(BaseModel):
    task_id: str = Field(..., description="Existing task ID")
    comment_text: str = Field(..., description="Comment body (Markdown supported)")


class _Empty(BaseModel):
    """Placeholder for tools that take no runtime args."""

    _ignore: Optional[str] = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# ░░░░░  Low‑level request helper  ░░░░░
# ---------------------------------------------------------------------------

def _request(method: str, endpoint: str, token: str, *, json_payload: Any | None = None) -> Any:
    """Minimal wrapper that raises `_ClickUpError` on non‑2xx."""

    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.request(method, url, headers=headers, json=json_payload, timeout=30)
        if resp.status_code >= 400:
            raise _ClickUpError(f"{resp.status_code} – {resp.text[:400]}")
        return resp.json()
    except requests.RequestException as exc:
        raise _ClickUpError(str(exc)) from exc


# ---------------------------------------------------------------------------
# ░░░░░  ClickUpCreateTool (create / update task)  ░░░░░
# ---------------------------------------------------------------------------

class ClickUpCreateTool(BaseTool):
    name: str = "ClickUp_CreateOrUpdate_Task"
    description: str = (
        "Create a task, sub‑task, or update an existing task in ClickUp."
    )
    args_schema: Type[BaseModel] = _CreateOrUpdateInput

    class Config:
        extra = "allow"  # allow token / list_id injection

    def __init__(self, *, token: str | None = None, list_id: str | None = None):
        super().__init__()
        self.token = token or os.getenv("CLICKUP_API_TKN") or ""
        self.list_id = list_id or os.getenv("CLICKUP_LIST_ID") or ""
        if not self.token:
            raise RuntimeError("CLICKUP_API_TKN missing")
        if not self.list_id:
            raise RuntimeError("CLICKUP_LIST_ID missing")

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _priority_val(self, raw: str | int | None) -> int:
        if raw is None:
            return 3
        if isinstance(raw, int):
            return raw if 1 <= raw <= 4 else 3
        raw = raw.lower()
        if raw.isdigit():
            v = int(raw)
            return v if 1 <= v <= 4 else 3
        return PRIORITY_MAP.get(raw, 3)

    def _due_date_ms(self, date_str: str | None) -> int | None:
        if not date_str:
            return None
        try:
            y, m, d = map(int, date_str.split("-"))
            return _ms_timestamp(_dt.datetime(y, m, d))
        except Exception:
            return None

    # ---------------------------------------------------------------------
    # CrewAI entry‑point
    # ---------------------------------------------------------------------

    def _run(self, **data) -> dict:
        inp = _CreateOrUpdateInput(**data)
        is_update = bool(inp.task_id)

        payload: Dict[str, Any] = {}

        # title
        if inp.title and not is_update:
            payload["name"] = inp.title
        elif not inp.title and not is_update:
            raise _ClickUpError("title is required when creating a task")

        # description
        if inp.description is not None:
            payload["description"] = inp.description

        # status
        if inp.status:
            payload["status"] = inp.status.lower()

        # priority
        payload["priority"] = self._priority_val(inp.priority)

        # due date
        due_ms = self._due_date_ms(inp.due_date)
        if due_ms is not None:
            payload["due_date"] = due_ms

        # parent
        if inp.parent_id and not is_update:
            payload["parent"] = inp.parent_id

        # ------------------------------------------------------------------
        if is_update:
            endpoint = f"/task/{inp.task_id}"
            result = _request("PUT", endpoint, self.token, json_payload=payload)
        else:
            endpoint = f"/list/{self.list_id}/task"
            result = _request("POST", endpoint, self.token, json_payload=payload)
        return result


# ---------------------------------------------------------------------------
# ░░░░░  ClickUpAddComment  ░░░░░
# ---------------------------------------------------------------------------

class ClickUpAddComment(BaseTool):
    name: str = "ClickUp_Add_Comment"
    description: str = "Add a comment to an existing ClickUp task"
    args_schema: Type[BaseModel] = _CommentInput

    class Config:
        extra = "allow"

    def __init__(self, *, token: str | None = None):
        super().__init__()
        self.token = token or os.getenv("CLICKUP_API_TKN") or ""
        if not self.token:
            raise RuntimeError("CLICKUP_API_TKN missing")

    def _run(self, task_id: str, comment_text: str, **_extra) -> dict:
        endpoint = f"/task/{task_id}/comment"
        payload = {"comment_text": comment_text}
        return _request("POST", endpoint, self.token, json_payload=payload)


# ---------------------------------------------------------------------------
# ░░░░░  ClickUpListTasks  ░░░░░
# ---------------------------------------------------------------------------

class ClickUpListTasks(BaseTool):
    name: str = "ClickUp_List_Tasks"
    description: str = "Fetch all non‑archived tasks from the configured ClickUp list."
    args_schema: Type[BaseModel] = _Empty

    class Config:
        extra = "allow"

    def __init__(self, *, token: str | None = None, list_id: str | None = None):
        super().__init__()
        self.token = token or os.getenv("CLICKUP_API_TKN") or ""
        self.list_id = list_id or os.getenv("CLICKUP_LIST_ID") or ""
        if not self.token:
            raise RuntimeError("CLICKUP_API_TKN missing")
        if not self.list_id:
            raise RuntimeError("CLICKUP_LIST_ID missing")

    def _run(self, **_extra) -> dict:
        endpoint = f"/list/{self.list_id}/task?archived=false"
        return _request("GET", endpoint, self.token)


# ---------------------------------------------------------------------------
# ░░░░░  Syntactic sugar: functional wrappers  ░░░░░
# ---------------------------------------------------------------------------

# These wrappers let an **agent** call the functionality directly via
# `from clickup_tool import create_clickup_task` etc.

_default_creator = ClickUpCreateTool()
_default_commenter = ClickUpAddComment()
_default_lister = ClickUpListTasks()


@tool("create_clickup_task")
def create_clickup_task(**kwargs) -> dict:  # noqa: D401,E501 – simple delegation
    """Functional wrapper over :class:`ClickUpCreateTool`."""

    return _default_creator._run(**kwargs)


@tool("add_clickup_comment")
def add_clickup_comment(**kwargs) -> dict:
    """Functional wrapper over :class:`ClickUpAddComment`."""

    return _default_commenter._run(**kwargs)


@tool("list_clickup_tasks")
def list_clickup_tasks() -> dict:
    """Functional wrapper over :class:`ClickUpListTasks`."""

    return _default_lister._run()
