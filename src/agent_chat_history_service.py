"""Local persistence for knowledge-chat conversations (R3 P0-B).

Product contract (brief §14-§19, §35-§36):

- One question → one answer is an *exchange* inside a conversation thread.
  Both turns are stored locally so a refresh, a browser reconnect or a
  service restart can restore what the user already paid tokens for
  (red team R1-06 / R2-05, HIGH).
- Failed or empty calls are recorded with their honest status
  (``failed`` / ``no_evidence``); the store never fabricates an answer.
- Citations are persisted as stable source ids so the sources panel and
  the source viewer can be rebuilt after reload.
- Chat history is conversation history ONLY.  Nothing in this service
  writes knowledge objects, notes or question items (§35 boundary).
- Delete is not offered in v0.8.6's UI; the schema keeps
  ``ON DELETE CASCADE`` so a future cleanup cannot orphan turns (R2-01
  foreign-key lesson applied from day one).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

MESSAGE_ROLES = ("question", "answer")
MESSAGE_STATUSES = ("success", "no_evidence", "failed")


class AgentChatHistoryError(RuntimeError):
    """Chat history persistence was refused or the record is unusable."""


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One stored question or answer turn."""

    id: int
    conversation_id: int
    role: str
    content: str
    status: str
    mode: str
    model: str
    citations: tuple[str, ...]
    failure_code: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """Listing entry for the history picker."""

    id: int
    title: str
    message_count: int
    created_at: str
    updated_at: str


class AgentChatHistoryService:
    """SQLite-backed conversation store for the knowledge Agent page."""

    def __init__(self, database: Any) -> None:
        self._database = database

    # ------------------------------------------------------------ conversations
    def ensure_conversation(self, conversation_id: int | None, title: str = "") -> int:
        """Return a usable conversation id, creating one when needed."""

        with self._database._connection() as connection:
            if conversation_id is not None:
                row = connection.execute(
                    "SELECT id FROM agent_conversations WHERE id = ?",
                    (int(conversation_id),),
                ).fetchone()
                if row is not None:
                    return int(row["id"])
            now = _utc_now()
            cursor = connection.execute(
                "INSERT INTO agent_conversations(title, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (str(title or "")[:80], now, now),
            )
            return int(cursor.lastrowid)

    def list_conversations(self, limit: int = 30) -> list[ConversationSummary]:
        """Most recently updated conversations with their turn counts."""

        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM agent_conversations c
                LEFT JOIN agent_messages m ON m.conversation_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            ConversationSummary(
                id=int(row["id"]),
                title=str(row["title"] or ""),
                message_count=int(row["message_count"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def get_conversation(self, conversation_id: int) -> ConversationSummary | None:
        with self._database._connection() as connection:
            row = connection.execute(
                """
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM agent_conversations c
                LEFT JOIN agent_messages m ON m.conversation_id = c.id
                WHERE c.id = ?
                GROUP BY c.id
                """,
                (int(conversation_id),),
            ).fetchone()
        if row is None:
            return None
        return ConversationSummary(
            id=int(row["id"]),
            title=str(row["title"] or ""),
            message_count=int(row["message_count"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    # ------------------------------------------------------------------ turns
    def record_turn(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
        status: str = "success",
        mode: str = "",
        model: str = "",
        citations: tuple[str, ...] | list[str] = (),
        failure_code: str = "",
    ) -> int:
        """Insert one question/answer turn and touch the conversation."""

        if role not in MESSAGE_ROLES:
            raise AgentChatHistoryError("对话角色必须是 question/answer。")
        if status not in MESSAGE_STATUSES:
            raise AgentChatHistoryError("对话状态必须是 success/no_evidence/failed。")
        if role == "answer" and status == "failed" and content.strip():
            raise AgentChatHistoryError(
                "失败状态的回答不允许携带内容（不得伪造答案）。"
            )
        citations_json = json.dumps(
            [str(c) for c in citations if str(c).strip()],
            ensure_ascii=False,
        )
        with self._database._connection() as connection:
            exists = connection.execute(
                "SELECT id FROM agent_conversations WHERE id = ?",
                (int(conversation_id),),
            ).fetchone()
            if exists is None:
                raise AgentChatHistoryError("会话不存在，无法记录对话。")
            now = _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO agent_messages(
                    conversation_id, role, content, status, mode, model,
                    citations_json, failure_code, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(conversation_id),
                    role,
                    str(content or ""),
                    status,
                    str(mode or ""),
                    str(model or ""),
                    citations_json,
                    str(failure_code or ""),
                    now,
                ),
            )
            connection.execute(
                "UPDATE agent_conversations SET updated_at = ? WHERE id = ?",
                (now, int(conversation_id)),
            )
            return int(cursor.lastrowid)

    def record_exchange(
        self,
        *,
        conversation_id: int,
        question: str,
        answer: str,
        status: str = "success",
        mode: str = "",
        model: str = "",
        citations: tuple[str, ...] | list[str] = (),
        failure_code: str = "",
    ) -> None:
        """Persist one question + answer pair (answer optional on failure)."""

        self.record_turn(
            conversation_id=conversation_id,
            role="question",
            content=question,
            status="success",
            mode=mode,
        )
        self.record_turn(
            conversation_id=conversation_id,
            role="answer",
            content=answer,
            status=status,
            mode=mode,
            model=model,
            citations=citations,
            failure_code=failure_code,
        )

    def latest_exchange(self, conversation_id: int) -> tuple[ChatTurn, ChatTurn | None]:
        """Return the newest question turn and its paired answer (if any)."""

        turns = self.list_turns(conversation_id)
        latest_question: ChatTurn | None = None
        for turn in reversed(turns):
            if turn.role == "question":
                latest_question = turn
                break
        if latest_question is None:
            raise AgentChatHistoryError("这个会话还没有任何对话。")
        answer: ChatTurn | None = None
        for turn in turns:
            if turn.role == "answer" and turn.created_at >= latest_question.created_at:
                answer = turn
        return latest_question, answer

    def list_turns(self, conversation_id: int) -> list[ChatTurn]:
        with self._database._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, role, content, status, mode, model,
                       citations_json, failure_code, created_at
                FROM agent_messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (int(conversation_id),),
            ).fetchall()
        turns: list[ChatTurn] = []
        for row in rows:
            try:
                citations = tuple(json.loads(row["citations_json"] or "[]"))
            except (TypeError, ValueError):
                citations = ()
            turns.append(
                ChatTurn(
                    id=int(row["id"]),
                    conversation_id=int(row["conversation_id"]),
                    role=str(row["role"]),
                    content=str(row["content"] or ""),
                    status=str(row["status"] or "success"),
                    mode=str(row["mode"] or ""),
                    model=str(row["model"] or ""),
                    citations=tuple(str(c) for c in citations),
                    failure_code=str(row["failure_code"] or ""),
                    created_at=str(row["created_at"]),
                )
            )
        return turns


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
