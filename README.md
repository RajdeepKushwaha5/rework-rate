# rework-rate

**Your change passed review. Did it survive the month?**

Code gets reviewed, merged, and thrown away, and nobody counts how often. This counts it,
from git alone.

```bash
rote play run https://play.modiqo.ai/rajdeepkushwaha/rework-rate
```

That runs a small repository bundled with the play, so it works on a clean machine with
nothing set up. For your own code pass an absolute path: `root=/path/to/repo`.

Zero credentials. It never checks anything out and never writes to your repository.
`python3` and `git`, nothing else.

## What it asks

For each commit, git reports the exact lines it added. `git blame --reverse` reports, for
each of those lines, the last commit in which it still existed. That gives three answers.

| verdict | what is being claimed |
|---|---|
| `STILL_STANDING` | The lines are still in the file. Not a claim that they are good. |
| `REWRITTEN_WITHIN_WINDOW` | Most of them were replaced inside the window, thirty days by default. Work that looked finished and was not. |
| `SUPERSEDED_LATER` | Replaced, but long after the window. A codebase moving on is not rework. |

A commit younger than the window is **never judged**, because it has not had time to be
rewritten. Counting it as survived would be the false clean this refuses to produce.

## Why the window is the whole idea

Ranking by what survives to today sounds right and is wrong. The first run of this ranked
a real repository and put dependency bumps at the top:

```
62fde54d  survival 0.0%  update test workflow trigger
2b4057a3  survival 0.0%  update dev dependencies
a72b0d77  survival 0.0%  Bump the github-actions group with 3 updates
```

A pinned version line is *meant* to be replaced by the next bump. Nothing was wrong. The
question is not whether the lines are gone, it is whether they died **young**.

## What it found

On `pallets/click`, six commits rewritten inside thirty days. One of them:

```
6c4a77b  2026-03-02  Use `default=True` as a sentinel for non-boolean flags
bb7be1f  2026-03-02  Revert "Use `default=True` as a sentinel for non-boolean flags"
```

Landed and reverted the same day. 25 lines added, 23 dead inside the window.

## Four things that were wrong first, all measured

**Rename detection dominated everything.** `git log --numstat` took **53 seconds** on one
repository and **150** on another. With `--no-renames`: **82ms** and **614ms**. It is also
the correct reading, since a renamed file's lines are new lines at a new path, and
crediting survival across a rename attributes them to a commit that never wrote them there.

**`--line-porcelain` emits eighteen times the bytes** of `--incremental` for the same
answer, because it repeats full metadata per line instead of once per hunk.

**Blaming an old revision costs 348 seconds** on a large file that takes 10 milliseconds at
HEAD, because blame away from HEAD loses the commit-graph shortcuts. `git blame --reverse`
answers the same question in 16 milliseconds. That one change took a repository from 239
seconds to 5.

**And the one that made every number wrong.** `--reverse` names the **tip** commit for a
line that is still alive: it means "this line last existed at HEAD". Reading that as a
death made surviving lines count as rewritten whenever the tip fell inside the window. The
headline finding at the time — 61 of 63 lines supposedly dead — was false. An independent
check caught it: `git log --ancestry-path` showed nothing had touched those files, and the
added code was still at HEAD.

There is now a bundled case whose only job is to fail if that bug returns.

## It checks itself in front of you

```
Self-check: PASSED (14/14 bundled analyzer cases)
```

Before reading your history, the shipped analyzer is fed the bundled repository and has to
reproduce it. A failure withholds the findings rather than dressing them up. Coverage is
itself a case, so a verdict with no positive case fails the check instead of passing
quietly, and so is discovery, because a history read that returns nothing looks exactly
like a repository where everything survived.

Proved by mutation, each restored byte-identically by checksum:

```
tip-is-alive rule removed     -> 12/14   the standing case and its guard both fail
too-recent guard removed      -> 10/17   three cases appear, and the analyzer stops running
bot author detector removed   -> 13/14
git failure read as an answer -> 12/14
```

That last one was added after the fact. Every case here ran on a machine where git works,
so the branch that reads a *failed* git was never exercised, and it reported "that path is
not inside a git repository" about a directory that plainly was one. git failing is this
play failing to look; it is not a fact about your repository, and it now says so.

## Licence

MIT
