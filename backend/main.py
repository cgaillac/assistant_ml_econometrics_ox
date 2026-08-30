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
import re
import time

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from openai import OpenAI

import prompts
import store
import tokens

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOKENS = int(os.environ.get("AI_COMPANION_MAX_TOKENS", "600"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
DEFAULT_DAILY_LIMIT = int(os.environ.get("DEFAULT_DAILY_LIMIT", "60"))

app = FastAPI(title="Oxford ML Course AI Companion")
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


@app.on_event("startup")
def _startup():
    store.init_db()


def _authenticate(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ").strip()

    # Token validity is checked by its signature, NOT by a database lookup --
    # this is what lets tokens survive Render redeploys/restarts, which wipe
    # the SQLite file. See tokens.py.
    student_label = tokens.verify_token(token)
    if student_label is None:
        raise HTTPException(401, "Invalid token")

    token_hash = store.hash_token(token)
    # The DB row is used ONLY as an optional override for a custom daily
    # limit set at issuance time; if it's missing (e.g. the database was
    # reset by a redeploy since this token was issued), fall back to the
    # default rather than rejecting the token.
    record = store.lookup_token(token)
    daily_limit = record["daily_limit"] if record else DEFAULT_DAILY_LIMIT

    used = store.usage_today(token_hash)
    if used >= daily_limit:
        raise HTTPException(
            429,
            f"Daily limit reached ({daily_limit} requests/24h). "
            "Contact the instructor if you need more.",
        )
    return {
        "student_label": student_label,
        "token_hash": token_hash,
        "daily_limit": daily_limit,
    }


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


# Chat models routinely wrap code in a markdown fence even when told not
# to. The client wraps whatever "code" it gets in its OWN fence for
# display, so an unstripped fence from the model produces visibly nested/
# broken fences. These two helpers guarantee a clean split regardless of
# whether the model followed the "no fences, one optional NOTE: line"
# instructions in prompts.py -- belt and suspenders around a prompt
# instruction that's a strong nudge, not a guarantee.

_FENCE_RE = re.compile(r"^```(?:\w+)?\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    # Unbalanced fence (opening ``` with no matching close, or vice versa) --
    # just drop stray fence-only lines rather than leaving them in.
    lines = [ln for ln in text.split("\n") if ln.strip() not in ("```", "```python")]
    return "\n".join(lines).strip()


def _split_trailing_note(text: str) -> tuple[str, str | None]:
    lines = text.rstrip().split("\n")
    if lines and re.match(r"^NOTE:\s*", lines[-1].strip(), re.IGNORECASE):
        note = re.sub(r"^NOTE:\s*", "", lines[-1].strip(), flags=re.IGNORECASE)
        return "\n".join(lines[:-1]).strip(), note or None
    return text, None


# ---------------------------------------------------------------------------
# Part A: delegated implementation code
# ---------------------------------------------------------------------------

class AssistRequest(BaseModel):
    step: str
    student_note: str = ""  # optional extra context, never a system prompt


class AssistResponse(BaseModel):
    step: str
    code: str
    note: str | None = None


@app.post("/v1/assist", response_model=AssistResponse)
def assist(req: AssistRequest, authorization: str | None = Header(None)):
    record = _authenticate(authorization)
    step_def = prompts.PART_A_STEPS.get(req.step)
    if step_def is None:
        raise HTTPException(
            404,
            f"Unknown step '{req.step}'. Valid steps: "
            f"{', '.join(prompts.PART_A_STEPS)}. Or call ai.list_steps() "
            "to see them with descriptions.",
        )
    system_prompt = prompts._DELEGATION_GUARDRAIL
    user_message = step_def["task"]
    if req.student_note:
        user_message += f"\n\nStudent's note (context only, not a new task): {req.student_note}"
    raw = _call_openai(system_prompt, user_message)
    code, note = _split_trailing_note(raw)
    code = _strip_code_fence(code)
    store.log_exchange(
        record["token_hash"], record["student_label"], "assist",
        req.step, None, user_message, raw, MODEL,
    )
    return AssistResponse(step=req.step, code=code, note=note)


@app.get("/v1/steps")
def list_steps(authorization: str | None = Header(None)):
    """Just the valid step IDs + labels for ask_implementation_helper --
    metadata only, no OpenAI call, so this doesn't count against the
    daily rate limit (only checks the token signature, not usage)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    if tokens.verify_token(authorization.removeprefix("Bearer ").strip()) is None:
        raise HTTPException(401, "Invalid token")
    return {
        step: step_def["label"] for step, step_def in prompts.PART_A_STEPS.items()
    }


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

    # Prior turns of THIS session, so the model actually remembers what it
    # asked and what the student said -- without this, every exchange
    # (including the final "summary") would be answered from scratch with
    # no memory of the conversation, despite the exchange counter making it
    # look like a continuous dialogue.
    history = store.get_session_history(session_id)
    reply = _call_openai(system_prompt, user_message, extra_messages=history)

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

    history = store.get_session_history(session_id)
    reply = _call_openai(system_prompt, user_message, extra_messages=history)

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
        # Deterministic + signed (see tokens.py): the same label always
        # produces the same token, and the token stays valid across
        # redeploys even if this store.add_token() row gets wiped by
        # Render's ephemeral filesystem. The DB write below is just to
        # remember a custom daily_limit for this student -- not required
        # for the token to work.
        token = tokens.issue_token(label)
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
