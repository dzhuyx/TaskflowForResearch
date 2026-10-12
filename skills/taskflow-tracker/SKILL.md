---
name: taskflow-tracker
description: Set up and maintain a high-level TaskPaper tracker for a focused research effort that runs across several Claude Code sessions, viewed and edited on the user's local taskflow board (a dependency graph with checkboxes). The tracker holds documents to review, hand-offs back to named sessions, and Claude-run jobs and work packages, all gated by @needs. Use when the user asks to set up a tracker or taskflow for an effort ("use the taskflow workflow", "make a tracker for X", "add this to the tracker"); when working on an effort whose folder has a *_tracker.taskpaper; when a session produces a document for the user to review, starts or finishes a long-running job, or stops to wait for the user's instructions.
---

# Taskflow tracker: high-level effort management across sessions

The user runs several Claude Code sessions in parallel on one effort, each on a different direction. The user manages them from one TaskPaper file per effort, viewed on the **taskflow board**: a local web page that draws the items as a dependency graph. On the board the user ticks items off, opens documents through Hookmark, and copies hand-off prompts. The TaskPaper file is the single source of truth. Every session, TaskPaper and the board edit the same file.

## Tools

The tools live in your taskflow folder (the repository this skill comes from). Call the script `TF="<your taskflow folder>/taskflow.py"`.

| Command | What it does |
|---|---|
| `python3 "$TF" <new.taskpaper> --init --title "Name" --prefix ab --emoji 🧬 [--purpose "..."] [--record PATH]` | Creates a tracker from `templates/tracker_template.taskpaper`, with its board launcher in `launchers/`, and adds it to the hub's list with its emoji. Refuses to overwrite. |
| `python3 "$TF" <tracker> --add-hook-links` | Adds a Hookmark `hook://` line under every `file://` line that lacks one. It works through AppleScript and never touches the clipboard. Without Hookmark it does nothing (and says so). |
| `python3 "$TF" <tracker> --check` | Validates the tracker and prints counts, ready items and warnings. Exit code 1 means fix something. It warns, among other things, about an item marked waiting none of whose `@needs` is open, which the board shows as ready, and about a review that is ready for the user with no document linked. |
| `python3 "$TF" <tracker> --make-launcher` | Writes a double-click `.command` that opens the board for an existing tracker. |
| `python3 "$TF" <tracker> --open` | Starts the board on 127.0.0.1 and opens the browser. The user normally uses the Taskflow app (`~/Applications/Taskflow.app`, one window per tracker) or a launcher instead. |
| `python3 "$TF" <tracker> --job-start <id> --slurm <ids> [--host cluster] [--results PATH ...]` | Registers a running cluster job for 💻 item `<id>` (comma-separated SLURM job ids) and sets it `@status(inprogress)`. |
| `python3 "$TF" <tracker> --job-start <id> --marker <file> [--results PATH ...]` | Registers any other job: it counts as finished when the marker file exists. Use the file the job writes last (an agent review: its report). |
| `python3 "$TF" <tracker> --job-run <id> [--results PATH ...] -- <command ...>` | Runs a local command as the job and records its outcome when it exits. Start it in the background for long runs. |
| `python3 "$TF" <tracker> --hub-add` / `--hub-archive` / `--hub-unarchive` | Adds an existing tracker to the hub's list (`trackers.json`) / hides a finished one from the hub, leaving its file alone / shows it again. Archive only when the user says the effort is finished. |
| `python3 "$TF" --hub-list` | Prints the hub's trackers with their counts. |
| `python3 "$TF" <tracker> --deadline hard\|soft YYYY-MM-DD [--what "what is due"]` | Sets the project's hard (external) or soft (internal) deadline on the tracker's title line, with a backup; `""` as the date removes it. Only when the user gives the date. |
| `python3 "$TF" <tracker> --status <id> inprogress\|waiting\|open [--note "..."]` | Sets an item's status with the board's edit (re-read, conflict check, backup), with an optional dated note. `inprogress` also adds your `@sessionid` and `@started(YYYY-MM-DD HH:MM)` (kept until the status goes back to open or waiting; the board shows how long your work has run from it). Refuses an item that is done. |
| `python3 "$TF" <tracker> --tick <id> [--note "one-line outcome"]` | Ticks an item done, with the outcome as a dated note. |
| `python3 "$TF" --jobs` / `--sweep [--force]` | Lists registered jobs / checks them now. `--no-review` on a job skips the results review. |

`README.md` in the same folder describes the board in detail. Run `python3 test_taskflow.py [tracker]` there after changing the code.

## What goes in a tracker: management level only

