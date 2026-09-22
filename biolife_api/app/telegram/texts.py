"""Uzbek (Latin) copy. This is an internal tool for the BioLife marketing team — one language only."""
from __future__ import annotations

from typing import Final

START: Final = (
    "Assalomu alaykum! Men BioLife AI Kopirayteriman.\n\n"
    "• Bugungi tayyor ssenariy uchun — /create\n"
    "• Yoki o‘z briefingizni oddiy matn qilib yuboring "
    "(masalan: «Jazirama issiq uchun 15 soniyalik Reels, yoshlar uchun»)."
)

HELP: Final = (
    "<b>Qanday ishlaydi</b>\n"
    "1) /create — bugungi sana, oy va fasl bo‘yicha tayyor ssenariy\n"
    "2) Istalgan matn — sizning briefingiz bo‘yicha ssenariy\n"
    "3) Ssenariy ostidagi tugmalar:\n"
    "   🎬 AI video generatorlar uchun prompt\n"
    "   ✏️ ssenariyni tahrirlash\n\n"
    "/cancel — tahrirlash rejimidan chiqish\n"
    "/help — shu yordam"
)

THINKING: Final = "O‘ylayapman... ⏳"
THINKING_VIDEO: Final = "AI video prompt tayyorlayapman... 🎬"
THINKING_EDIT: Final = "Ssenariyni qayta yozyapman... ✏️"
ASK_EDITS: Final = ("Ssenariyga qanday o‘zgartirishlar kiritmoqchisiz? Izohingizni yuboring...\n"
                    "(bekor qilish uchun — /cancel)")
CANCELLED: Final = "Bekor qilindi. Yangi ssenariy uchun /create yoki briefingizni yuboring."
NO_ACTIVE_SCRIPT: Final = ("Faol ssenariy topilmadi. Avval /create yuboring yoki briefingizni yozing.")
BRIEF_TOO_LONG: Final = "Brief juda uzun ({limit} belgidan oshmasin). Qisqartirib yuboring."
TEXT_ONLY: Final = "Hozircha faqat matnli brieflarni qabul qilaman."
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
