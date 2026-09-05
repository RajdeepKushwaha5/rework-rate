#!/usr/bin/env python3
"""rework-rate: your change passed review. Did it survive the month?

    rework.py survey  <root> [commits] [window_days]
    rework.py measure <root> <survey-json>

An agent scores above 80 percent on isolated tasks and at most 38 percent across a
continuous commit history (EvoClaw, reported in Cao 2026). That gap is work that looked
finished and was not. This measures it on one repository, from git alone.

For each commit, `git show -U0` gives the exact line ranges it added. `git blame
--reverse` gives, for each of those lines, the last commit in which it still existed. A
line whose last commit is the tip is alive; otherwise git names the commit that outlived
it, and its date says whether the line died inside the window or long after.

Three things this had to learn the hard way, all measured:

  Rename detection dominated the history read: `git log --numstat` took 53 seconds on one
  repository and 150 on another, against 82 and 614 milliseconds with `--no-renames`. It
  is also the correct reading, since a renamed file's lines are new lines at a new path.

  Survival at HEAD is the wrong question on its own. Ranking by it put dependency bumps
  on top, and a pinned version line is meant to be replaced by the next bump.

  Blaming a tree at an old revision costs 348 seconds on a large file where the same file
  at HEAD costs 10 milliseconds, because blame away from HEAD loses the commit-graph
  shortcuts. `--reverse` answers the same question in 16 milliseconds.

Read-only. Never checks anything out, never writes to your repository, no network,
no credentials.
"""
import collections, json, os, re, subprocess, sys, time

sys.dont_write_bytecode = True

MIN_ADDED = 5
REWRITTEN = 0.50     # at least half the added lines died inside the window
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(args, root, timeout=60):
    try:
        p = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True,
                           timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return p.stdout if p.returncode == 0 else None


def added_ranges(root, commit, path):
    """The line ranges this commit added, in its own version of the file."""
    out = git(["show", "-U0", "--no-renames", "--format=", commit, "--", path], root, 30)
    if out is None:
        return None
    spans = []
    for line in out.splitlines():
        m = HUNK.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            if count:
                spans.append((start, start + count - 1))
    return spans


def death_map(root, commit, path, tip):
    """For each line of the file as it stood at `commit`, the last commit it survived to.

    --reverse walks forward from the commit rather than backward from HEAD, which is why
    it costs milliseconds where blaming the old tree costs minutes.

    The trap, and it produced a wrong answer on every repository until an independent
    check caught it: --reverse reports the TIP commit for a line that is still alive,
    meaning "it last existed at HEAD". Reading that as a death made surviving lines look
    rewritten whenever the tip happened to fall inside the window. A reported sha equal
    to the tip is life, not death.
    """
    out = git(["blame", "--reverse", "--incremental", commit + "..HEAD", "--", path],
              root, 45)
    if out is None:
        return None
    deaths = []
    cur = None
    for line in out.splitlines():
        parts = line.split(" ")
        if len(parts) == 4 and len(parts[0]) == 40 and parts[3].isdigit():
            cur = {"sha": parts[0], "orig": int(parts[1]), "count": int(parts[3]),
                   "at": None, "alive": parts[0] == tip}
            deaths.append(cur)
        elif cur is not None and line.startswith("author-time "):
            cur["at"] = int(line.split(" ", 1)[1])
    return deaths


def read_history(root, commits):
    raw = git(["log", "--no-merges", "--no-renames", "-n", str(commits),
               "--numstat", "--format=@@%H|%at|%an|%s"], root, 240)
    if raw is None:
        return None
    added, meta, touched = collections.Counter(), {}, {}
    cur = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            parts = line[2:].split("|", 3)
            if len(parts) != 4:
                continue
            cur = parts[0]
            meta[cur] = {"at": int(parts[1]), "author": parts[2], "subject": parts[3]}
            touched[cur] = []
            continue
        p = line.split("\t")
        if cur and len(p) == 3 and p[0].isdigit():
            added[cur] += int(p[0])
            if p[2] and "=>" not in p[2]:
                touched[cur].append(p[2])
    return added, meta, touched


def is_bot(author):
    a = (author or "").lower()
    return "[bot]" in a or "dependabot" in a or "renovate" in a or a.endswith("-bot")


def survey(root, commits, window):
    if os.path.exists(os.path.join(root, ".git", "shallow")):
        return {"status": "shallow-clone",
                "detail": "a shallow clone carries no history to measure survival against"}
    if git(["rev-parse", "--git-dir"], root) is None:
        return {"status": "not-a-git-repo", "detail": root}
    hist = read_history(root, commits)
    if hist is None:
        return {"status": "history-unreadable",
                "detail": "git log did not complete in the time allowed"}
    added, meta, touched = hist
    if not added:
        return {"status": "no-commits-read",
                "detail": "no non-merge commit in range added a line"}

    now = time.time()
    rows, too_recent = [], 0
    for h, n in added.items():
        if n < MIN_ADDED:
            continue
        if (now - meta[h]["at"]) / 86400.0 < window:
            # not yet old enough to have been rewritten. Calling it survived would be
            # the false clean this refuses to produce.
            too_recent += 1
            continue
        rows.append({"commit": h, "added": n, "at": meta[h]["at"],
                     "author": meta[h]["author"], "bot": is_bot(meta[h]["author"]),
                     "subject": meta[h]["subject"][:100],
                     "paths": touched[h][:8]})
    rows.sort(key=lambda r: r["at"], reverse=True)
    tip = (git(["rev-parse", "HEAD"], root) or "").strip()
    # rote unpacks a play into a fresh temp directory, so the resolved demo path is a
    # run-specific string nobody can act on. Report what was asked for.
    label = "demo (bundled repository)" if root.endswith(os.path.join("demo", "repo")) else root
    return {"status": "ok", "root": label, "root_path": root,
            "window_days": window, "tip": tip,
            "commits_read": len(added), "eligible": len(rows),
            "too_recent": too_recent, "rows": rows[:300],
            "rows_capped": max(0, len(rows) - 300)}