- **Review items**, `@type(review) @owner(me)`: one per document the user needs to read.
  - The note says where in the document its questions are, for example "Questions: log §7, Q3–Q10".
  - Questions and decisions live in the document and are answered there. Never create tracker items for individual questions, decisions or small steps.
  - **Exception: a decision that blocks a task**. A task that cannot go on until the user decides something gets `@needs` on the item that holds that decision: the open review of the document where the question is asked, or the review of the checkpoint report that asks it. If no open item holds it, because the document's review is finished or the user deferred the question, add one decision item, `@type(review) @owner(me) @status(open)`, titled "Decide <question>". Its note links the document where the question and its options are, says where in it, and gives what the user has said so far, and it may `@need` the reports the user wants to read first. Add one such item per pending decision that blocks work; questions that block nothing stay in their documents.
- **Session hand-offs**, `@type(session) @owner(me) @session(title) @sessionid(<id>)`: one per return to a named Claude Code session to give it its next instructions.
  - It is gated by `@needs` on the reviews that must be finished first.
  - The note contains:
    - the session id;
    - `Resume it from the VS Code session list, or in a terminal with: claude --resume <id>`;
    - a line starting `Prompt:` with a draft instruction for the user to paste.
- **Jobs and work packages**, `@type(work)`: one per coherent piece of work.
  - `@owner(claude)` marks work a Claude session runs on its own: cluster simulations, long local checks, agent reviews. It shows as 💻.
  - A work package that a session will start on the user's instruction is gated by its hand-off.
  - The user's own work packages are `@owner(me)`.
- **Closing the loop.** Every 💻 job gets a results review: a 🧑 review item with `@needs` on the job, linking the results. The next hand-off is gated on that review, not on the raw job.
- **Checkpoint reports**: at intermediate steps, not only when work ends, Claude's results come to the user as a report document for the user to review, discuss and decide on. Each report is one 🧑 review item with `@needs` on every 💻 task whose results it reports, so the board draws arrows from those tasks to it, and `@sessionid` of every session that contributed. Several Claude tasks, from one session or several, may feed one shared report; separate topics get separate reports. The related open sessions huddle first (the `session-huddle` skill). See "At a checkpoint: huddle, then a report for the user" below.
- **Documents Claude is revising are Claude's tasks, not the user's reviews**. A document may be sent back after the user's review, or Claude may rewrite it over a long session. Either way, its item is `@type(work) @owner(claude) @status(inprogress)` with the writer's `@session` and `@sessionid`, for as long as Claude is revising it. It is never left as an open 🧑 review that the user cannot act on.
  - When the revision is posted, the same item goes back to the user: `@type(review) @owner(me) @status(open)`, linking the new version and with a dated line saying what changed.
  - Keep one item per document; it alternates between a review and Claude's task. See "When a document goes back for revision" below.
- **Automatic completion.** A registered job is ticked automatically. Claude Code hooks (`Stop` and `SessionStart` in `~/.claude/settings.json`) run `taskflow.py --sweep-detached` whenever any session starts or finishes a reply; the open board sweeps every minute. Sweeps run at most every 2 minutes, and each SLURM job is checked over `ssh cluster` at most every 10 minutes.
  - When a job finishes, the sweep ticks it `@done(date)` with a "Finished automatically" note, adds the results review under "Review — results of Claude jobs" with the next free id (unless a review already needs the job), links the `--results` paths, adds Hookmark links, and appends to the Update log.
  - When a SLURM job ends in any state other than COMPLETED, or a `--job-run` command exits non-zero or is killed, the item gets a note and `@status(waiting)` instead.
  - If the cluster can't be reached (the ssh master connection has lapsed), the job is kept and checked again later. Marker jobs never fail on their own; if one is abandoned, say so on the item and tick or reopen it by hand.
  - The log is `jobs/sweep.log` in your taskflow folder.

## Tags and rules

- `@id(<prefix>.N)` on every item. Ids are unique and never reused; record retired ids in the Update log.
- `@sessionid(<full session id>[, <id> ...])` on every item that comes from a Claude Code session. Use the full 36-character id, which never changes, unlike the title.
  - **Which session** depends on the item:
    - a review: the session that wrote the document;
    - a hand-off: the session to go back to;
    - a job or work package: the session that runs it;
    - The user's own work package: the session that proposed it.
  - Several sessions are allowed, for example on a shared log.
  - Your own id is in `$CLAUDE_CODE_SESSION_ID`.
  - **Forks share their parent's history.** Work done before a fork belongs to the parent session. To attribute old work, look for the session id inside scratchpad or task paths in the transcript's tool calls, not for which transcript contains it.
  - Registered jobs get the tag automatically, and so do their results reviews.
  - On the board, `--check` and the panel show each session's current title, read from its transcript. The panel copies the resume command (`cd "<project>" && claude --resume <id>`), and a menu filters by session.
  - Items the user adds need no `@sessionid`.
