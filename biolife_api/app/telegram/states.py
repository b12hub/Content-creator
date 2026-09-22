"""FSM for the copywriter bot (aiogram 3.x)."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ContentStates(StatesGroup):
    """Only one interactive step: collecting edit feedback for the last script."""

    waiting_for_script_edits = State()


# FSM data keys, in one place so handlers never guess a string
KEY_SCRIPT = "active_script"        # last generated script (plain text)
KEY_BRIEF = "active_brief"          # the brief that produced it
KEY_MSG_ID = "active_message_id"    # message the script was delivered in