def measure(root, payload, budget_s=150):
    window = payload["window_days"]
    deadline = time.time() + budget_s
    out, counts = [], collections.Counter()
    unmeasured = 0
    for r in payload.get("rows", []):
        if time.time() > deadline:
            unmeasured += 1
            continue
        died_in, died_after, alive, unknown = 0, 0, 0, 0
        cutoff = r["at"] + window * 86400
        read_any = False
        for path in r["paths"]:
            spans = added_ranges(root, r["commit"], path)
            deaths = death_map(root, r["commit"], path, payload["tip"])
            if spans is None or deaths is None:
                continue
            read_any = True
            owned = set()
            for a, b in spans:
                owned.update(range(a, b + 1))
            # one death per line, not one per hunk. Summing hunk overlaps double counted
            # where reported ranges overlapped, which produced a negative alive count on
            # a real repository: a line is either alive or it died once.
            death_at = {}
            living = set()
            for d in deaths:
                for ln in range(d["orig"], d["orig"] + d["count"]):
                    if ln not in owned:
                        continue
                    if d["alive"]:
                        living.add(ln)
                    else:
                        death_at[ln] = d["at"]
            for at in death_at.values():
                if at is None:
                    unknown += 1
                elif at <= cutoff:
                    died_in += 1
                else:
                    died_after += 1
            # reported at the tip, or never reported at all: still there
            alive += len(owned) - len(death_at)
        if not read_any:
            r["verdict"] = "NOT_MEASURED"
            r["detail"] = "no file from this commit could be read"
        else:
            total = max(1, died_in + died_after + alive + unknown)
            r["died_in_window"] = died_in
            r["died_after_window"] = died_after
            r["still_alive"] = alive
            r["unknown"] = unknown
            r["measured_lines"] = total
            if died_in / total >= REWRITTEN:
                r["verdict"] = "REWRITTEN_WITHIN_WINDOW"
            elif (died_in + died_after) / total >= REWRITTEN:
                r["verdict"] = "SUPERSEDED_LATER"
            else:
                r["verdict"] = "STILL_STANDING"
        counts[r["verdict"]] += 1
        out.append(r)
    # A commit that is still standing is a count. Keeping every row produced 115,045
    # bytes on a real repository against a 65,536 byte ceiling, and the run reported
    # itself incomplete instead of reporting anything useful.
    CAP = 80
    reportable = [r for r in out if r["verdict"] != "STILL_STANDING"]
    omitted = max(0, len(reportable) - CAP)
    for r in reportable:
        r.pop("paths", None)
    # A bounded sample of the standing commits still travels. The count alone cannot
    # show that an untouched commit has every line alive, and that is the assertion which
    # pins the bug where a living line read as a death.
    standing = [r for r in out if r["verdict"] == "STILL_STANDING"]
    for r in standing[:5]:
        r.pop("paths", None)
    payload["standing_sample"] = standing[:5]
    payload["measured"] = reportable[:CAP]
    payload["verdicts"] = dict(counts)          # exact, over every commit measured
    payload["rows_omitted"] = omitted
    payload["unmeasured"] = unmeasured
    payload["budget_exhausted"] = unmeasured > 0
    payload.pop("rows", None)                   # the survey rows are not needed downstream
    return payload


def resolve(value):
    if value == "demo":
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo", "repo")
    return value


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: rework.py survey|measure <root> [args]\n")
        sys.exit(2)
    mode, raw = sys.argv[1], sys.argv[2]
    if raw != "demo" and not os.path.isabs(raw):
        sys.stderr.write(
            "root must be an ABSOLUTE path, or the word demo. Got: " + raw + chr(10) +
            "A step runs inside rote's own workspace, not the directory you were "
            "standing in, so a relative path silently reads the wrong tree." + chr(10))
        sys.exit(2)
    root = resolve(raw)
    if not os.path.isdir(root):
        sys.stderr.write("no such directory: " + root + chr(10))
        sys.exit(1)

    if mode == "survey":
        commits = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 300
        window = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 30
        print(json.dumps(survey(root, max(10, min(2000, commits)),
                                max(1, min(365, window))), separators=(",", ":")))
        return
    payload = json.loads(sys.argv[3])
    if payload.get("status") != "ok":
        print(json.dumps(payload, separators=(",", ":")))
        return
    print(json.dumps(measure(root, payload), separators=(",", ":")))


if __name__ == "__main__":
    main()
