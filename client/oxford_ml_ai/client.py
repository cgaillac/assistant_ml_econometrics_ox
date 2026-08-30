"""
Student-facing client for the course AI companion.

Usage in a notebook:

    import oxford_ml_ai as ai
    ai.configure(token="paste-your-token-here")   # or set OXFORD_AI_TOKEN env var

    ai.ask_implementation_helper("2_split")
    ai.ask_skeptical_reviewer("My interpretation is that ...")
    ai.ask_premortem("I trained a KNN classifier on Iris and got 97% accuracy ...")

There is deliberately no function that accepts a free-form system prompt or
lets you pick the model -- every call goes to one of the fixed personas the
instructor configured server-side.
"""

import json
import os
from pathlib import Path

import requests

_CONFIG_PATH = Path.home() / ".oxford_ml_ai" / "config.json"
_DEFAULT_BASE_URL = os.environ.get(
    "OXFORD_AI_BASE_URL", "https://oxford-ml-ai-companion.onrender.com/"
)

_state = {
    "token": os.environ.get("OXFORD_AI_TOKEN"),
    "base_url": _DEFAULT_BASE_URL,
    "reviewer_session_id": None,
    "hint_session_id": None,
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


def _display(markdown_text: str):
    try:
        from IPython.display import Markdown, display

        display(Markdown(markdown_text))
    except ImportError:
        print(markdown_text)


# ---------------------------------------------------------------------------
# Part A
# ---------------------------------------------------------------------------

def ask_implementation_helper(step: str, note: str = "") -> str:
    """Get the routine scikit-learn code for one specific, pre-approved
    step of the exercise (e.g. "2_split", "3_logreg", "4_accuracy",
    "5_knn_cv", "6_gridsearch", "7_confusion"). Will NOT answer the
    conceptual/discussion question attached to that step -- that part is
    yours to do.
    """
    result = _post("/v1/assist", {"step": step, "student_note": note})
    _display(f"**AI-generated code for `{step}`:**\n\n```python\n{result['code']}\n```")
    return result["code"]


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
    header = f"**Hint ({result['exchange_number']}/3):**"
    if result["is_final"]:
        header += " _last hint for this question_"
    _display(f"{header}\n\n{result['reply']}")
    return result["reply"]


def reset_hint_session():
    _state["hint_session_id"] = None


# ---------------------------------------------------------------------------
# Part B, Step 8
# ---------------------------------------------------------------------------

def ask_skeptical_reviewer(message: str) -> str:
    """Send one message to the Skeptical Reviewer. The conversation is
    capped at 5 exchanges (server-enforced); the final exchange returns a
    structured summary instead of another question. Call
    reset_reviewer_session() to start a fresh conversation.
    """
    result = _post(
        "/v1/skeptical-reviewer",
        {"message": message, "session_id": _state["reviewer_session_id"]},
    )
    _state["reviewer_session_id"] = result["session_id"]
    header = f"**Skeptical Reviewer (exchange {result['exchange_number']}/5):**"
    if result["is_final"]:
        header += " _final -- summary below_"
    _display(f"{header}\n\n{result['reply']}")
    return result["reply"]


def reset_reviewer_session():
    _state["reviewer_session_id"] = None


# ---------------------------------------------------------------------------
# Part B, Step 9
# ---------------------------------------------------------------------------

def ask_premortem(description: str) -> str:
    """One-shot pre-mortem on your analysis. Describe what you did in a
    couple of sentences; get back 2-3 concrete, specific failure modes.
    """
    result = _post("/v1/premortem", {"description": description})
    _display(f"**Pre-mortem:**\n\n{result['reply']}")
    return result["reply"]


def whoami() -> dict:
    """Check your usage against your daily limit."""
    url = _state["base_url"].rstrip("/") + "/v1/whoami"
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()
