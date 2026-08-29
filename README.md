# Oxford ML course AI companion

A scoped AI companion for the "Coding exercise: Supervised learning in
Python" tutorial. Students keep writing and running their notebook exactly
as before (locally, in Colab, wherever) -- the only thing that changes is
that they call a small Python package for the specific, instructor-authored
AI interactions the exercise calls for (delegated `scikit-learn` code, the
Skeptical Reviewer, the pre-mortem). Nothing else is reachable: there is no
endpoint that accepts an arbitrary system prompt, and your OpenAI key never
leaves the backend.

## How it fits together

```
student's notebook  --pip installed client-->  your backend  --your key-->  OpenAI
(local / Colab)            (oxford_ml_ai)      (FastAPI, holds prompts,
                                                 tokens, rate limits, logs)
```

- `backend/` -- FastAPI app you deploy once. Holds `OPENAI_API_KEY`, the
  fixed prompts (`prompts.py`), per-student tokens, daily rate limits, and
  a log of every exchange.
- `client/` -- the `oxford_ml_ai` package students `pip install`. It has no
  access to the API key and cannot send a custom system prompt -- it only
  exposes `ask_implementation_helper`, `ask_skeptical_reviewer`, and
  `ask_premortem`.

## 1. Deploy the backend

1. Push the `backend/` folder to a GitHub repo (or the whole project --
   just point Render at the `backend` subfolder as the root).
2. On [Render](https://render.com) (free tier is enough for <30 students):
   New -> Blueprint -> point at the repo. `render.yaml` configures the
   service; you'll be prompted to paste your `OPENAI_API_KEY` as a secret.
   (Any similar host -- Railway, Fly.io -- works too; `render.yaml` is
   Render-specific but `main.py` isn't tied to it.)
3. Note the deployed URL, e.g. `https://oxford-ml-ai-companion.onrender.com`.
4. Test it: `curl https://your-url.onrender.com/healthz` should return
   `{"ok": true, ...}`.

Cost control, beyond the app's own daily-per-student limit: set a hard
monthly spending cap on the OpenAI account itself (platform.openai.com ->
Settings -> Limits) as a backstop. With `gpt-4o-mini` and ~30 students at
even 60 requests/day each, you're well under a few dollars for the whole
exercise.

## 2. Issue student tokens

```bash
cd backend
pip install -r requirements.txt
echo -e "student01\nstudent02\nstudent03" > students.txt   # one label per line
python generate_tokens.py --labels students.txt --daily-limit 60
```

This writes `tokens_output.csv` (label, token). Distribute each row
individually and privately (e.g. one-per-student via your LMS's private
feedback/assignment channel) -- not as a shared spreadsheet, since a token
is effectively a password against your OpenAI spend.

Re-run `generate_tokens.py` any time to add students or rotate a
compromised token (it overwrites by label).

> Note on Render's free tier: its filesystem is ephemeral, so tokens and
> logs stored in the default SQLite file are wiped on redeploy/restart. For
> a single-term course this is usually fine (issue tokens once at the start
> of term); if you want persistence across restarts, attach a free Render
> disk and point `AI_COMPANION_DB` at it (commented instructions are in
> `render.yaml`).

## 3. Give students the client package

Simplest distribution without publishing to PyPI: push this whole repo (or
just `client/`) to GitHub and have students run:

```bash
pip install git+https://github.com/YOUR-ORG/oxford-ml-ai-companion.git#subdirectory=client
```

Then, before editing `client/oxford_ml_ai/client.py`, set the real
`_DEFAULT_BASE_URL` to your deployed Render URL so students don't have to
pass `base_url` themselves.

In their notebook:

```python
import oxford_ml_ai as ai
ai.configure(token="the-token-you-were-given")   # cached locally after first run

# Part A -- delegated implementation (won't answer the "why", only the code)
ai.ask_implementation_helper("2_split")
ai.ask_implementation_helper("6_gridsearch")

# Part B, Step 8 -- Skeptical Reviewer (bounded to 5 exchanges)
ai.ask_skeptical_reviewer("My interpretation of the confusion matrix is ...")
ai.ask_skeptical_reviewer("On generalization, I'd say ...")   # continues same session
ai.reset_reviewer_session()                                    # start over if needed

# Part B, Step 9 -- pre-mortem
ai.ask_premortem("I trained a KNN classifier on Iris, tuned k via GridSearchCV, got 97% test accuracy.")

ai.whoami()   # check remaining daily quota
```

Valid `step` values for `ask_implementation_helper` are defined in
`backend/prompts.py` (`2_split`, `3_logreg`, `4_accuracy`, `5_knn_cv`,
`6_gridsearch`, `7_confusion`) -- one per delegable step in the exercise.
Add or edit steps there; no client change needed.

## Why this shape, and not a Streamlit app

A Streamlit app runs server-side, so if it hosted the code execution too
you'd be paying for and managing a sandbox per student, and the deliverable
would split across two windows instead of staying in one notebook (which
the exercise requires: "Your code should run from start to end in one
execution ... integrated in a Jupyter Notebook"). This design keeps
execution where the exercise already puts it and adds only the AI layer.
If you'd still like a browsable web view of the tutorial text alongside the
same scoped personas (e.g. for students who prefer clicking buttons to
importing a package), that's a straightforward addition on top of this
backend -- the personas and rate limiting don't change, only the frontend.

## What this does and doesn't protect against

- Students cannot see your API key or send it arbitrary prompts (only the
  three typed endpoints exist).
- Per-student daily limits and a hard OpenAI spending cap bound your
  worst-case cost.
- Every exchange is logged server-side (`usage_log` table) with the
  student's label, persona, and full text -- useful both for the course's
  own "Robustness & Verification Appendix" requirement and as a light
  academic-integrity signal (a student claiming they never used a
  disallowed path, or a claim you want to spot-check).
- What it does *not* do: stop a student from using ChatGPT directly in a
  separate tab instead of this package, or fully prevent prompt injection
  within the delegation guardrail (a determined student could try to talk
  the "assist" persona into explaining a concept). The guardrail and the
  logging make that harder and visible, not impossible -- which matches
  the exercise's own framing of AI use as something to be checked, not
  blocked outright.
