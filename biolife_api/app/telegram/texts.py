"""Uzbek (Latin) copy. This is an internal tool for the BioLife marketing team — one language only."""
from __future__ import annotations

from typing import Final

START: Final = (
    "Assalomu alaykum! Men BioLife AI Kopirayteriman.\n"
    "Bugungi reklama ssenariysini yaratish uchun /create buyrug‘ini yuboring."
)

HELP: Final = (
    "<b>Buyruqlar</b>\n"
    "/create — bugungi sana, oy va fasl bo‘yicha reklama ssenariysi\n"
    "/start — botni qayta ishga tushirish\n"
    "/help — shu yordam"
)

THINKING: Final = "O‘ylayapman... ⏳"
BUSY: Final = "Oldingi so‘rovingiz hali tayyor bo‘lmadi. Bir oz kuting ⏳"
ERROR: Final = "Xatolik yuz berdi. Bir daqiqadan so‘ng qayta urinib ko‘ring."
GENERATION_FAILED: Final = "Ssenariy yaratilmadi: {reason}\nQayta urinib ko‘ring — /create"
UNKNOWN: Final = "Bu buyruqni bilmayman. /create yoki /help ni yuboring."
NO_ACCESS: Final = "Bu bot faqat BioLife marketing jamoasi uchun. Kirish uchun administratorga murojaat qiling."
TOO_FAST: Final = "Juda tez — bir soniya kuting."

MONTHS_UZ: Final[dict[int, str]] = {
    1: "yanvar", 2: "fevral", 3: "mart", 4: "aprel", 5: "may", 6: "iyun",
    7: "iyul", 8: "avgust", 9: "sentyabr", 10: "oktyabr", 11: "noyabr", 12: "dekabr",
}

SEASONS_UZ: Final[dict[str, str]] = {
    "winter": "qish", "spring": "bahor", "summer": "yoz", "autumn": "kuz",
}
