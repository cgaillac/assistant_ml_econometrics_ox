"""
Student-facing client for the course AI companion.

Usage in a notebook:

    import oxford_ml_ai as ai
    ai.configure(token="paste-your-token-here")   # or set OXFORD_AI_TOKEN env var

    ai.list_steps()                       # see valid step IDs for the line below
    ai.ask_implementation_helper("2_split")   # a code SKELETON, not a finished solution
    ai.ask_skeptical_reviewer("My interpretation is that ...")
    ai.ask_ai_student("I trained a KNN classifier and tuned k via GridSearchCV ...")

There is deliberately no function that accepts a free-form system prompt or
lets you pick the model -- every call goes to one of the fixed personas the
instructor configured server-side.

ask_implementation_helper() deliberately does NOT hand back finished,
ready-to-run code: it fills in the scikit-learn structure (imports, the
right function/class, variable names) but leaves every substantive choice
-- a hyperparameter, a split fraction, a parameter grid -- as a `___`
blank with a comment naming the decision. Running the skeleton as-is will
raise a NameError at the first blank; that's expected, and is your cue to
fill it in yourself rather than paste-and-run.

Every ask_*() call returns an object that behaves like a plain string (or,
for list_steps(), a plain dict) -- `code = ai.ask_implementation_helper(...)`
works exactly as you'd expect -- but ALSO renders as nicely formatted
Markdown/HTML when it's the last line of a notebook cell, instead of
Jupyter's default "quoted string" auto-echo. That's what avoids the
double-printed output some earlier versions had.
"""

import html as _html
import json
import os
from pathlib import Path

import requests

_CONFIG_PATH = Path.home() / ".oxford_ml_ai" / "config.json"
_DEFAULT_BASE_URL = os.environ.get(
    "OXFORD_AI_BASE_URL", "https://oxford-ml-ai-companion.onrender.com"
)

_state = {
    "token": os.environ.get("OXFORD_AI_TOKEN"),
    "base_url": _DEFAULT_BASE_URL,
    # Keyed by `topic`, since a deployment can serve more than one exercise
    # (e.g. "classification" and "shrinkage") and each keeps its own
    # Skeptical Reviewer conversation, exchange count, and persona --
    # mixing them would carry the wrong conversation history into a fresh
    # topic's exchange-1 question. See ask_skeptical_reviewer().
    "reviewer_session_ids": {},
    "hint_session_id": None,
    "student_session_id": None,
}


def configure(token: str | None = None, base_url: str | None = None, persist: bool = True):
    """Set your access token (and optionally the server URL).

    With persist=True (default), the token is cached to
    ~/.oxford_ml_ai/config.json so you don't have to re-enter it in every
    notebook session.
    """
    if token:
        _state["token"] = token
    if base_url:
        _state["base_url"] = base_url
    if persist and _state["token"]:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(
            json.dumps({"token": _state["token"], "base_url": _state["base_url"]})
        )


def _load_persisted_config():
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text())
            _state.setdefault("token", None)
            if not _state["token"]:
                _state["token"] = data.get("token")
            if data.get("base_url"):
                _state["base_url"] = data["base_url"]
        except (json.JSONDecodeError, OSError):
            pass


_load_persisted_config()


def _headers():
    if not _state["token"]:
        raise RuntimeError(
            "No access token set. Call ai.configure(token='...') with the "
            "token your instructor gave you, or set the OXFORD_AI_TOKEN "
            "environment variable."
        )
    return {"Authorization": f"Bearer {_state['token']}"}


def _post(path: str, payload: dict) -> dict:
    url = _state["base_url"].rstrip("/") + path
    resp = requests.post(url, json=payload, headers=_headers(), timeout=60)
    if resp.status_code == 401:
        raise RuntimeError("Access token rejected. Check ai.configure(token=...).")
    if resp.status_code == 429:
        raise RuntimeError(resp.json().get("detail", "Rate limit reached."))
    resp.raise_for_status()
    return resp.json()


def _get(path: str) -> dict:
    url = _state["base_url"].rstrip("/") + path
    resp = requests.get(url, headers=_headers(), timeout=30)
    if resp.status_code == 401:
        raise RuntimeError("Access token rejected. Check ai.configure(token=...).")
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Rich-but-plain return values: behave as a normal str/dict, but carry a
# nicer notebook rendering so a bare `ai.ask_x(...)` cell shows one clean
# block instead of our own display PLUS Jupyter's separate raw-repr echo.
# ---------------------------------------------------------------------------

class _RichText(str):
    def __new__(cls, value: str, markdown: str, html_body: str):
        obj = super().__new__(cls, value)
        obj._markdown = markdown
        obj._html = html_body
        return obj

    def _repr_markdown_(self):
        return self._markdown

    def _repr_html_(self):
        return self._html


class _RichDict(dict):
    def __new__(cls, value: dict, markdown: str, html_body: str):
        obj = super().__new__(cls, value)
        return obj

    def __init__(self, value: dict, markdown: str, html_body: str):
        super().__init__(value)
        self._markdown = markdown
        self._html = html_body

    def _repr_markdown_(self):
        return self._markdown

    def _repr_html_(self):
        return self._html


def _code_block(header: str, code: str, note: str | None = None) -> _RichText:
    md = f"**{header}**\n\n```python\n{code}\n```"
    htm = f"<p><strong>{_html.escape(header)}</strong></p><pre><code>{_html.escape(code)}</code></pre>"
    if note:
        md += f"\n\n*Note: {note}*"
        htm += f"<p><em>Note: {_html.escape(note)}</em></p>"
    return _RichText(code, md, htm)


