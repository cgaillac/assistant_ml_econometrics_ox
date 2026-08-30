"""
Instructor-only utility: generate one access token per student, directly on
your DEPLOYED backend.

Why this calls the live server instead of writing to a local database: your
laptop and the Render service do not share a filesystem. An earlier version
of this script wrote tokens into a SQLite file on your machine, which the
deployed app never reads -- students given those tokens got 401s. This
version POSTs to the /admin/generate-tokens endpoint on your deployed app,
so the tokens land in the exact database the live server checks.

Needs no dependencies beyond the Python standard library -- you do not need
to `pip install -r requirements.txt` just to run this.

1. Create a text file with one student label per line. On any platform,
   the reliable way (do NOT use `echo -e ... > students.txt` in Windows
   cmd.exe -- it does not interpret -e or \\n and will write one garbled
   line instead):

       python -c "open('students.txt','w').write('student01\\nstudent02\\nstudent03\\n')"

   or just create students.txt in a text editor, one label per line.

2. Set ADMIN_TOKEN as an environment variable on your Render service
   (Dashboard -> your service -> Environment -> Add Environment Variable;
   pick any long random string, e.g. from `python -c "import secrets;
   print(secrets.token_urlsafe(24))"`) and redeploy.

3. Run:

       python generate_tokens.py --base-url https://YOUR-APP.onrender.com --admin-token YOUR_ADMIN_TOKEN --labels students.txt --daily-limit 60

   (or set ADMIN_TOKEN as an env var locally instead of passing --admin-token)

Writes tokens_output.csv (student_label, token). Distribute each row
individually and privately (e.g. one-per-student via your LMS's private
feedback/assignment channel) -- not as a shared spreadsheet, since a token
is effectively a password against your OpenAI spend.
"""

import argparse
import csv
import json
import os
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url", required=True,
        help="your deployed backend URL, e.g. https://your-app.onrender.com",
    )
    parser.add_argument(
        "--admin-token", default=os.environ.get("ADMIN_TOKEN"),
        help="the ADMIN_TOKEN you set on the Render service (or set the "
        "ADMIN_TOKEN environment variable instead of passing this)",
    )
    parser.add_argument("--labels", required=True, help="text file, one student label per line")
    parser.add_argument("--daily-limit", type=int, default=60)
    parser.add_argument("--out", default="tokens_output.csv")
    args = parser.parse_args()

    if not args.admin_token:
        raise SystemExit(
            "No admin token given. Pass --admin-token or set the "
            "ADMIN_TOKEN environment variable to match what's configured "
            "on the deployed service."
        )

    with open(args.labels, encoding="utf-8") as f:
        labels = [line.strip() for line in f if line.strip()]
    if not labels:
        raise SystemExit(
            f"No labels found in {args.labels} -- check the file has one "
            "label per line."
        )

    payload = json.dumps({"labels": labels, "daily_limit": args.daily_limit}).encode("utf-8")
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/admin/generate-tokens",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Token": args.admin_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"Server rejected the request ({e.code}): {e.read().decode(errors='replace')}\n"
            "If this is 401, double check ADMIN_TOKEN matches what's set "
            "on the Render service. If this is 'Not Found', double check "
            "--base-url and that the deployed backend includes the "
            "/admin/generate-tokens endpoint (redeploy if it's an older build)."
        )
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach {args.base_url}: {e.reason}")

    rows = [(t["student_label"], t["token"]) for t in data["tokens"]]

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["student_label", "token"])
        writer.writerows(rows)

    print(f"Generated {len(rows)} tokens on {args.base_url} -> {args.out}")
    print("Distribute each row individually. Do not post the whole file publicly.")


if __name__ == "__main__":
    main()
