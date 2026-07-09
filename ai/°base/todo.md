/plan I want to have "explore" commands written to the prompt log, similar to `Task Notification:`.
Claude CLI writes:
```log
Explore(Explore record-memory hook and commit logic)
```
I think we could write it like this:

@ai/°base/errors/9.expected.md

- [x] Done

---

If `ai/.debug` file exists, all hooks shall write their payload to `ai/output/debug/`. Adapt paths for `°base` as usual.

- [x] Done

---

Record `/compact` results, too.
For `/compact`, see @ai/°base/errors/10.md and @ai/°base/errors/10.expected.md

- [x] Done

---

For the `Task Notification:` prompt update, from [5.expected.md](./errors/5.expected.md), the last line (tools, tokens, time) is missing. Implement it.

- [x] Done

---

Fix prompts-while-running for claude being lost.
E.g. in this repo/prompt (blockquote me):
> Change the output dir for agent stuff to `ai/output/agents/NNN.task-id/` (+`output`)

- [x] Done

---

Alright, let's modernize the question prompt log.
Currently, we have question output to `query.md` like @ai/°base/errors/12.md
Instead I want it changed to @ai/°base/errors/12.expected.md

Construct a unittest using `12.expected.md`'s `json` under **Input JSON (Claude)** as input and asserting the markdown under **`query.md` addition** as output.
For implementing, the sections **Summary of format options** and **Notes** may be helpful.

- [x] Done

---

Additionally, using @ai/references/https/developers.openai.com/codex/mcp.md and @ai/references/https/github.com/7c/bugsink-mcp/blob/87ce8bba8d65b15004f80314fce5845543ae193b/README.md support MCP sync.
While at it configure it directly with `envmcp` to use `ai/.env` of a repo, see @ai/references/https/github.com/griffithsbs/envmcp/blob/9dc9d6510aa07f999095b7bb5eed636428eebec5/README.md for that tool.

- [x] Done

---

/plan I need to create a tool for splitting off AI stuff & `base` from a project, as that project is not supposed to have this base or AI mentions in it.
The general concept is that for a branch, AI versions can exist.
Generally, there's the clean branch with any name, but examples could be `feature/ABC-123/something/mr1`, `feature/ABC-123_something`, `ABC-1234_foo`, `bugfix/foo-crash` or just `i-did-a-thing`.
This now gets two additional branches, one for direct work and commits (unclean) and one for tracking commit relations etc. (history)

type | branch name format | purpose
--- | --- | ---
clean | `{branch}` | the clean branch, not containing any mention of or commits from this base or anything related to AI assistant usage (prompts, refs, etc.). This one is save to release to the public or the customer.
unclean | `ai/UNCLEAN/{branch}` | this is the branch to work on. It will allow you to commit ai, non-ai (code), or a mix of both as you want, making actually editing code etc. easier.
history | `ai/history/{branch}` | this stores the metadata, and ai stuff, basically it's the left overs after you extract the clean parts from the unclean branch. So it contains every change which is not part of the code, so we can still reuse our AI instructions for later branches as well, e.g. `CLAUDE.md`. Also contains all metadata to sync the stripped down **clean** with the other branches.

The general concept is to have those be synced automatically.
There's also a `ai/history/master` branch (or whatever the repo's main branch is, `master`, `main`, `mane`, etc.),
which will be holding the history of the AI stuff for after a **clean** variant was merged into the main branch. It then is the base for the next **unclean** and hence also the next **history** branch - while the main branch itself is the base for the new **clean** branch.

#### `update-history-master`
The script needs a `update-history-master` command, which does create a new base history.
The master history is constructed like this in terms of commits:

category | sorting | description
--- | --- | ---
`master` | comes first | all commits from the origin's current main branch)
base | after `master` | this are merge commits of `base/base` into the `ai/history/master`.
**history** | after `master` | commits of all **history** branches which **clean** branches were already merged into master.
merge | after `master` | this are empty commits marking/referencing the last (= merge) commit merge of an **clean** branch which had **history** into master, so it comes after the rebased commits of that **history** branch.

The difficult part is that after an update of **clean `master`**, the **`master` history** shall be rebased onto that.
While that `master` is updated, we need to keep track of merges of **clean** branches which have an existing **history** or **unclean** variant, so we can cherry-pick those commits, too.

With that general rebase strategy, and flattening the history of already-merged ai branches, it's gonna be difficult to support merging this `base`, and later on updates to the `base` to it occasionally.
How can we handle that gracefully?

Additionally, there should be a `--force-merge=<branch name>` option (multiple), which would force a manual history branch merge before it is actually included in the clean master.

It does roughly the following:
1. checkout `master`
2. check if `master` is up to date, if not, ask whether we should `git pull` (default: N)
3. checkout `base`
4. check if `base` is up to date, if not, ask whether we should `git pull` (default: N)
5. check out `ai/history/master`
6. rebase `ai/history/master` onto `master`
7. merge the most recent `base/base` into `ai/history/master`.

The problem to think about is how we handle `base/base` merges. Merges doesn't really like rebases...
Maybe the script manually rebases, processing the changes as usual until those would occur, then automatically merge the old merged commit of `base/base` freshly into `ai/history/master` (again), while applying the old conflict resolution once more (so if we can't rebase a merge, we instead recreate it).

#### `sync-splits`
This subcommand allows to sync **clean**, **unclean** and **history** versions of a branch.
Those branches not created yet will be added.

##### Generating **clean**
Take **unclean** and strip all AI content, and outright drop ai-only commits.

A branch's **clean** branch will start on the `master` branch, and add commits to that.

##### Generating **history**
Take **unclean** and strip all code content, but keep the commits even if empty.
Add metadata to commits or add specifically crafted metadata commits to store everything needed to sync an **clean** branch with an **history** branch back to **unclean**.

A branch's **history** branch will start on the `ai/history/master`, and add commits to that.

##### Generating **unclean**
This is the most difficult one, as you need to merge **clean** and **history** back.

Steps:
1. Start from `history/master` (i.e. the specific commit of `history/master` the branches **history** is based on, so that all the previous AI stuff is contained.
2. Cherry-pick the commits from **clean** and **history** in order.

In that there can be different commits to process:
1. code only
    - the commit exists only in **clean**, and there's no relating **history** for it
      - e.g. it is a quick commit added after the last sync, i.e. to hotfix something, or if ai was not needed.
2. history only
    - this commit exist only in **history** and there's no **clean** commit matching.
      - e.g. just an update to `CLAUDE.md` without any code changes.
3. code + history
   - the commit is in both other branches, and can be merged back into a single commit.
     - e.g. prompt/query file update + the actually implemented changes.

A branch's **unclean** branch will start on the `ai/history/master`, and add commits to that.

#### `rebase-branches-to-master`
This one takes all of the three branches and rebases it onto the current `master` variants.

- **clean**
    - This one will be rebased onto the also **clean** `master` branch.
- **history**
    - This will be rebased onto the`ai/history/master` branch.
- **unclean**
    - This will be rebased onto the **history** branch just created.

Also think about how to handle one or two of them branches missing.


---

Additionally, we need:

1. branch push name check
   1. do not allow **unclean** or **history** format-named branches to be pushed to a remote called `origin`.
2. branch push content check
   1. block ai or ai-containing commits to be pushed if the branch name is not **unclean** format.
   2. block code or code-containing commits to be pushed if the branch name is not **history** format.

- [ ] Done
