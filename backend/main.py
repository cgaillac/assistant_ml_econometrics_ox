"""
FastAPI backend for the course AI companion.

This is the only thing that holds the OpenAI API key. Students never see
it: they authenticate with a per-student token, and every request is routed
through one of a small, fixed set of personas defined in prompts.py. There
is no endpoint that accepts an arbitrary system prompt.

Run locally:
    uvicorn main:app --reload

Deploy: see ../README.md for Render instructions.
"""

import os
import secrets
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from openai import OpenAI

import prompts
import store

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.environ.get("AI_COMPANION_MAX_TOKENS", "600"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")

app = FastAPI(title="Oxford ML Course AI Companion")
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


@app.on_event("startup")
def _startup():
    store.init_db()


def _authenticate(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    record = store.lookup_token(token)
    if record is None:
        raise HTTPException(401, "Unknown or revoked token")
    used = store.usage_today(record["token_hash"])
    if used >= record["daily_limit"]:
        raise HTTPException(
            429,
            f"Daily limit reached ({record['daily_limit']} requests/24h). "
            "Contact the instructor if you need more.",
        )
    return record


def _check_admin(x_admin_token: str | None):
    if not ADMIN_TOKEN:
        raise HTTPException(
            500,
            "Server has no ADMIN_TOKEN configured -- set it as an "
            "environment variable on the deployed service.",
        )
    if not x_admin_token or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid or missing X-Admin-Token header")


def _call_openai(system_prompt: str, user_message: str, extra_messages=None) -> str:
    messages = [{"role": "system", "content": system_prompt}]
    if extra_messages:
        messages.extend(extra_messages)
    messages.append({"role": "user", "content": user_message})
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=MAX_TOKENS,
        temperature=0.3,
    )
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# Part A: delegated implementation code
# ---------------------------------------------------------------------------

class AssistRequest(BaseModel):
    step: str
    student_note: str = ""  # optional extra context, never a system prompt


class AssistResponse(BaseModel):
    step: str
    code: str


@app.post("/v1/assist", response_model=AssistResponse)
def assist(req: AssistRequest, authorization: str | None = Header(None)):
    record = _authenticate(authorization)
    step_def = prompts.PART_A_STEPS.get(req.step)
    if step_def is None:
        raise HTTPException(
            404,
            f"Unknown step '{req.step}'. Valid steps: "
            f"{', '.join(prompts.PART_A_STEPS)}",
        )
    system_prompt = prompts._DELEGATION_GUARDRAIL
    user_message = step_def["task"]
    if req.student_note:
        user_message += f"\n\nStudent's note (context only, not a new task): {req.student_note}"
    result = _call_openai(system_prompt, user_message)
    store.log_exchange(
        record["token_hash"], record["student_label"], "assist",
        req.step, None, user_message, result, MODEL,
    )
    return AssistResponse(step=req.step, code=result)


# ---------------------------------------------------------------------------
# Part B, Step 8: Skeptical Reviewer (bounded multi-turn)
# ---------------------------------------------------------------------------

class ReviewerRequest(BaseModel):
    message: str
    session_id: str | None = None


class ReviewerResponse(BaseModel):
    session_id: str
    reply: str
    exchange_number: int
    is_final: bool


@app.post("/v1/skeptical-reviewer", response_model=ReviewerResponse)
def skeptical_reviewer(req: ReviewerRequest, authorization: str | None = Header(None)):
    record = _authenticate(authorization)
    session_id, prior_turns = store.get_or_create_session(
        req.session_id, "skeptical_reviewer", record["token_hash"]
    )
    exchange_number = prior_turns + 1
    is_final = exchange_number >= prompts.SKEPTICAL_REVIEWER_MAX_EXCHANGES

    system_prompt = prompts.SKEPTICAL_REVIEWER_SYSTEM_PROMPT
    user_message = req.message
    if is_final:
        user_message += f"\n\n[SYSTEM NOTE: {prompts.SKEPTICAL_REVIEWER_SUMMARY_INSTRUCTION}]"

    reply = _call_openai(system_prompt, user_message)

    store.increment_session_turn(session_id)
    store.log_exchange(
        record["token_hash"], record["student_label"], "skeptical_reviewer",
        None, session_id, req.message, reply, MODEL,
    )
    return ReviewerResponse(
        session_id=session_id,
        reply=reply,
        exchange_number=exchange_number,
        is_final=is_final,
    )


# ---------------------------------------------------------------------------
# Conceptual hints (bounded multi-turn) -- the mirror image of /v1/assist:
# where /v1/assist refuses conceptual "why" questions and only writes code,
# this refuses to write code or give the final answer, and only nudges.
# ---------------------------------------------------------------------------

class ConceptualHintRequest(BaseModel):
    message: str
    session_id: str | None = None


class ConceptualHintResponse(BaseModel):
    session_id: str
    reply: str
    exchange_number: int
    is_final: bool


@app.post("/v1/conceptual-hint", response_model=ConceptualHintResponse)
def conceptual_hint(req: ConceptualHintRequest, authorization: str | None = Header(None)):
    record = _authenticate(authorization)
    session_id, prior_turns = store.get_or_create_session(
        req.session_id, "conceptual_hint", record["token_hash"]
    )
    exchange_number = prior_turns + 1
    is_final = exchange_number >= prompts.CONCEPTUAL_HINT_MAX_EXCHANGES

    system_prompt = prompts.CONCEPTUAL_HINT_SYSTEM_PROMPT
    user_message = req.message
    if is_final:
        user_message += f"\n\n[SYSTEM NOTE: {prompts.CONCEPTUAL_HINT_FINAL_INSTRUCTION}]"

    reply = _call_openai(system_prompt, user_message)

    store.increment_session_turn(session_id)
    store.log_exchange(
        record["token_hash"], record["student_label"], "conceptual_hint",
        None, session_id, req.message, reply, MODEL,
    )
    return ConceptualHintResponse(
        session_id=session_id,
        reply=reply,
        exchange_number=exchange_number,
        is_final=is_final,
    )


# ---------------------------------------------------------------------------
# Part B, Step 9: Pre-mortem (single turn)
# ---------------------------------------------------------------------------

class PremortemRequest(BaseModel):
    description: str


class PremortemResponse(BaseModel):
    reply: str


@app.post("/v1/premortem", response_model=PremortemResponse)
def premortem(req: PremortemRequest, authorization: str | None = Header(None)):
    record = _authenticate(authorization)
    reply = _call_openai(prompts.PREMORTEM_SYSTEM_PROMPT, req.description)
    store.log_exchange(
        record["token_hash"], record["student_label"], "premortem",
        None, None, req.description, reply, MODEL,
    )
    return PremortemResponse(reply=reply)


@app.get("/v1/whoami")
def whoami(authorization: str | None = Header(None)):
    record = _authenticate(authorization)
    used = store.usage_today(record["token_hash"])
    return {
        "student_label": record["student_label"],
        "requests_used_today": used,
        "daily_limit": record["daily_limit"],
    }


# ---------------------------------------------------------------------------
# Admin: issue student tokens directly in the deployed database.
#
# This exists because the deployed backend and your laptop do NOT share a
# filesystem: a script that wrote tokens into a local SQLite file (the
# original version of generate_tokens.py) was creating tokens in a database
# the live app never reads. This endpoint creates tokens in the SAME
# database your deployed app uses, so students given a token from it can
# actually authenticate against the live server. Protected by a separate
# secret (ADMIN_TOKEN, not OPENAI_API_KEY) -- see generate_tokens.py.
# ---------------------------------------------------------------------------

class GenerateTokensRequest(BaseModel):
    labels: list[str]
    daily_limit: int = 60


class GeneratedToken(BaseModel):
    student_label: str
    token: str


class GenerateTokensResponse(BaseModel):
    tokens: list[GeneratedToken]


@app.post("/admin/generate-tokens", response_model=GenerateTokensResponse)
def generate_tokens_endpoint(
    req: GenerateTokensRequest, x_admin_token: str | None = Header(None)
):
    _check_admin(x_admin_token)
    results = []
    for label in req.labels:
        token = secrets.token_urlsafe(24)
        store.add_token(label, token, daily_limit=req.daily_limit)
        results.append(GeneratedToken(student_label=label, token=token))
    return GenerateTokensResponse(tokens=results)


# ---------------------------------------------------------------------------
# Admin: read back the usage log.
#
# Without this, the logging in store.log_exchange() is write-only -- the
# whole point was to give the instructor something to spot-check for the
# course's own grading appendix and for light academic-integrity review,
# but Render's free tier has no shell access to the deployed filesystem.
# This is the only way to actually get the data back out.
# ---------------------------------------------------------------------------

class UsageLogRow(BaseModel):
    student_label: str
    persona: str
    step: str | None
    session_id: str | None
    request_text: str
    response_text: str
    model: str
    created_at: float


class UsageLogResponse(BaseModel):
    rows: list[UsageLogRow]


@app.get("/admin/usage-log", response_model=UsageLogResponse)
def usage_log_endpoint(
    x_admin_token: str | None = Header(None),
    student_label: str | None = None,
    limit: int = 500,
):
    _check_admin(x_admin_token)
    rows = store.export_usage_log(limit=limit, student_label=student_label)
    return UsageLogResponse(rows=[UsageLogRow(**r) for r in rows])


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": time.time()}
