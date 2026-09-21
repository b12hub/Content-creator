"""Helpers for Telegram tests: a fake Bot session that records API calls, and update builders."""
from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import AsyncGenerator
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import Response
from aiogram.types import Chat, Message, User

CHAT_ID = 555_000
USER_ID = 555_000


class RecordingSession(BaseSession):
    """Captures every outgoing Telegram API call instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []

    def method_names(self) -> list[str]:
        return [type(c).__name__ for c in self.calls]

    def texts(self) -> list[str]:
        return [getattr(c, "text", None) or getattr(c, "caption", None) or "" for c in self.calls]

    async def close(self) -> None:  # pragma: no cover
        return None

    async def stream_content(self, url: str, headers: dict[str, Any] | None = None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True
                             ) -> AsyncGenerator[bytes, None]:  # pragma: no cover
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name in {"SendMessage", "EditMessageText"}:
            # .as_(bot) binds the message to the bot, exactly as the real session does - otherwise
            # message.edit_text() raises "This method is not mounted to a any bot instance".
            result: Any = Message(message_id=len(self.calls), date=dt.datetime.now(dt.timezone.utc),
                                  chat=Chat(id=CHAT_ID, type="private"),
                                  from_user=User(id=1, is_bot=True, first_name="BioLife"),
                                  text=getattr(method, "text", "")).as_(bot)
        else:
            result = True
        return Response[Any](ok=True, result=result).result


def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    return Bot(token="42:TEST-TOKEN-FOR-UNIT-TESTS", session=session), session


_counter = itertools.count(1000)


def next_id() -> int:
    return next(_counter)


def _base(update_id: int | None) -> dict[str, Any]:
    return {"update_id": update_id if update_id is not None else next_id()}


def _from_user(language_code: str = "ru") -> dict[str, Any]:
    return {"id": USER_ID, "is_bot": False, "first_name": "Bobur", "language_code": language_code}


def message_update(text: str, update_id: int | None = None, language_code: str = "ru") -> dict[str, Any]:
    entities = ([{"offset": 0, "length": len(text.split()[0]), "type": "bot_command"}]
                if text.startswith("/") else [])
    base = _base(update_id)
    return base | {"message": {
        "message_id": base["update_id"], "date": 1700000000, "chat": {"id": CHAT_ID, "type": "private"},
        "from": _from_user(language_code), "text": text, "entities": entities}}
