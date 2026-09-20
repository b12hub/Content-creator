"""Spelling variants used by QA to check that strategic_selection references the brief.
Matching is prefix-based with a word-start boundary, so Russian case endings are tolerated
(плов → плова, плову) without matching inside other words (каза ≠ показать)."""
from __future__ import annotations

import re

DISH_ALIASES: dict[str, list[str]] = {
    "osh": ["плов", "палов", "ош", "osh", "palov", "pilaf"],
    "shashlik": ["шашлык", "шашлы", "кабоб", "shashlik", "kabob"],
    "somsa": ["самса", "сомса", "самсу", "somsa", "samsa"],
    "kazan_kabob": ["казан-кебаб", "казан кебаб", "казанкебаб", "казан-кабоб", "qozon kabob", "qozonkabob"],
    "lagmon": ["лагман", "лагмон", "lagmon", "lag'mon", "lag‘mon", "lagman"],
    "manti": ["манты", "манти", "мант", "manti"],
    "norin": ["нарын", "норин", "norin", "naryn"],
    "sumalak": ["сумаляк", "сумалак", "sumalak"],
    "shurva": ["шурпа", "шурва", "шорпо", "шорва", "sho'rva", "sho‘rva", "shorva", "shurpa"],
}

SEASON_ALIASES: dict[str, list[str]] = {
    "Winter": ["зим", "qish"], "Spring": ["весн", "весен", "bahor"],
    "Summer": ["лет", "лето", "yoz"], "Autumn": ["осен", "осён", "kuz"],
}

EVENT_ALIASES: dict[str, list[str]] = {
    "ramadan": ["рамадан", "рамазан", "ифтар", "ураз", "пост", "ramazon", "iftor"],
    "ramazan_hayit": ["хайит", "ураза", "байрам", "hayit"],
    "kurban_hayit": ["курбан", "хайит", "байрам", "qurbon", "hayit"],
    "navruz": ["навруз", "новруз", "навро", "navro"],
    "new_year": ["новый год", "новогод", "нового года", "новом году", "yangi yil"],
    "chilla_summer": ["чилл", "chilla", "жар", "зной"],
    "summer_season": ["жар", "зной"],
    "autumn_weddings": ["свадьб", "свадеб", "той", "to'y", "to‘y"],
    "independence_day": ["независим", "mustaqil"],
    "teachers_day": ["учител", "o'qituvchi", "o‘qituvchi"],
    "womens_day": ["8 марта", "женск", "мам"],
    "memorial_day": ["памят", "xotira"],
    "street_food_nights": ["уличн", "ночн", "вечер"],
    "choyxona_opening": ["чайхан", "choyxona"],
    "spring_blossoms": ["цвет", "весн", "весен"],
    "spring_preparation": ["весн", "весен"],
    "winter_cold_season": ["зим", "холод", "мороз"],
    "harvest_period": ["урожа", "hosil"],
    "homeland_defenders_day": ["защитник", "vatan"],
}

TEMPERATURE_RE = re.compile(r"[+\-−]?\d+\s*(?:°|градус|daraja)|(?<!\w)(?:жар|зной|мороз|холод|тепл|issiq|sovuq)",
                            re.IGNORECASE)


def _stem_pattern(alias: str) -> str:
    a = alias.lower()
    if len(a) <= 3:                                   # short words must match whole: "ош", "той", "лет"
        return r"(?<!\w)" + re.escape(a) + r"(?!\w)" if len(a) < 3 else r"(?<!\w)" + re.escape(a)
    return r"(?<!\w)" + re.escape(a[: max(4, len(a) - 1)])


def mentions_any(text: str, aliases: list[str]) -> bool:
    t = text.lower()
    return any(re.search(_stem_pattern(a), t) for a in aliases if a)


def dish_aliases(key: str, name_ru: str, name_uz: str) -> list[str]:
    extra = [w for n in (name_ru, name_uz) for w in re.findall(r"[\w'‘-]{3,}", n)]
    return DISH_ALIASES.get(key, []) + extra


def context_aliases(season: str | None, event_key: str | None, event_name_ru: str | None) -> list[str]:
    out = list(SEASON_ALIASES.get(season or "", []))
    if event_key:
        out += EVENT_ALIASES.get(event_key, [])
    if event_name_ru:
        out += [w for w in re.findall(r"\w{5,}", event_name_ru)]
    return out
