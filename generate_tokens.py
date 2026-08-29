"""
Instructor-only utility: generate one access token per student.

Usage:
    python generate_tokens.py --labels students.txt --daily-limit 60

`students.txt` should have one student label per line (e.g. a candidate
number or first name -- avoid emails/full names if you want the usage log
itself to be less sensitive). Prints a table of label -> token to stdout
and writes it to tokens_output.csv; distribute each student ONLY their own
row (e.g. via the LMS's per-student feedback channel, not a shared sheet).

Re-running with the same labels issues fresh tokens (old ones stop working
once overwritten in the database).
"""

import argparse
import csv
import secrets

import store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", required=True, help="text file, one student label per line")
    parser.add_argument("--daily-limit", type=int, default=60)
    parser.add_argument("--out", default="tokens_output.csv")
    args = parser.parse_args()

    store.init_db()

    with open(args.labels) as f:
        labels = [line.strip() for line in f if line.strip()]

    rows = []
    for label in labels:
        token = secrets.token_urlsafe(24)
        store.add_token(label, token, daily_limit=args.daily_limit)
        rows.append((label, token))

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["student_label", "token"])
        writer.writerows(rows)

    print(f"Generated {len(rows)} tokens -> {args.out}")
    print("Distribute each row individually. Do not post the whole file publicly.")


if __name__ == "__main__":
    main()
