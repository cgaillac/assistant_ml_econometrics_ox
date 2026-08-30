"""
Instructor-only utility: pull the usage log back off your deployed backend.

Without this, the log the server keeps for every exchange (for the course's
Robustness & Verification Appendix requirement, and for spot-checking a
claim) is write-only -- Render's free tier gives you no shell access to
read the SQLite file directly. This hits the /admin/usage-log endpoint
instead, the same way generate_tokens.py issues tokens.

Needs no dependencies beyond the Python standard library.

Usage:
    python export_log.py --base-url https://your-app.onrender.com --admin-token YOUR_ADMIN_TOKEN
    python export_log.py --base-url https://your-app.onrender.com --admin-token YOUR_ADMIN_TOKEN --student student01 --limit 200

Writes usage_log_export.csv (student_label, persona, step, session_id,
request_text, response_text, model, created_at).
"""

import argparse
import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--admin-token", default=os.environ.get("ADMIN_TOKEN"))
    parser.add_argument("--student", default=None, help="filter to one student_label")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--out", default="usage_log_export.csv")
    args = parser.parse_args()

    if not args.admin_token:
        raise SystemExit(
            "No admin token given. Pass --admin-token or set the "
            "ADMIN_TOKEN environment variable."
        )

    params = {"limit": args.limit}
    if args.student:
        params["student_label"] = args.student
    url = args.base_url.rstrip("/") + "/admin/usage-log?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(url, headers={"X-Admin-Token": args.admin_token})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Server rejected the request ({e.code}): {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach {args.base_url}: {e.reason}")

    rows = data["rows"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "student_label", "persona", "step", "session_id",
            "request_text", "response_text", "model", "created_at_utc",
        ])
        for r in rows:
            ts = datetime.fromtimestamp(r["created_at"], tz=timezone.utc).isoformat()
            writer.writerow([
                r["student_label"], r["persona"], r["step"] or "",
                r["session_id"] or "", r["request_text"], r["response_text"],
                r["model"], ts,
            ])

    print(f"Exported {len(rows)} log rows -> {args.out}")


if __name__ == "__main__":
    main()
