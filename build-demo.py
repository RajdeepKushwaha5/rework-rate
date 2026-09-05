"""Build the bundled demo repository for rework-rate.

Four cases, because each one pins a rule that would otherwise be unprotected:

  rewritten   lines added, then replaced eleven days later, inside the window
  standing    lines added and never touched again. This is the case that pins the bug
              where blame --reverse reports the TIP commit for a line that is alive;
              reading that as a death made every surviving commit look rewritten
  superseded  lines added, replaced ninety days later, well outside the window
  too recent  committed three days ago, so it cannot have been rewritten yet and must
              never be counted as survived

Dates are set explicitly so the windows are exact rather than whatever today happens to
make them.
"""
import os, shutil, subprocess, time

NL = chr(10)
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo")
repo = os.path.join(P, "repo")
if os.path.isdir(P):
    shutil.rmtree(P)
os.makedirs(repo)

NOW = int(time.time())
DAY = 86400

env = dict(os.environ)
env.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})


def git(*args, **kw):
    e = dict(env)
    when = kw.get("when")
    who = kw.get("who", "Demo Author")
    if when is not None:
        stamp = "%d +0000" % when
        e.update({"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    e.update({"GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": "demo@example.invalid",
              "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": "demo@example.invalid"})
    subprocess.run(["git", "-C", repo] + list(args), check=True, capture_output=True, env=e)


def write(rel, lines):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(NL.join(lines) + NL)


def block(prefix, n):
    """Every line carries the prefix, so a rewrite really replaces every line.

    The first demo used a shared body of `return 0`, which git correctly credited to the
    original commit because the bytes never changed. Half of a rewritten commit then
    looked alive, and the demo taught the wrong lesson.
    """
    out = []
    for i in range(n):
        out.append("def %s_%02d():" % (prefix, i))
        out.append("    return '%s-%02d'" % (prefix, i))
    return out


git("init", "-q")

# ---- base
write("README.md", ["# demo", "", "A small history with four kinds of commit."])
git("add", "-A"); git("commit", "-qm", "initial", when=NOW - 220 * DAY)

# ---- REWRITTEN_WITHIN_WINDOW: added, then replaced 11 days later
write("app/checkout.py", block("charge", 12))
git("add", "-A")
git("commit", "-qm", "add checkout handling", when=NOW - 200 * DAY)

# ---- STILL_STANDING: added and never touched again. Pins the tip-is-alive rule.
write("app/stable.py", block("stable", 14))
git("add", "-A")
git("commit", "-qm", "add stable helpers", when=NOW - 196 * DAY)

# ---- SUPERSEDED_LATER: added, replaced 92 days later, well outside the window
write("app/legacy.py", block("legacy", 12))
git("add", "-A")
git("commit", "-qm", "add legacy helpers", when=NOW - 192 * DAY)

write("app/checkout.py", block("settle", 12))
git("add", "-A")
git("commit", "-qm", "rewrite checkout handling", when=NOW - 189 * DAY)

# ---- a bot commit, so the author flag has a positive case
write("requirements.txt", ["requests==2.31.0", "urllib3==2.0.7", "certifi==2023.7.22",
                           "idna==3.4", "charset-normalizer==3.3.0"])
git("add", "-A")
git("commit", "-qm", "Bump requests from 2.30.0 to 2.31.0",
    when=NOW - 150 * DAY, who="dependabot[bot]")

write("app/legacy.py", block("modern", 12))
git("add", "-A")
git("commit", "-qm", "replace legacy helpers", when=NOW - 100 * DAY)

# ---- TOO_RECENT: three days old, cannot have been rewritten yet
write("app/fresh.py", block("fresh", 10))
git("add", "-A")
git("commit", "-qm", "add fresh helpers", when=NOW - 3 * DAY)

for junk in ("hooks", "description", "COMMIT_EDITMSG", "logs", "branches"):
    q = os.path.join(repo, ".git", junk)
    shutil.rmtree(q, ignore_errors=True)
    if os.path.isfile(q):
        os.remove(q)

print("demo repo at", repo)
out = subprocess.run(["git", "-C", repo, "log", "--format=  %h %ad %an  %s",
                      "--date=short"], capture_output=True, text=True, env=env)
print(out.stdout)
