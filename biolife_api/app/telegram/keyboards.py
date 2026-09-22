"""Inline keyboards. Callback data are fixed strings, as specified by the product spec."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CB_VIDEO_PROMPT = "btn_gen_video_prompt"
CB_EDIT_SCRIPT = "btn_edit_script"
CB_SAVE_FINAL = "btn_save_final"


def script_actions_keyboard() -> InlineKeyboardMarkup:
    """Attached to every generated script."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 AI Video Prompt tayyorlash", callback_data=CB_VIDEO_PROMPT)
    kb.button(text="✏️ Ssenariyni tahrirlash", callback_data=CB_EDIT_SCRIPT)
    kb.button(text="💾 Tasdiqlash va Saqlash", callback_data=CB_SAVE_FINAL)
    kb.adjust(1)
    return kb.as_markup()
