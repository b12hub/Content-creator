import copy

import pytest

from app.llm.base import LLMRefusal
from tests.conftest import load

ROUTES = [
    ("chilla_43C", "PEPSI_BEHAVIORAL_DOPAMINE", "dopamine_behavioral", "ZARBA"),
    ("ramadan_iftar", "COCA_COLA_EMOTIONAL", "hippocampus_emotional", "USTOZ"),
    ("wedding_norin", "COCA_COLA_EMOTIONAL", "hippocampus_emotional", "USTOZ"),
    ("navruz", "HYBRID_SPRING_RENEWAL", "hybrid_spring_renewal", "BAHOR"),
]


@pytest.mark.parametrize("fixture,framework,handler,persona", ROUTES)
def test_deterministic_routing(make_client, fixture, framework, handler, persona):
    client, fake = make_client()
    r = client.post("/generate-reel-script", json=load(fixture))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["framework"] == framework
    assert body["meta"]["handler"] == handler
    assert persona in fake.calls[0]["system"]
    # the other personas must NOT leak into this route's prompt
    for other in {"ZARBA", "USTOZ", "BAHOR"} - {persona}:
        assert other not in fake.calls[0]["system"]
    assert set(body) >= {"strategic_selection", "visual_hook", "script", "call_to_action", "meta", "quality"}
    assert set(body["script"]) == {"ru", "uz", "audio_cues"}


def test_unknown_framework_is_422(make_client):
    client, _ = make_client()
    p = load("navruz"); p["framework"]["name"] = "RANDOM"
    assert client.post("/generate-reel-script", json=p).status_code == 422


def test_empty_dishes_is_422(make_client):
    client, _ = make_client()
    p = load("navruz"); p["dishes"] = []
    assert client.post("/generate-reel-script", json=p).status_code == 422


def test_self_repair_on_cyrillic_uzbek(make_client, good):
    bad = copy.deepcopy(good); bad["script"]["uz"]["voiceover"] = "Бир култум BioLife"
    client, fake = make_client([bad, good])
    r = client.post("/generate-reel-script", json=load("chilla_43C"))
    assert r.status_code == 200, r.text
    q = r.json()["quality"]
    assert q["attempts"] == 2 and any("Latin" in i for i in q["issues_fixed_on_retry"])
    assert "REJECTED BY QA" in fake.calls[1]["user"]
    assert fake.calls[1]["user"].count("REJECTED BY QA") == 1


def test_competitor_leak_fails_after_retries(make_client, good):
    bad = copy.deepcopy(good); bad["script"]["ru"]["voiceover"] = "Лучше чем Pepsi. BioLife!"
    client, _ = make_client([bad, bad])
    r = client.post("/generate-reel-script", json=load("chilla_43C"))
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "quality_check_failed"


def test_medical_and_alcohol_claims_rejected(make_client, good):
    bad = copy.deepcopy(good); bad["script"]["ru"]["voiceover"] = "BioLife - детокс после пива"
    client, _ = make_client([bad, bad])
    issues = client.post("/generate-reel-script", json=load("chilla_43C")).json()["detail"]["issues"]
    assert any("Medical" in i for i in issues) and any("Alcohol" in i for i in issues)


def test_invented_cta_channel_rejected(make_client):
    from app.llm.fake_client import default_payload
    good = default_payload("USTOZ", "")
    good["strategic_selection"] = "Нарын на свадьбе осенью: эмоциональный угол."
    bad = copy.deepcopy(good); bad["call_to_action"]["channel_key"] = "instagram_shop"
    client, _ = make_client([bad, good])
    r = client.post("/generate-reel-script", json=load("wedding_norin"))
    assert r.status_code == 200 and r.json()["quality"]["attempts"] == 2


def test_refusal_is_502(make_client):
    client, _ = make_client([LLMRefusal("no")])
    r = client.post("/generate-reel-script", json=load("navruz"))
    assert r.status_code == 502 and r.json()["detail"]["error"] == "llm_refusal"


def test_ramadan_prompt_and_review_flag(make_client, good):
    client, fake = make_client()
    r = client.post("/generate-reel-script", json=load("ramadan_iftar"))
    sys = fake.calls[0]["system"]
    assert "RAMADAN SCENE LAW" in sys
    assert "Never depict eating or drinking during daylight fasting hours" in sys
    q = r.json()["quality"]
    assert q["review_required"] is True
    assert any("Religious period" in x for x in q["review_reasons"])
    assert any("estimate" in x for x in q["review_reasons"])


def test_ramadan_rejects_discount_language(make_client, good):
    bad = copy.deepcopy(good); bad["call_to_action"]["ru"] = "Скидка 20% на BioLife в Telegram-боте"
    client, _ = make_client([bad, bad])
    r = client.post("/generate-reel-script", json=load("ramadan_iftar"))
    assert r.status_code == 502


def test_dopamine_prompt_uses_live_context(make_client):
    client, fake = make_client()
    client.post("/generate-reel-script", json=load("chilla_43C"))
    sys, user = fake.calls[0]["system"], fake.calls[0]["user"]
    assert "+43°C" in sys and "#BetterWithBioLife" in sys
    assert "Osh (Palov)" in sys                       # user-selected dish drives the CUE
    assert "SKU to feature: 0.5L PET" in user
    assert "MAX 44 words per language" in user        # 160 wpm * 15s / 60 * 1.1 = 44
    assert "telegram_bot" in user
    for p in (sys, user):
        assert "{ctx" not in p and "{dish" not in p   # no unrendered template vars


def test_hybrid_navruz_ritual(make_client):
    client, fake = make_client()
    client.post("/generate-reel-script", json=load("navruz"))
    assert "sumalak kazan" in fake.calls[0]["system"]


def test_health(make_client):
    client, _ = make_client()
    assert client.get("/health").json()["status"] == "ok"


def test_route_boundary_enforced_emotional(make_client, good):
    bad = copy.deepcopy(good)   # default payload contains "ледяного" / "muzdek"
    client, _ = make_client([bad, bad])
    r = client.post("/generate-reel-script", json=load("wedding_norin"))
    assert r.status_code == 502
    assert any("psychological boundaries" in i for i in r.json()["detail"]["issues"])


def test_uz_cta_label_has_no_cyrillic():
    from app.config import get_settings
    import re
    for c in get_settings().cta_channels:
        assert not re.search(r"[А-Яа-яЁё]", c.label_uz), c.key
