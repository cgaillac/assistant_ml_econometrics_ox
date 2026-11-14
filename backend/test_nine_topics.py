"""
Regression test for the backend serving two courses / nine topics:
classification, shrinkage, bandits, conformal, nlp, dml (course 1,
"Foundations of Machine Learning"), and hte, opl, fairness (course 2,
"Advanced Machine Learning", Christophe Gaillac -- hte is that course's
own "Course 1" on CATE estimation, the Gender Wage Gap application; opl
is its "Course 2" on optimal policy learning, the Hillstrom e-mail-
targeting application; fairness is its "Course 6" on Ethics and ML --
NOT a sequel to hte/opl the way opl was hte's sequel, but an independent
third tutorial of the same course, built on a COMPAS pretrial-risk model).

Extends test_eight_topics.py with the new "fairness" topic, to confirm
what this project's architecture has claimed at every step since the
second topic was added: a new topic is just new dict entries in
prompts.py -- main.py, store.py, and client.py need no changes, and
topics stay fully isolated from each other.

Uses FastAPI's TestClient against the real app, with `_call_openai`
monkeypatched so no real OpenAI call is made and no API key is needed.
Run with: ADMIN_TOKEN=test-secret OPENAI_API_KEY=sk-test python -m pytest test_nine_topics.py -v
"""
import os