def _text_block(header: str, body: str) -> _RichText:
    md = f"**{header}**\n\n{body}"
    htm = f"<p><strong>{_html.escape(header)}</strong></p><p>{_html.escape(body).replace(chr(10), '<br>')}</p>"
    return _RichText(body, md, htm)


# ---------------------------------------------------------------------------
# Part A
# ---------------------------------------------------------------------------

def list_steps() -> dict:
    """Valid step IDs for ask_implementation_helper, with a short label for
    each. Metadata only -- doesn't call the AI, doesn't count against your
    daily quota.
    """
    steps = _get("/v1/steps")
    md_lines = "\n".join(f"- `{k}` -- {v}" for k, v in steps.items())
    md = f"**Valid steps for `ai.ask_implementation_helper(...)`:**\n\n{md_lines}"
    html_items = "".join(
        f"<li><code>{_html.escape(k)}</code> -- {_html.escape(v)}</li>"
        for k, v in steps.items()
    )
    htm = (
        "<p><strong>Valid steps for ai.ask_implementation_helper(...)</strong></p>"
        f"<ul>{html_items}</ul>"
    )
    return _RichDict(steps, md, htm)


def ask_implementation_helper(step: str, note: str = "") -> str:
    """Get a scikit-learn CODE SKELETON for one specific, pre-approved step
    of the exercise -- not a finished, ready-to-run solution. Structure and
    function/variable names are filled in, but every substantive choice
    (a hyperparameter, a split fraction, a parameter grid, ...) is left as
    a `___` blank with a comment naming what you need to decide. Running
    it unedited will raise a NameError at the first blank -- that's
    expected, fill the blanks in yourself. Call list_steps() to see valid
    step IDs. Will NOT answer the conceptual/discussion question attached
    to that step -- that part is yours to do (use ask_conceptual_hint for
    a nudge on it).
    """
    result = _post("/v1/assist", {"step": step, "student_note": note})
    return _code_block(f"Code skeleton for `{step}` -- fill in the blanks:", result["code"], result.get("note"))


# ---------------------------------------------------------------------------
# Conceptual hints -- for the "why" questions Part A leaves to you.
# ask_implementation_helper refuses these on purpose; this is the
# sanctioned AI path for them instead. It nudges, it does not answer.
# ---------------------------------------------------------------------------

def ask_conceptual_hint(question: str, your_thinking: str = "") -> str:
    """Get a Socratic hint (never the final answer) on one of the
    conceptual "why" questions in Part A. Capped at 3 exchanges per
    question (server-enforced) -- call reset_hint_session() to start a
    fresh question. Sharing your own attempt in `your_thinking` gets you a
    much more specific hint than leaving it blank.
    """
    message = question
    if your_thinking:
        message += f"\n\nWhat I'm thinking so far: {your_thinking}"
    result = _post(
        "/v1/conceptual-hint",
        {"message": message, "session_id": _state["hint_session_id"]},
    )
    _state["hint_session_id"] = result["session_id"]
    header = f"Hint ({result['exchange_number']}/3)"
    if result["is_final"]:
        header += " -- last hint for this question"
    return _text_block(header, result["reply"])


def reset_hint_session():
    _state["hint_session_id"] = None


# ---------------------------------------------------------------------------
# Part B, Step 8
# ---------------------------------------------------------------------------

def ask_skeptical_reviewer(message: str, topic: str = "classification") -> str:
    """Send one message to the Skeptical Reviewer. The conversation is
    capped at 5 exchanges (server-enforced); the final exchange returns a
    structured summary instead of another question. Call
    reset_reviewer_session() to start a fresh conversation.

    `topic` selects which of the instructor's fixed Skeptical Reviewer
    prompts to use, if this course deployment serves more than one
    exercise -- e.g. "classification" (the fishing-mode/Iris exercise's
    default) or "shrinkage" (the shrinkage-estimation exercise). Each
    topic keeps its own conversation and exchange count; you don't need
    to pass this unless your exercise's instructions tell you to.
    """
    session_id = _state["reviewer_session_ids"].get(topic)
    result = _post(
        "/v1/skeptical-reviewer",
        {"message": message, "session_id": session_id, "topic": topic},
    )
    _state["reviewer_session_ids"][topic] = result["session_id"]
    header = f"Skeptical Reviewer (exchange {result['exchange_number']}/5)"
    if result["is_final"]:
        header += " -- final, summary below"
    return _text_block(header, result["reply"])


def reset_reviewer_session(topic: str = "classification"):
    _state["reviewer_session_ids"].pop(topic, None)


# ---------------------------------------------------------------------------
# Part B, Step 9 -- AI-student (learning by teaching)
# ---------------------------------------------------------------------------

def ask_ai_student(message: str) -> str:
    """Explain your analysis, in your own words, to an AI playing a
    not-especially-strong, earnest student. It will ask ONE naive,
    genuine clarifying question per turn about your explanation -- not an
    expert critique (that's ask_skeptical_reviewer), just an honest "wait,
    I don't follow that bit" from a novice. Explaining it well enough for
    a beginner to follow is the point (the "learning by teaching" effect):
    the questions that trip it up are usually exactly the parts of your
    own understanding that are still shaky. Bounded at 3 exchanges
    (server-enforced); the final exchange returns a short reflection
    instead of another question. Call reset_ai_student_session() to start
    over with a different explanation.
    """
    result = _post(
        "/v1/ai-student",
        {"message": message, "session_id": _state["student_session_id"]},
    )
    _state["student_session_id"] = result["session_id"]
    header = f"AI-student (exchange {result['exchange_number']}/3)"
    if result["is_final"]:
        header += " -- final reflection below"
    return _text_block(header, result["reply"])


def reset_ai_student_session():
    _state["student_session_id"] = None


def whoami() -> dict:
    """Check your usage against your daily limit."""
    return _get("/v1/whoami")
