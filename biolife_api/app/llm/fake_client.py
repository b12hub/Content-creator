"""Deterministic offline client for tests and local demos (BIOLIFE_LLM_PROVIDER=fake).
Records every call so tests can assert which prompt was used."""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def default_payload(system: str, user: str) -> dict[str, Any]:
    """Route-aware offline demo output (picks tone from the persona in the system prompt)."""
    if "USTOZ" in system:
        hook = {"ru": "Помнишь этот стол?", "uz": "Bu dasturxon esingizdami?"}
        ru = "Мы снова вместе. Первый стакан BioLife - самому старшему."
        uz = "Yana birgamiz. BioLife'ning birinchi piyolasi - eng kattamizga."
        cue = "Тишина, звук наливаемой воды, тихий смех"
        instr = "Крупный план: руки бабушки разглаживают дастархан, 24fps, тёплый свет."
    elif "BAHOR" in system:
        hook = {"ru": "Весна начинается с чистого", "uz": "Bahor tozadan boshlanadi"}
        ru = "Природа просыпается. Семья за одним столом. BioLife 1.5L - чистое начало."
        uz = "Tabiat uyg'onmoqda. Oila bir dasturxonda. BioLife 1.5L - toza boshlanish."
        cue = "Пение птиц, акустическая гитара, лёгкий подъём музыки"
        instr = "Макро: бутон абрикоса раскрывается, мягкий дневной свет, пастельная палитра."
    else:
        hook = {"ru": "Жара? Есть решение", "uz": "Issiqmi? Yechim bor"}
        ru = "Один глоток ледяного BioLife 0.5L - и снова хочется жить."
        uz = "Bir qultum muzdek BioLife 0.5L - va kuch qaytadi."
        cue = "Щелчок крышки, треск льда, бит на первом глотке"
        instr = "Макро: бутылка BioLife 0.5L в конденсате, 120fps, холодный бирюзовый грейд."
    dish = re.search(r"^  - .+? / (.+?) \[", user, re.M)
    temp = re.search(r"temperature: ([+-]?\d+°C)", user)
    selection = (f"Демо-ответ без LLM. Блюдо: {dish.group(1) if dish else 'плов'}; "
                 f"температура {temp.group(1) if temp else '+30°C'}; угол выбран по фреймворку из базы.")
    return {
        "strategic_selection": selection,
        "visual_hook": {"time_range": "0-3s", "shot_type": "макро", "lighting": "естественный свет",
                        "camera_movement": "медленный dolly-in, 24fps",
                        "editor_instructions": instr, "on_screen_text": hook},
        "script": {
            "ru": {"voiceover": ru, "on_screen_text": ["BioLife"]},
            "uz": {"voiceover": uz, "on_screen_text": ["BioLife"]},
            "audio_cues": [{"timecode": "0.0-3.0s", "kind": "FOLEY", "cue": cue},
                           {"timecode": "0.0-15.0s", "kind": "MUSIC", "cue": "Фоновая музыка по брифу"}],
        },
        "call_to_action": {"channel_key": "telegram_bot",
                           "ru": "Закажите BioLife в Telegram-боте BioLife (@BioLifeUz_bot)",
                           "uz": "BioLife'ni BioLife Telegram-boti (@BioLifeUz_bot) orqali buyurtma qiling"},
    }


class FakeLLMClient:
    provider = "fake"
    model = "fake-offline"

    def __init__(self, responses: list[dict[str, Any] | Exception] | None = None,
                 factory: Callable[[str, str], dict[str, Any]] = default_payload):
        self._queue = list(responses or [])
        self._factory = factory
        self.calls: list[dict[str, str]] = []

    async def generate_structured(self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls.append({"system": system, "user": user})
        item = self._queue.pop(0) if self._queue else self._factory(system, user)
        if isinstance(item, Exception):
            raise item
        return schema.model_validate(item)

    async def aclose(self) -> None:  # pragma: no cover
        return None
