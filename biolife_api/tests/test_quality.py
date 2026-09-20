"""Regression tests for QA false positives/negatives found in review."""
import copy

import pytest

from app.config import get_settings
from app.llm.fake_client import default_payload
from app.models.context import AdContext
from app.models.script_package import ReelScriptLLM
from app.services.quality import evaluate, word_count
from tests.conftest import load


def _run(fixture, mutate, persona=""):
    p = copy.deepcopy(default_payload(persona, ""))
    mutate(p)
    ctx = AdContext.model_validate(load(fixture))
    return evaluate(ctx, ReelScriptLLM.model_validate(p), get_settings())


@pytest.mark.parametrize("phrase", ["Сжигающее солнце. BioLife 0.5L", "Shifokorlar ham BioLife 0.5L ichadi"])
def test_heat_and_doctor_words_are_not_medical_claims(phrase):
    def m(p):
        p["script"]["ru"]["voiceover"] = phrase if "Сж" in phrase else p["script"]["ru"]["voiceover"]
        p["script"]["uz"]["voiceover"] = phrase if "Shifo" in phrase else p["script"]["uz"]["voiceover"]
    assert not any("Medical" in i for i in _run("chilla_43C", m).hard_issues)


@pytest.mark.parametrize("phrase", ["BioLife сжигает жир", "Bokal vina? Нет, бокал вина не нужен", "пивной вечер с BioLife"])
def test_real_claims_still_caught(phrase):
    def m(p): p["script"]["ru"]["voiceover"] = phrase
    issues = _run("chilla_43C", m).hard_issues
    assert any("Medical" in i or "Alcohol" in i for i in issues)


def test_dash_is_not_a_word():
    assert word_count("+43°C. Один глоток — и всё меняется") == 6
    assert word_count("Bir qultum - va o‘zgaradi") == 4


def test_family_pack_sku_not_matched_by_temperature():
    def m(p):
        for lang in ("ru", "uz"):
            p["script"][lang]["voiceover"] = p["script"][lang]["voiceover"].replace("1.5L", "") + " +45°C 15"
    r = _run("navruz", m, persona="BAHOR")      # navruz SKU = 5L/10L Family Pack
    assert any("SKU" in x for x in r.review_reasons)

    def m2(p): p["script"]["ru"]["voiceover"] += " Семейная BioLife 5/10 л."
    assert not any("SKU" in x for x in _run("navruz", m2, persona="BAHOR").review_reasons)


def test_zero_voiceover_ok_if_hook_is_russian():
    def m(p):
        p["script"]["ru"]["voiceover"] = ""
        p["script"]["ru"]["on_screen_text"] = ["+43°C", "BioLife 0.5L"]
    assert not any("Cyrillic text" in i for i in _run("chilla_43C", m).hard_issues)


def test_repair_prompt_contains_rejected_draft(make_client):
    good = default_payload("", "")
    bad = copy.deepcopy(good); bad["script"]["uz"]["voiceover"] = "Бир култум BioLife"
    client, fake = make_client([bad, good])
    client.post("/generate-reel-script", json=load("chilla_43C"))
    assert "Бир култум BioLife" in fake.calls[1]["user"]


def test_none_rationale_not_rendered(make_client):
    client, fake = make_client()
    p = load("navruz"); p["framework"]["selection_rationale"] = None
    client.post("/generate-reel-script", json=p)
    assert "why this framework today: None" not in fake.calls[0]["user"]


# ---- Phase 3: strategic_selection must cite the brief (review findings) ----
@pytest.mark.parametrize("fixture,text", [
    ("chilla_43C", "При +43 градусах тяжёлый плов требует холодной воды."),
    ("new_year", "В новогоднюю ночь за столом с пловом BioLife объединяет семью."),
    ("ramadan_iftar", "В Рамазан на ифтаре шурпа и вода для старших."),
    ("navruz", "Весенний Новруз и сумаляк: обновление и традиция."),
    ("chilla_36C", "Шашлыки вечером, летом в жару - контраст огня и льда."),
])
def test_selection_accepts_realistic_russian(fixture, text):
    ctx = AdContext.model_validate(load(fixture))
    p = default_payload("", ""); p["strategic_selection"] = text
    issues = evaluate(ctx, ReelScriptLLM.model_validate(p), get_settings()).hard_issues
    assert not any("strategic_selection" in i for i in issues), issues


def test_selection_rejects_false_dish_match():
    ctx = AdContext.model_validate(load("chilla_36C"))      # dish = shashlik
    p = default_payload("", ""); p["strategic_selection"] = "Чтобы показать жару, берём контраст."
    issues = evaluate(ctx, ReelScriptLLM.model_validate(p), get_settings()).hard_issues
    assert any("featured dish" in i for i in issues)


# ---- length limits (regression: these checks were once lost in a refactor) ----
def _issues(fixture, mutate):
    return _run(fixture, mutate).hard_issues


def test_voiceover_budget_enforced():
    def m(p): p["script"]["ru"]["voiceover"] = "BioLife " + "слово " * 60     # budget for chilla = 44
    assert any("ru voiceover has" in i for i in _issues("chilla_43C", m))


def test_on_screen_and_hook_word_limits_enforced():
    def m(p):
        p["script"]["uz"]["on_screen_text"] = ["bir ikki uch to'rt besh olti yetti sakkiz to'qqiz"]
        p["visual_hook"]["on_screen_text"]["ru"] = "один два три четыре пять шесть семь"
    issues = _issues("chilla_43C", m)
    assert any("uz on-screen text too long" in i for i in issues)
    assert any("ru hook text too long" in i for i in issues)


def test_audio_cue_kinds_required():
    def m(p): p["script"]["audio_cues"] = [{"timecode": "0-3s", "kind": "VOICE", "cue": "голос"}]
    issues = _issues("chilla_43C", m)
    assert any("MUSIC" in i for i in issues) and any("ASMR or FOLEY" in i for i in issues)