- `@needs(ab.3, ab.7)` lists the items that must be done first. The board draws arrows from them and marks the item ready or blocked.
  - **Ready or blocked comes from `@needs` alone**. `@status(waiting)` does not block: the board and the hub show a waiting item as ready once all its `@needs` are done, and rank the user's tasks by what they unblock. So every task that waits for something, including another session's task and the blocked part of a running work package, carries `@needs` on the item it waits for. A note saying what it waits for is not enough. `--check` warns about a waiting item none of whose needs is open.
- **Checklists inside a task**: when the user has several steps to work through for one task, write them as indented `- ` lines under the task (TaskPaper sub-tasks), one step per line, with no `@id` and no other tags; a step that is already done gets `@done(YYYY-MM-DD HH:MM)`. They are not tracker items: the board and hub show them as a tickable checklist with its progress ("2/5") on the card, and the user can add or remove steps in the board's panel. Never end a step with `:` (TaskPaper would read a project). Keep the rule above: no separate tracker items for small steps.
- Status is one of `@status(open)`, `@status(inprogress)` or `@status(waiting)`. When finished, tick it with `--tick <id> --note "<outcome>"`, which writes `@done(YYYY-MM-DD HH:MM)` and the outcome as a dated note. Never delete an item.
- Other tags: `@priority(high)` or `@priority(low)`, and `@added(YYYY-MM-DD HH:MM)` on every item you add, with the time from `date '+%Y-%m-%d %H:%M'` (the hub's Gantt chart draws each item from `@added` to `@done`). Older items carry dates only; leave them. Never guess a missing `@added`: add it only if you know when the item was added. A tracker's own header may still show the date-only form; follow this skill.
- Project deadlines are tags on the tracker's title line only: `@harddeadline(YYYY-MM-DD, what is due)`, an external, real deadline, and `@softdeadline(...)`, an internal one that may slip a few days but should be kept. Read them before planning, and tell the user when work on the tracker puts one at risk. The hub ranks the user's tasks by them after `@priority` and after how much finishing each task frees Claude to work (each Claude task waiting on it counts 1, shared among everything that task still waits for, a hand-off half that), so accurate `@needs` on Claude's tasks matter as much as on the user's. Set them only with dates the user gives, never a guessed date, with `--deadline` or the user's hub; never put them on an item.
- **Board emoji:** 🧑 for `@owner(me)`, 🧑‍💻 for `@type(session)`, 💻 for `@owner(claude)`.
- **Documents** are two lines in the item's note: the `file://` URL, alone on its line, with spaces as `%20` and `+` left as is; then its `hook://` URL, added by `--add-hook-links`. The board lists each document by file name and opens it through Hookmark.
- **TaskPaper syntax traps:**
  - Tag values cannot contain parentheses; write `[fork]`, not `(fork)`.
  - A note line must not end with `:`, or TaskPaper turns it into a project.
  - Do not start a note line with `- `, or it becomes a task.
  - Indent with tabs only.
- **Update log:** append one line per session that changes the file, ending "Highest @id used: ab.N."

## Procedures

### Start a tracker for a new effort

1. Agree with the user on the effort's name, the id prefix, and where the tracker lives. The default is the effort's plan or log folder, named `<effort>_tracker.taskpaper`.
2. Run `--init`, giving `--record` as the effort's main log or plan, and `--emoji` with one emoji that fits the effort's topic and that no other project in the hub has (`--hub-list` shows theirs). It also adds the tracker to the hub; a tracker made any other way needs `--hub-add`, which takes `--emoji` too. Left out, the hub gives the first unused emoji of its picker; the user can change it in the hub.
3. Seed the review items, hand-offs and work packages with `@needs`, write the files, then run `--add-hook-links` and `--check`.
4. Add a pointer to the tracker near the top of the effort's main log.
5. Note in the project's CLAUDE.md (or Claude Code's memory) that the tracker exists and must be read first.
6. Tell the user where the launcher is: its path is printed by `--init` and written into the tracker header.

### Every session working on an effort with a tracker

