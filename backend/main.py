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
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from openai import OpenAI

import prompts
import store

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.environ.get("AI_COMPANION_MAX_TOKENS", "600"))

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


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": time.time()}
