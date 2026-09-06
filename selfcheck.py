#!/usr/bin/env python3
"""Run the real analyzer against the bundled repository before it is trusted with yours.

The assertions that prove this analyzer works live on the author's machine, where nobody
running the play can see them. This feeds the shipped rework.py its own bundled history
at run time, and the presentation withholds its verdict if any case fails.

Two things a naive self-check misses, both because a green check above a broken analyzer
is worse than no check at all:

  every verdict needs a POSITIVE case, so deleting the rule that produces it fails here
  rather than passing quietly.

  DISCOVERY is checked, not only classification. A history read that returns nothing
  reports a repository where everything survived, and no classification case would notice.

One case exists for a specific bug rather than a rule. `git blame --reverse` names the TIP
commit for a line that is still alive, meaning it last existed at HEAD. Reading that as a
death made every surviving commit look rewritten whenever the tip fell inside the window,
and it was wrong on every repository until an independent check caught it. `still-standing`
below is that bug's permanent guard.
"""
import json
import os
import os, os, subprocess, sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REWORK = os.path.join(HERE, "rework.py")

# subject fragment -> the verdict the bundled history must produce
EXPECTED = {
    "add checkout handling": "REWRITTEN_WITHIN_WINDOW",
    "add legacy helpers": "SUPERSEDED_LATER",
    "add stable helpers": "STILL_STANDING",
}
MUST_COVER = {"REWRITTEN_WITHIN_WINDOW", "SUPERSEDED_LATER", "STILL_STANDING"}


def run_json(args):
    # spawned as a literal so the command can be checked against deps.toml
    p = subprocess.run(["python3"] + args, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "").strip()[:200] or "exit %d" % p.returncode)
    return json.loads(p.stdout)