os.environ.setdefault("ADMIN_TOKEN", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")

import pytest
from fastapi.testclient import TestClient

import main
import prompts
import tokens

TOPICS = ["classification", "shrinkage", "bandits", "conformal", "nlp", "dml", "hte", "opl", "fairness"]

STEP_PREFIX = {
    "classification": None,  # unprefixed steps (2_split, 3_logreg, ...)
    "shrinkage": "sh",
    "bandits": "bd",
    "conformal": "cf",
    "nlp": "nlp",
    "dml": "dml",
    "hte": "hte",
    "opl": "opl",
    "fairness": "fair",
}


@pytest.fixture(autouse=True)
def _fake_openai(monkeypatch):
    """Replace the real OpenAI call with an echo of the system prompt, so
    each test can assert on exactly which persona/step text the backend
    would have sent, without any network access or API key."""

    def _fake_call(system_prompt, user_message, extra_messages=None):
        return f"[ECHO] system={system_prompt[:0]}len={len(system_prompt)}|user_len={len(user_message)}"

    monkeypatch.setattr(main, "_call_openai", _fake_call)
    yield


@pytest.fixture
def client():
    main.store.init_db()
    return TestClient(main.app)


@pytest.fixture
def auth_header():
    token = tokens.issue_token("test_student")
    return {"Authorization": f"Bearer {token}"}


def test_all_nine_topics_have_at_least_one_step(client, auth_header):
    resp = client.get("/v1/steps", headers=auth_header)
    assert resp.status_code == 200
    steps = resp.json()
    for topic, prefix in STEP_PREFIX.items():
        if prefix is None:
            matching = [s for s in steps if s[0].isdigit()]
        else:
            matching = [s for s in steps if s.startswith(prefix)]
        assert matching, f"no PART_A_STEPS entries found for topic '{topic}'"


def test_fair_steps_present_and_isolated_from_opl_and_hte():
    """fairness is a THIRD, independent tutorial of the same course as hte
    and opl (not a sequel to either) -- confirm its step-id namespace
    doesn't collide with either, and that the 'fair' prefix wasn't
    accidentally matched by anything already starting with those other
    letters."""
    fair_steps = [k for k in prompts.PART_A_STEPS if k.startswith("fair")]
    hte_steps = [k for k in prompts.PART_A_STEPS if k.startswith("hte")]
    opl_steps = [k for k in prompts.PART_A_STEPS if k.startswith("opl")]
    assert len(fair_steps) >= 4
    assert set(fair_steps).isdisjoint(hte_steps)
    assert set(fair_steps).isdisjoint(opl_steps)
    for key in fair_steps:
        assert "label" in prompts.PART_A_STEPS[key]
        assert "task" in prompts.PART_A_STEPS[key]
        assert len(prompts.PART_A_STEPS[key]["task"]) > 50


def test_assist_endpoint_accepts_fair_step(client, auth_header):
    resp = client.post(
        "/v1/assist", json={"step": "fair2_groupmetrics"}, headers=auth_header
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["step"] == "fair2_groupmetrics"
    assert isinstance(body["code"], str)


def test_assist_endpoint_rejects_unknown_fair_step(client, auth_header):
    resp = client.post("/v1/assist", json={"step": "fair99_nonexistent"}, headers=auth_header)
    assert resp.status_code == 404
    assert "fair2_groupmetrics" in resp.json()["detail"]


@pytest.mark.parametrize("topic", TOPICS)
def test_skeptical_reviewer_accepts_every_topic(client, auth_header, topic):
    resp = client.post(
        "/v1/skeptical-reviewer",
        json={"topic": topic, "message": "Here is my interpretation..."},
        headers=auth_header,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exchange_number"] == 1
    assert body["is_final"] is False


def test_skeptical_reviewer_rejects_unknown_topic(client, auth_header):
    resp = client.post(
        "/v1/skeptical-reviewer",
        json={"topic": "not_a_real_topic", "message": "hello"},
        headers=auth_header,
    )
    assert resp.status_code == 404
    assert "fairness" in resp.json()["detail"]  # valid-topics list should include the new one


def test_skeptical_reviewer_sessions_are_isolated_by_topic(client, auth_header):
    """A student working on hte, opl, AND fairness in the same sitting
    must get three independent bounded dialogues, not a shared session."""
    r1 = client.post(
        "/v1/skeptical-reviewer", json={"topic": "hte", "message": "hte take 1"}, headers=auth_header
    )
    r2 = client.post(
        "/v1/skeptical-reviewer", json={"topic": "opl", "message": "opl take 1"}, headers=auth_header
    )
    r3 = client.post(
        "/v1/skeptical-reviewer", json={"topic": "fairness", "message": "fairness take 1"}, headers=auth_header
    )
    ids = {r1.json()["session_id"], r2.json()["session_id"], r3.json()["session_id"]}
    assert len(ids) == 3
    assert r1.json()["exchange_number"] == 1
    assert r2.json()["exchange_number"] == 1
    assert r3.json()["exchange_number"] == 1  # fairness's own session starts fresh

    r1b = client.post(
        "/v1/skeptical-reviewer",
        json={"topic": "hte", "message": "hte take 2", "session_id": r1.json()["session_id"]},
        headers=auth_header,
    )
    assert r1b.json()["exchange_number"] == 2


def test_reusing_opl_session_id_under_fairness_self_heals(client, auth_header):
    """The round-7 self-heal fix: passing another topic's session_id must
    mint a fresh session, not replay or extend that topic's history."""
    r_opl = client.post(
        "/v1/skeptical-reviewer", json={"topic": "opl", "message": "opl take 1"}, headers=auth_header
    )
    r_cross = client.post(
        "/v1/skeptical-reviewer",
        json={"topic": "fairness", "message": "trying to reuse opl's session",
              "session_id": r_opl.json()["session_id"]},
        headers=auth_header,
    )
    assert r_cross.json()["session_id"] != r_opl.json()["session_id"]
    assert r_cross.json()["exchange_number"] == 1


def test_fairness_skeptical_reviewer_prompt_is_distinct_from_hte_and_opl():
    fair_prompt = prompts.SKEPTICAL_REVIEWER_PROMPTS["fairness"]
    assert fair_prompt != prompts.SKEPTICAL_REVIEWER_PROMPTS["hte"]
    assert fair_prompt != prompts.SKEPTICAL_REVIEWER_PROMPTS["opl"]
    assert "compas" in fair_prompt.lower()
    assert "shap" in fair_prompt.lower()


def test_delegation_guardrail_is_shared_and_course_agnostic():
    """The guardrail is sent verbatim regardless of topic/course -- it must
    not hardcode one specific course's name now that a second course (and
    a third tutorial within it) uses it, and it must still forbid
    answering conceptual questions."""
    assert "foundations of machine learning" not in prompts._DELEGATION_GUARDRAIL.lower()
    assert "do not answer any conceptual" in prompts._DELEGATION_GUARDRAIL.lower()


def test_list_steps_has_no_duplicate_keys_across_topics():
    keys = list(prompts.PART_A_STEPS.keys())
    assert len(keys) == len(set(keys))


def test_ai_student_endpoint_needs_no_topic_argument(client, auth_header):
    """The AI-student persona is already topic-agnostic by design (round
    5/14's finding, re-checked here for the 9th topic): it is callable
    with no `topic` field at all, and adding fairness needed no change to
    this endpoint or its prompt."""
    resp = client.post(
        "/v1/ai-student", json={"message": "here's my explanation..."}, headers=auth_header
    )
    assert resp.status_code == 200


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
