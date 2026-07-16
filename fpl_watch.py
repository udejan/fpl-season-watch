#!/usr/bin/env python3
"""
FPL New Season Watcher (GitHub Actions edition)
=================================================
Runs on a GitHub Actions schedule. Checks the Fantasy Premier League public
API for a season rollover, persists its state as state.json inside the repo
(the workflow commits it back after each run), and emails the owner via
SMTP the first time a new season is detected.

Detection logic: Gameweek 1 (event id == 1) keeps a fixed deadline_time
during a season and for the off-season right after it ends. When FPL rolls
the game over to the next season, the whole `events` array is replaced and
GW1 gets a brand new deadline_time (the new season's opening weekend). So:
GW1's deadline_time changing from the stored baseline == new season opened.

Required environment variables (set as GitHub repo secrets), only needed
when actually sending the notification email:
  SMTP_USER  - the Gmail address to send from (also used as SMTP login)
  SMTP_PASS  - a Gmail "app password" (NOT the normal account password)
  TO_EMAIL   - address to notify (can be the same as SMTP_USER)
"""
import json
import os
import smtplib
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText

API_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def fetch_bootstrap():
    req = urllib.request.Request(API_URL, headers={"User-Agent": "Mozilla/5.0 (fpl-season-watch)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def send_email(subject, body):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_email = os.environ.get("TO_EMAIL", smtp_user)

    if not smtp_user or not smtp_pass:
        print("SMTP_USER / SMTP_PASS not set - skipping email, but season change WAS detected.")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_email], msg.as_string())
    print(f"Notification email sent to {to_email}.")


def main():
    now = datetime.now(timezone.utc).isoformat()

    try:
        data = fetch_bootstrap()
    except Exception as e:
        print(f"ERROR fetching FPL API: {e}")
        # Still touch state.json's last_checked-with-error so the repo stays
        # active, but don't treat this as a season change.
        state = load_state()
        state["last_checked"] = now
        state["last_error"] = str(e)
        save_state(state)
        sys.exit(1)

    events = data.get("events", [])
    gw1 = next((e for e in events if e.get("id") == 1), None)
    teams = sorted(t["name"] for t in data.get("teams", []))

    if gw1 is None:
        print("Could not find Gameweek 1 in API response.")
        sys.exit(1)

    current = {
        "gw1_deadline_time": gw1.get("deadline_time"),
        "gw1_finished": gw1.get("finished"),
        "teams": teams,
    }

    state = load_state()
    baseline = state.get("baseline")

    if baseline is None:
        state["baseline"] = current
        state["notified_for_deadline"] = None
        state["last_checked"] = now
        state.pop("last_error", None)
        save_state(state)
        print(f"Baseline established. GW1 deadline: {current['gw1_deadline_time']} (finished={current['gw1_finished']}).")
        return

    season_changed = current["gw1_deadline_time"] != baseline["gw1_deadline_time"]

    if not season_changed:
        state["last_checked"] = now
        state.pop("last_error", None)
        save_state(state)
        print("No new season yet. GW1 deadline unchanged from baseline.")
        return

    already_notified = state.get("notified_for_deadline") == current["gw1_deadline_time"]

    if already_notified:
        state["last_checked"] = now
        save_state(state)
        print("New season already detected and previously notified.")
        return

    # New season detected for the first time.
    new_teams = sorted(set(current["teams"]) - set(baseline.get("teams", [])))
    dropped_teams = sorted(set(baseline.get("teams", [])) - set(current["teams"]))

    body_lines = [
        "Fantasy Premier League je otvorio novu sezonu!",
        "",
        f"Stari GW1 deadline: {baseline['gw1_deadline_time']}",
        f"Novi GW1 deadline: {current['gw1_deadline_time']}",
        "",
    ]
    if new_teams:
        body_lines.append(f"Novi timovi (promovisani): {', '.join(new_teams)}")
    if dropped_teams:
        body_lines.append(f"Ispali timovi: {', '.join(dropped_teams)}")
    body_lines.append("")
    body_lines.append("Idi na https://fantasy.premierleague.com/ da napravis tim.")

    body = "\n".join(body_lines)
    send_email("FPL: nova sezona je otvorena!", body)

    state["baseline"] = current
    state["notified_for_deadline"] = current["gw1_deadline_time"]
    state["last_checked"] = now
    save_state(state)
    print("NEW SEASON DETECTED and email sent.")


if __name__ == "__main__":
    main()