def main():
    failures, total = [], 0
    seen = set()
    d = None
    try:
        s = run_json([REWORK, "survey", "demo", "200", "30"])
        d = run_json([REWORK, "measure", "demo", json.dumps(s)])
    except Exception as e:
        total += len(EXPECTED)
        failures.append({"case": "analyzer-runs", "detail": str(e)})

    rows = {}
    if d is not None:
        for r in d.get("measured", []) + d.get("standing_sample", []):
            rows[r["subject"]] = r

    for fragment, verdict in sorted(EXPECTED.items()):
        total += 1
        seen.add(verdict)
        row = None
        for subject, r in rows.items():
            if fragment in subject:
                row = r
                break
        if row is None:
            failures.append({"case": fragment,
                             "detail": "expected %s, no such commit was measured" % verdict})
        elif row["verdict"] != verdict:
            failures.append({"case": fragment,
                             "detail": "expected %s, produced %s"
                                       % (verdict, row["verdict"])})

    # the tip-is-alive guard: every line of an untouched commit must read as alive
    total += 1
    row = next((r for s, r in rows.items() if "add stable helpers" in s), None)
    if row is None or row.get("still_alive") != row.get("added"):
        failures.append({
            "case": "tip-is-alive",
            "detail": "a commit nothing has touched must have every line alive, got "
                      "%s alive of %s added. blame --reverse names the tip commit for a "
                      "living line, and reading that as a death makes every surviving "
                      "commit look rewritten"
                      % ((row or {}).get("still_alive"), (row or {}).get("added"))})

    # a commit younger than the window can never be judged
    total += 1
    if d is not None and d.get("too_recent", 0) < 1:
        failures.append({"case": "too-recent-is-not-survived",
                         "detail": "the bundled history holds a three day old commit that "
                                   "must be held back as too recent to judge, and none was"})

    # the bot flag needs a positive case or the rule could be deleted unnoticed
    total += 1
    if not any(r.get("bot") for r in rows.values()):
        failures.append({"case": "bot-author-recognised",
                         "detail": "the bundled history holds a dependabot commit and no "
                                   "row was flagged as bot authored"})

    for verdict in sorted(MUST_COVER - seen):
        total += 1
        failures.append({"case": "coverage:%s" % verdict,
                         "detail": "no bundled case asserts this verdict, so removing the "
                                   "rule that produces it would not be noticed"})

    # ---- git failing is not a fact about the repository
    sys.path.insert(0, HERE)
    try:
        import rework
    except Exception as e:
        total += 1
        failures.append({"case": "analyzer-imports", "detail": str(e)})
        rework = None

    if rework is not None:
        class Fake(object):
            def __init__(self, rc, err):
                self.returncode, self.stdout, self.stderr = rc, "", err

        real = rework.subprocess.run
        try:
            cases = [
                ("git answered: not a repository", "no",
                 lambda *a, **k: Fake(128, "fatal: not a git repository")),
                ("git exited without explaining", "down", lambda *a, **k: Fake(1, "")),
                ("git is not installed", "down",
                 lambda *a, **k: (_ for _ in ()).throw(OSError("no git"))),
                ("git timed out", "down",
                 lambda *a, **k: (_ for _ in ()).throw(
                     rework.subprocess.TimeoutExpired("git", 60))),
            ]
            for label, want, fn in cases:
                rework.subprocess.run = fn
                total += 1
                how = rework.git_status(["rev-parse", "--git-dir"], "/anywhere")[1]
                if how != want:
                    failures.append({"case": "git:%s" % want,
                                     "detail": "%s was read as %r, expected %r"
                                               % (label, how, want)})

            # and the status the reader is shown must match
            rework.subprocess.run = lambda *a, **k: Fake(1, "")
            total += 1
            st = rework.survey("/anywhere", 5, 30).get("status")
            if st != "git-unavailable":
                failures.append({
                    "case": "git:failure-is-not-a-claim-about-the-repository",
                    "detail": "with git failing the survey reported %r. Telling someone "
                              "their path is not a git repository when git would not "
                              "start sends them to check the wrong thing" % st})
        finally:
            rework.subprocess.run = real

    # discovery: a history read that returns nothing looks like a repository where
    # everything survived, and every case above would still pass on an empty set
    total += 1
    if d is None or d.get("commits_read", 0) < 7:
        failures.append({"case": "discovery:reads-the-history",
                         "detail": "expected at least 7 commits in the bundled repository, "
                                   "the survey read %s"
                                   % (d or {}).get("commits_read")})


    # ---- git escapes non-ASCII paths before printing them, so a wrapper without
    # core.quotePath=false reads back a filename that does not exist. On a repository
    # with an accented filename this made blast-radius report no changes at all.
    total += 1
    try:
        _src = open(os.path.join(HERE, "rework.py"), encoding="utf-8").read()
        if "core.quotePath=false" not in _src:
            failures.append({
                "case": "paths:non-ascii-are-not-escaped",
                "detail": "the git wrapper does not pass core.quotePath=false, so a path "
                          "with a non-ASCII character comes back as an escaped string "
                          "and every file named that way is silently missed"})
    except OSError as _e:
        failures.append({"case": "paths:non-ascii-are-not-escaped",
                         "detail": "could not read the analyzer: %s" % _e})


    # ---- the play must run with no arguments at all
    #
    # A reviewer pulled all nine and found three that did not: two declared required
    # parameters and refused, and one defaulted to the reader's real history instead of
    # the bundled example. No self-check looked at the frontmatter, so nothing caught it.
    total += 1
    try:
        _mt = open(os.path.join(HERE, "..", "main.ts"), encoding="utf-8").read()
        _params = _mt.split("* parameters:")[1].split("* metadata:")[0] if "* parameters:" in _mt else ""
        _required = [ln for ln in _params.split(chr(10)) if "required: true" in ln]
        if _required:
            failures.append({
                "case": "runs-bare:no-required-parameters",
                "detail": "%d parameter(s) are declared required, so `rote play run "
                          "<this>` refuses instead of showing the bundled example"
                          % len(_required)})
    except Exception as _e:
        failures.append({"case": "runs-bare:no-required-parameters",
                         "detail": "could not read the frontmatter: %s" % _e})

    print(json.dumps({"passed": total - len(failures), "total": total,
                      "failures": failures[:10]}, separators=(",", ":")))


if __name__ == "__main__":
    main()