- **At the start:** read the tracker and the newest entries of the main record.
- **Every item you add** carries `@sessionid($CLAUDE_CODE_SESSION_ID)`, or the id of the session it comes from if that is another session.
- **When you produce a document for the user:** put its questions inside the document, then link it on its review item: add the review item, or, if one already exists for it (a report's review created early, a decision item), add the document's `file://` line to that item, run `--add-hook-links`, and tick the task that wrote it. A review that is ready for the user must link its document.
- **When a document goes back for revision**: this applies when the user asks for a new version, or the user's comments send a document back for rewriting, and the revision will take more than a short edit.
  - **Before starting**, turn its review item into your task. Edit the item line to `@type(work) @owner(claude) @status(inprogress)` with your `@session(title)` and `@sessionid`, and retitle it "Revise <document> to version N: <goal>".
  - **Add a dated note** giving the scope and naming the version the user annotated. Never overwrite that annotated version; the new version is a new file.
  - **Dependent documents:** do the same for any document that must be revised after this one, and gate it with `@needs` on this item.
  - **When the new version is posted**, give the item back to the user. Edit the line to `@type(review) @owner(me) @status(open)` and retitle it "Review <document>, version N". Replace the old `file://` line with the new version's, run `--add-hook-links`, and add a dated line saying what changed and what is not yet cross-checked.
  - `--status` cannot change `@type` or `@owner`. Edit the line with the re-read check below.
- **Your own 💻 tasks (`@owner(claude)`) carry their real status, always**. The user's hub lists the tasks Claude is running, meaning yours marked in progress, with when their session last did anything. A task you work on without marking it in progress is invisible to the user.
  - **When you start working on one**, run `--status <id> inprogress`, which also adds your `@sessionid`. Do this for work you do in the conversation too, not only for registered jobs.
  - **When you stop before it is finished:** if you are waiting for the user, first give the task `@needs` on the item that holds the user's decision (see the exception under "What goes in a tracker"), then run `--status <id> waiting --note "<what you wait for>"`. Add a hand-off (below) only if resuming needs the user's instruction. If you set it aside, run `--status <id> open`.
  - **When it is finished**, run `--tick <id> --note "<one-line outcome>"`, unless it is a registered job, which the sweep ticks.
  - Never leave a task in progress when you stop: the hub flags one whose session has been quiet for an hour and that has no registered job.
- **At a checkpoint: huddle, then a report for the user**.
  - **When:** a result changes what should happen next (a choice between directions, a surprise, an assumption that failed); several Claude tasks have results on the same question; a phase of a long work package is finished; or the next step is expensive or hard to undo (long cluster runs, rewriting a manuscript section). Not for routine progress: no report only to say that work goes on.
  - **Huddle the sessions** with the `session-huddle` skill. The sessions in the huddle are the open sessions whose 💻 tasks in this tracker bear on the topic: read the tracker for those tasks and their `@sessionid`. The session that reaches the checkpoint convenes, unless the user names another. In the claim, propose the reports: which topics, one document per topic or one shared document, and who writes each. Each document has one writer, as the huddle skill requires; the other members send that writer their material (results, tables, file paths, pasted text), and the cross-check pairs read each report before it goes to the user. If no other open session has related tasks, write the report alone.
  - **Sessions that are not open** cannot be huddled: use only what they have posted (documents and result files in the effort's folders, their tracker items and notes), never their scratchpads or transcripts, and say in the report what is missing from them. The user can resume them for the next round.
  - **What a report holds:** what was done and by which task; the results, as tables and figures; what they mean and what limits them; the cross-check outcome; and a numbered **Decisions** section, each decision with its options, the recommendation, and what each option would start next. This section is the huddle's one question to the user: nobody asks the user separately, and nobody settles a decision in the user's place.
  - **In the tracker** (the convener is its single writer during the round; members send it their lines):
    1. Close the phase each report covers. If the work was one 💻 task, edit its title to name the phase it finished and tick it with `--tick <id> --note "<outcome>"`; add a new 💻 task for what follows. Plan long work as phases from the start, so a checkpoint ticks one.
    2. Add one review item per report: `@type(review) @owner(me)`, titled "Review checkpoint report: <topic>", with `@needs` on each 💻 task it reports, so the board draws arrows from them to it, and `@sessionid` of every session that contributed. Its note links the document and says where the decisions are, for example "Decisions: §5, D1–D4".
       - **A review is never ready without its document**. If you add the review before the report is written, for example so that the sweep adds no per-job results reviews, also add a 💻 task that writes the report (its writer's `@sessionid`; in progress while it is drafted and cross-checked), and give the review `@needs` on it. When the report is posted, link the document on the review and tick the writing task.
    3. Gate on it what depends on the user's decisions: the next 💻 tasks and each member's hand-off get `@needs` on the review. This includes tasks that already exist and wait for the same decision, in any session (look for waiting items whose notes name it), and the blocked part of a work package that is otherwise running: split that part into its own task, gated on the review. Work that goes on whatever the user decides is not gated. Then run `--check`, which warns about a waiting item with no open need.
    4. Once the user has finished a review, new results go in a new version or a new report (see "When a document goes back for revision").
  - **Close the round** as the huddle skill says, and send the user one message naming each report, its review item's id, and the decisions it asks for.
- **When you start a long-running job:** add its 💻 item, then register it with `--job-start` (`--slurm` for a cluster job, `--marker` for anything else) or run it through `--job-run`. Pass `--results` for each output that the user should open.
- **When the job finishes:** the sweep ticks it and adds the results review. Do not tick a registered job by hand. If you finish its write-up before the sweep runs, `--sweep --force` records it at once. A job that cannot be registered is ticked by the session, which also adds the results review.
- **When you stop to wait for the user:** gate the waiting work itself with `@needs` on the item that holds the user's decision: a review, a checkpoint report's review, or a decision item (see the exception under "What goes in a tracker"). If resuming will need the user's instruction, also add a hand-off for yourself, gated the same way, and gate your next work package on that hand-off. Give both `@sessionid` with your id. Your identifiers:
  - **Session id:** `echo $CLAUDE_CODE_SESSION_ID`. The folder name in your scratchpad path, `/private/tmp/claude-*/<project>/<session-id>/scratchpad`, gives the same id.
  - **Title:** `ListAgents` prints "This session is <title>". If the session is unnamed, ask the user to `/rename` it, or use `unnamed <first 8 characters of the id>`.
- **Writing the file:**
  - For status changes and ticks, use `--status` and `--tick` rather than editing the line yourself: they re-read the file, check it did not change meanwhile, keep a backup, and keep `@started` consistent with the status. If you must write a status by hand, for example on an item you add for another session (`--status inprogress` would stamp your own `@sessionid`), keep the same rule: an item in progress carries `@started(YYYY-MM-DD HH:MM)` with the time it started, and an item set to waiting or open carries none.
  - Otherwise, re-read it immediately before writing, and write only if it is unchanged since you read it, because other sessions edit it concurrently.
  - Keep a copy of the previous version in your scratchpad.
  - After writing, run `--add-hook-links` and `--check`.
- **In chat:** report what you added, with the item ids.

### When the user works from the board

The user opens trackers in the Taskflow app, usually from the tracker header's **Open board** link: a `taskflow:///<path>` link (spaces as `%20`), made by `app_url()` in `taskflow.py` and written by `--init`. File › Open, Open Recent and Finder › Open With › Taskflow also work; each window runs its own `taskflow.py --app` server, with logs in `~/Library/Logs/Taskflow/`. After changing `macapp/Sources/`, rebuild with `macapp/build_app.sh --install`; changes to `taskflow.py` or `static/index.html` take effect when a window is reopened.

The app opens the **hub** at launch (Window › Hub, ⇧⌘H): every tracker in the hub's list, one per project (named by its title without the word "tracker"), with its counts; the unblocked tasks across them, grouped into the user's own, hand-offs, and the tasks Claude is running (`@owner(claude)` in progress); a Progress view with a Gantt chart and daily activity built from `@added` and `@done`; and the details of the task the user clicks, with a tick box and Open on its board. Apart from ticking and unticking (with the board's conflict check and backups) the hub only reads trackers, so keep `@owner`, `@type(session)` and `@status(inprogress)` accurate: they decide where a task appears. The user's tasks are ranked, on the boards' Next and in the hub, by what finishing them starts: one of Claude's own tasks first, then a hand-off, then anything else, then by how many items wait downstream. So gate every 💻 work package and hand-off with accurate `@needs`: a missing `@needs` ranks the task that should start it too low.

The board writes ticks, status changes, `@needs` edits and notes straight into the file, after its own conflict check, keeping backups in the `backups/` folder of your taskflow folder. Sessions see these changes on their next read. Do not undo them.

## Guardrails

- Your own global rules (CLAUDE.md) apply as always.
- Leave other sessions' materials alone: never archive, copy, move or extract another session's scratchpad files or transcript reports. Each session keeps its own records; if something of another session's seems at risk, tell the user or note it on that session's hand-off.
- Do not create, move or delete trackers, launchers or Hookmark bookmarks beyond what the effort needs. If a tracker moves, with the user's agreement, `--hub-add` its new path and `--hub-archive` the old one. Never put credentials or data values in a tracker.
