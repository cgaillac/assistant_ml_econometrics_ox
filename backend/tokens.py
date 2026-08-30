"""
Self-verifying (signed) student tokens.

Why this exists: Render's free tier has an EPHEMERAL filesystem -- the
SQLite file is wiped on every redeploy/restart. The original design
issued random tokens and only the database said whether one was valid, so
every redeploy silently invalidated every token already handed out to
students. That's the "401 after I redeployed" bug.

The fix: a token is now `base64url(student_label) + "." + signature`,
where the signature is an HMAC over the label keyed by ADMIN_TOKEN. A
token is valid if its signature checks out -- no database lookup
required. This means:

  - Tokens survive redeploys/restarts automatically. No re-issuing needed
    after a routine backend update.
  - Issuing a token for the same label twice returns the SAME token
    (it's a deterministic function of label + secret) -- idempotent by
    design.
  - The tradeoff: there is no per-student revocation list, because a
    persistent revocation list has the exact same ephemeral-filesystem
    problem this fix is solving. To revoke ONE compromised token you'd
    have to rotate ADMIN_TOKEN and re-issue for the whole class (since
    ADMIN_TOKEN is also the signing key) -- acceptable for a small,
    trusted class, but worth knowing.

Usage limits (usage_log/tokens table) still live in SQLite and DO reset
on redeploy -- that's a much smaller problem (a quota resets, nobody gets
locked out) so it's left as-is rather than also being made stateless.
"""

import base64
import hashlib
import hmac
import os


def _secret() -> bytes:
    secret = os.environ.get("ADMIN_TOKEN")
    if not secret:
        raise RuntimeError(
            "ADMIN_TOKEN must be set to issue or verify student tokens."
        )
    return secret.encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _signature(label_b64: str) -> str:
    digest = hmac.new(_secret(), label_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)[:22]  # 128 bits -- plenty for this threat model


def issue_token(student_label: str) -> str:
    label_b64 = _b64encode(student_label.encode("utf-8"))
    return f"{label_b64}.{_signature(label_b64)}"


def verify_token(token: str) -> str | None:
    """Returns the student_label if the token's signature is valid, else None."""
    if not token or "." not in token:
        return None
    label_b64, _, sig_b64 = token.partition(".")
    if not hmac.compare_digest(sig_b64, _signature(label_b64)):
        return None
    try:
        return _b64decode(label_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
