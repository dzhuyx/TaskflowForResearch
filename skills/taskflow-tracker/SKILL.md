---
name: taskflow-tracker
description: Set up and maintain a high-level TaskPaper tracker for a focused research effort that runs across several Claude Code sessions, viewed and edited on the user's local taskflow board and hub. The tracker runs one loop, linked by @needs - a session writes a document with every question for the user inside it, the user reviews it, a hand-off takes the user back to the session, and the work that follows is gated on that hand-off - plus Claude-run jobs that close themselves. Use when the user asks to set up a tracker or taskflow for an effort ("use the taskflow workflow", "make a tracker for X", "add this to the tracker"); when working on an effort whose folder has a *_tracker.taskpaper; when a session produces a document for the user to review, links documents on an item, starts or finishes a task or a long-running job, stops to wait for the user, or is brought back through a hand-off.
---

# Taskflow tracker: high-level effort management across sessions

The user runs several Claude Code sessions in parallel on one effort, each on a different direction. The user manages them from one TaskPaper file per effort, the **tracker**, which the user sees on the **taskflow board** (one tracker as a dependency graph) and the **hub** (every tracker's open tasks, ranked). There the user ticks items off, opens documents, and copies hand-off prompts. The tracker is the single source of truth: every session, TaskPaper, the board and the hub edit the same file, and what the file says is all the user sees. Keep it true.

## The loop

1. A session writes a **document**. Every question or decision for the user is inside it, with its options and a recommendation.
2. The user **reviews** it and answers in comments on it. The review's checklist lists the files the user must read, and the user ticks each as the user finishes it. Ticking the review means "read and commented".
3. A **hand-off** takes the user back to the session to act on the user's comments.
4. The **work** that follows is gated on that hand-off, and its results come back as the next document.

The three kinds of item, as they appear in the file (indent with tabs; `<…>` stands for real values). Every new item starts `@status(open)`, even when its `@needs` block it: blocked or ready comes from `@needs` alone.

```
	- Review J4 cutoff report, version 2 @type(review) @owner(me) @status(open) @id(ab.12) @needs(ab.11) @sessionid(<writer's id>) @added(2026-10-12 14:05)
		file:///Users/…/j4_report_20261012_v2.pdf
		hook://file/…
		Questions: §5, Q1–Q4, each with its options and a recommendation.
		- j4_report_20261012_v2.pdf
	- Go back to <session title>: act on your comments in the J4 report @type(session) @owner(me) @status(open) @session(<session title>) @sessionid(<its id>) @id(ab.13) @needs(ab.12) @added(2026-10-12 14:05)
		Session id: <its id>
		Resume it from the VS Code session list, or in a terminal with: claude --resume <its id>
		Prompt: apply my comments in §5 of the J4 report, then start J5.
	- Run J5 on the cluster @type(work) @owner(claude) @status(open) @id(ab.14) @needs(ab.13) @sessionid(<its id>) @added(2026-10-12 14:05)
```

### Quick reference

| When | Do |
|---|---|
| You start work on the effort | Read the tracker and the newest entries of its main record (the header's "Main record" line names it). |
| You start one of your 💻 tasks | `--status <id> inprogress`. |
| You finish it | `--tick <id> --note "<one-line outcome>"` (a registered job is ticked by the sweep). |
| You post a document for the user | Questions inside it; its review item, "Review <document>", with the document linked by `--link <id> <path> --to-review`, which also lists it in the review's checklist; the hand-off after the review; tick the task that wrote it. |
| You need the user's answer to go on | The question in a document, its review, your hand-off after the review; the task that waits `@needs` the hand-off and, if it is in progress, `--status <id> waiting --note "<what you wait for>"`. |
| the user brings you back through a hand-off | Tick the hand-off if the user has not, and set your next task in progress. |
| You post a new version of a document | `--link <id> <new version> --to-review` (it goes first, and replaces the earlier version in the checklist unless the user has ticked it); the item back to a review; a dated line on what changed. |
| You start a long-running job | Add its 💻 item, then `--job-start` or `--job-run`, with `--results`. |
| You end a prompt with a task of yours unfinished | Leave none in progress unless you go on at once: set it waiting (with `@needs`) or open, with a note on where it stands. |
| After you change the tracker | `--add-hook-links` and `--check`; then one line in the Update log for what you changed in this prompt (not one per command). |

## Tools

The tools live in your taskflow folder (the repository this skill comes from). Call the script `TF="<your taskflow folder>/taskflow.py"`.

| Command | What it does |
|---|---|
| `python3 "$TF" <new.taskpaper> --init --title "Name" --prefix ab --emoji 🧬 [--purpose "..."] [--record PATH]` | Creates a tracker from `templates/tracker_template.taskpaper`, with its board launcher in `launchers/`, and adds it to the hub's list with its emoji. Refuses to overwrite. |
| `python3 "$TF" <tracker> --status <id> inprogress\|waiting\|open [--note "..."]` | Sets an item's status with the board's edit (re-read, conflict check, backup), with an optional dated note. `inprogress` also adds your `@sessionid` and `@started(YYYY-MM-DD HH:MM)` (kept until the status goes back to open or waiting; the board shows how long your work has run from it). Refuses an item that is done. |
| `python3 "$TF" <tracker> --tick <id> [--note "one-line outcome"]` | Ticks an item done, with the outcome as a dated note. |
| `python3 "$TF" <tracker> --link <id> <path> [--also] [--to-review]` | Links a document (or a results folder), given as a file path, not a `file://` URL, on an item with the board's edit (re-read, conflict check, backup), with its Hookmark line. Without `--also` it goes first, as the item's **main document**; with `--also`, after the item's other documents. A path already linked is moved, with its Hookmark line. Refuses a path that does not exist. `--to-review` also adds a step named for the file at the end of the item's checklist, as a **file for the user to review** (unless one is there), and removes an unticked step for an older version of it. |
| `python3 "$TF" <tracker> --unlink <id> <path>` | Removes a document's `file://` line and its Hookmark line from an item, and its step in the checklist unless the user has ticked it. |
| `python3 "$TF" <tracker> --add-hook-links` | Adds a Hookmark `hook://` line under every `file://` line that lacks one. It works through AppleScript and never touches the clipboard. Without Hookmark it does nothing (and says so). |
| `python3 "$TF" <tracker> --check` | Validates the tracker and prints counts, ready items and warnings. Exit code 1 means fix something. Among other things it warns about an item marked waiting none of whose `@needs` is open, which the board shows as ready; about a review that is ready for the user with no document linked; and about an open item whose Open button would open the wrong document: a folder or an audio or video file first while a document to read comes after it, or an older version first while a newer one comes after it; and about an open review that links, after its main document, another open review's main document; about an open review whose checklist names no file to review, and about a file to review, not yet ticked, that is older than a version linked on the item. |
| `python3 "$TF" <tracker> --job-start <id> --slurm <ids> [--host cluster] [--results PATH ...]` | Registers a running cluster job for 💻 item `<id>` (comma-separated SLURM job ids; `--host` defaults to `cluster`) and sets it `@status(inprogress)`. |
| `python3 "$TF" <tracker> --job-start <id> --marker <file> [--results PATH ...]` | Registers any other job: it counts as finished when the marker file exists. Use the file the job writes last (an agent review: its report). |
| `python3 "$TF" <tracker> --job-run <id> [--results PATH ...] -- <command ...>` | Runs a local command as the job and records its outcome when it exits. Start it in the background for long runs. |
| `python3 "$TF" --jobs` / `--sweep [--force]` | Lists registered jobs / checks them now. `--no-review`, added to `--job-start` or `--job-run`, skips the job's results review. |
| `python3 "$TF" <tracker> --deadline hard\|soft YYYY-MM-DD [--what "what is due"]` | Sets the project's hard (external) or soft (internal) deadline on the tracker's title line, with a backup; `""` as the date removes it. Only when the user gives the date. |
| `python3 "$TF" <tracker> --hub-add` / `--hub-archive` / `--hub-unarchive` | Adds an existing tracker to the hub's list (`trackers.json`) / hides a finished one from the hub, leaving its file alone / shows it again. Archive only when the user says the effort is finished. |
| `python3 "$TF" --hub-list` | Prints the hub's trackers with their counts. |
| `python3 "$TF" <tracker> --make-launcher` | Writes a double-click `.command` that opens the board for an existing tracker. |
| `python3 "$TF" <tracker> --open` | Starts the board on 127.0.0.1 and opens the browser. The user normally uses the Taskflow app (`~/Applications/Taskflow.app`, one window per tracker) or a launcher instead. |

There is no command to add an item: write it into the file as "Writing the file" (under Procedures) says. `README.md` in the same folder describes the board in detail. Run `python3 test_taskflow.py [tracker]` there after changing the code.

## What goes in a tracker: management level only

- **Review items**, `@type(review) @owner(me)`, titled "Review <document>": one per document the user needs to read. A review is about its document only.
  - Every question or decision for the user is written inside the document, with its options and a recommendation, and the user answers in comments on it. If the user answers in chat instead, the session writes the user's answer into the document.
  - The note says where the questions are, for example "Questions: log §7, Q3–Q10". The document is the item's main link and the first file in its checklist (see "Documents on an item").
  - Ticking a review means "read and commented".
  - Never create tracker items for individual questions, decisions or small steps, and no "Decide …" items: a decision is a question in a document. (This retires the decision items of 11 October; the old ones stay as history.)
- **Session hand-offs**, `@type(session) @owner(me) @session(title) @sessionid(<id>)`: what follows a review, titled "Go back to <session>: act on your comments in <document>". One per return to a session.
  - It is gated by `@needs` on the review it follows; a hand-off may need several reviews. A review that no session acts on (a document for information only) needs no hand-off.
  - The note contains the session id; `Resume it from the VS Code session list, or in a terminal with: claude --resume <id>`; and a line starting `Prompt:` with a draft instruction for the user to paste.
- **A task held up by one of the user's decisions** `@needs` the hand-off that follows the review of the document holding the question. If there is no such document, review or hand-off, add them. If the user answers in chat and the session can act at once, no item is needed: the session records the user's answer in the effort's log, and in the document if it holds the question.
- **Jobs and work packages**, `@type(work)`: one per coherent piece of work.
  - `@owner(claude)` marks work a Claude session runs on its own: cluster simulations, long local checks, agent reviews. It shows as 💻.
  - A work package that a session runs after a review is gated on the hand-off that follows that review. Its results come back as a document, its review, and the next hand-off.
  - The user's own work packages are `@owner(me)`.
- **Closing the loop.** Every 💻 job gets a results review: a 🧑 review item with `@needs` on the job, linking the results. The next hand-off is gated on that review, not on the raw job.
- **Checkpoint reports**: at intermediate steps, not only when work ends, Claude's results come to the user as a report document for the user to review, discuss and decide on. Each report is one 🧑 review item with `@needs` on every 💻 task whose results it reports, so the board draws arrows from those tasks to it, and `@sessionid` of every session that contributed. A hand-off back to each session that acts on it follows the review. Several Claude tasks, from one session or several, may feed one shared report; separate topics get separate reports. The related open sessions huddle first (the `session-huddle` skill). See "At a checkpoint: huddle, then a report for the user".
- **Documents Claude is revising are Claude's tasks, not the user's reviews**. A document may be sent back after the user's review, or Claude may rewrite it over a long session. Either way, its item is `@type(work) @owner(claude) @status(inprogress)` with the writer's `@session` and `@sessionid`, for as long as Claude is revising it. It is never left as an open 🧑 review that the user cannot act on.
  - When the revision is posted, the same item goes back to the user: `@type(review) @owner(me) @status(open)`, with the new version linked first and a dated line saying what changed.
  - Keep one item per document; it alternates between a review and Claude's task. See "When a document goes back for revision".
- **Automatic completion.** A registered job is ticked automatically. Claude Code hooks (`Stop` and `SessionStart` in `~/.claude/settings.json`) run `taskflow.py --sweep-detached` whenever any session starts or finishes a reply; the open board sweeps every minute. Sweeps run at most every 2 minutes, and each SLURM job is checked over `ssh cluster` at most every 10 minutes.
  - When a job finishes, the sweep ticks it `@done(date)` with a "Finished automatically" note, adds the results review under "Review — results of Claude jobs" with the next free id (unless a review already needs the job), links the `--results` paths, adds Hookmark links, and appends to the Update log.
  - When a SLURM job ends in any state other than COMPLETED, or a `--job-run` command exits non-zero or is killed, the item gets a note and `@status(waiting)` instead.
  - If the cluster can't be reached (the ssh master connection has lapsed), the job is kept and checked again later. Marker jobs never fail on their own; if one is abandoned, say so on the item and tick or reopen it by hand.
  - The log is `jobs/sweep.log` in your taskflow folder.

## Tags and rules

- `@id(<prefix>.N)` on every item. Ids are unique and never reused; record retired ids in the Update log.
- `@sessionid(<full session id>[, <id> ...])` on every item that comes from a Claude Code session. Use the full 36-character id, which never changes, unlike the title.
  - **Which session** depends on the item: a review, the session that wrote the document; a hand-off, the session to go back to; a job or work package, the session that runs it; the user's own work package, the session that proposed it. Several sessions are allowed, for example on a shared log.
  - **Your own id:** `echo $CLAUDE_CODE_SESSION_ID`. The folder name in your scratchpad path, `/private/tmp/claude-*/<project>/<session-id>/scratchpad`, gives the same id.
  - **Forks share their parent's history.** Work done before a fork belongs to the parent session. To attribute old work, look for the session id inside scratchpad or task paths in the transcript's tool calls, not for which transcript contains it.
  - Registered jobs get the tag automatically, and so do their results reviews. Items the user adds need no `@sessionid`.
  - On the board, `--check` and the panel show each session's current title, read from its transcript. The panel copies the resume command (`cd "<project>" && claude --resume <id>`), and a menu filters by session.
- `@session(<title>)` on a hand-off names the session by its title. **Your own title:** `ListAgents` prints "This session is <title>". If the session is unnamed, or `ListAgents` is not available, ask the user to `/rename` it, or use `unnamed <first 8 characters of the id>`.
- `@needs(ab.3, ab.7)` lists the items that must be done first. The board draws arrows from them and marks the item ready or blocked.
  - **Ready or blocked comes from `@needs` alone**. `@status(waiting)` does not block: the board and the hub show a waiting item as ready once all its `@needs` are done. So every task that waits for something, including another session's task and the blocked part of a running work package, carries `@needs` on the item it waits for. A note saying what it waits for is not enough. `--check` warns about a waiting item none of whose needs is open.
  - **`@needs` also ranks the user's work.** On the boards' Next and in the hub, the user's tasks are ranked by `@priority`; then by how much finishing each frees Claude to work (each of Claude's tasks waiting on it counts 1, shared among everything that task still waits for, and a hand-off half that); then by the project's deadlines; then by what else finishing it starts. A missing `@needs` on a 💻 task or a hand-off ranks the item that should start it too low.
- Status is one of `@status(open)`, `@status(inprogress)` or `@status(waiting)`. When finished, tick it with `--tick <id> --note "<outcome>"`, which writes `@done(YYYY-MM-DD HH:MM)` and the outcome as a dated note. Never delete an item.
- **Documents on an item**. Each is two lines in the item's note: the `file://` URL, alone on its line (spaces as `%20`, `+` left as is), then its `hook://` URL. Link them with `--link`, not by hand, so that they stay complete and in order.
  - **The first linked document is the item's main one.** The hub's and the board's **Open** button opens it, and so does double-clicking its box on the board, unless the checklist names files to review (next point). When an item links two or more, **All N** beside the button lists every one in link order, the main one first and marked.
  - **Files to review**. Every review's checklist lists the linked files the user must read, one step per file, named exactly as the board lists it (its file name), in the order the user should read them; the user ticks each when the user has finished it. Add them with `--link <id> <path> --to-review`: the main document first, then any other linked file the user must read (an appendix, an agent review the user is asked to read). Files the user may only consult (earlier versions, an audio summary, the results behind the document) are linked with `--also` and no step.
    - When the checklist names files to review, the **Open** button opens the first one not yet ticked, and **All N** lists just those, in checklist order, marking the next and the ones the user has reviewed. The item's other links stay in the panel's Documents row.
    - A step names a file when its text is the file's name, alone or followed by a space and more words. The user can add one by typing the file name as a step.
    - A new version that the user must read: `--link <id> <new version> --to-review` replaces the unticked step for the earlier version; a version the user has ticked stays as a record.
  - **The main document is the one to read now, in its current version:** `--link <id> <path>` puts it first. A results folder or an audio file is never the main document while a document to read is linked.
  - **Only closely related files come after it**, linked with `--also`. The test: the user would open the file only because the user is reviewing the main document. That allows:
    1. earlier versions of the same document, which keep the user's annotations;
    2. other forms of the same document: an audio summary, a figure, an appendix;
    3. material about the document that the user may check while reviewing it: its agent reviews, its cross-check, the results folder behind it.
  - **A separate document is never linked after another's:** one that needs its own reading or carries its own questions gets its own review item. The main document cites it, and `@needs` sets the order of the reviews.
  - **A new version** of the main document: `--link <id> <new version>`, which puts it first. Keep the earlier versions linked after it (the user's annotations live in them), and retitle the item to name the version linked first.
  - **Complete:** link every file about the main document that the user should open, including its agent reviews and figures named in the item's notes. A file named only for the record (a script, a build file) needs no link.
  - **Remove** a link that no longer belongs to the item with `--unlink <id> <path>`: one added by mistake, or a file the item no longer covers (for example a results folder of a job whose results now have their own review). Results that the main document reports on are supporting files: keep them, after it.
  - The board and the hub list each document by file name and open it through Hookmark. `--check` warns when an open item's first document is a folder or an audio or video file while a document to read comes after it, or is older than a version linked after it; and when an open review links, after its main document, the main document of another open review.
- **Checklists inside a task**: when the user has several steps to work through for one task, write them as indented `- ` lines under the task (TaskPaper sub-tasks), one step per line, with no `@id` and no other tags; a step that is already done gets `@done(YYYY-MM-DD HH:MM)`. They are not tracker items: the board and hub show them as a tickable checklist with its progress ("2/5") on the card, and the user can add or remove steps in the board's panel or the hub's details. Never end a step with `:` (TaskPaper would read a project). No separate tracker items for small steps. A step named for a linked file is a file to review (see "Documents on an item"); add those with `--link … --to-review`.
- Other tags: `@priority(high)` or `@priority(low)`, and `@added(YYYY-MM-DD HH:MM)` on every item you add, with the time from `date '+%Y-%m-%d %H:%M'` (the hub's Gantt chart draws each item from `@added` to `@done`). Older items carry dates only; leave them. Never guess a missing `@added`: add it only if you know when the item was added. A tracker's own header may still show the date-only form; follow this skill.
- **Project deadlines** are tags on the tracker's title line only: `@harddeadline(YYYY-MM-DD, what is due)`, an external, real deadline, and `@softdeadline(...)`, an internal one that may slip a few days but should be kept. Read them before planning, and tell the user when work on the tracker puts one at risk. Set them only with dates the user gives, never a guessed date, with `--deadline` or the user's hub; never put them on an item.
- **Board emoji:** 🧑 for `@owner(me)`, 🧑‍💻 for `@type(session)`, 💻 for `@owner(claude)`.
- **TaskPaper syntax traps:**
  - Tag values cannot contain parentheses; write `[fork]`, not `(fork)`.
  - A note line must not end with `:`, or TaskPaper turns it into a project.
  - Do not start a note line with `- `, or it becomes a task.
  - Indent with tabs only.
- **Where items go:** under the tracker's section for their kind (Review, Session hand-offs, Work packages, as its header lists them); the sweep puts results reviews under "Review — results of Claude jobs".
- **Dated notes** are note lines that start with the date and time, for example `2026-10-12 16:00: version 2 posted; it adds §6.` The `--note` of `--status` and `--tick` writes one for you.
- **Update log:** at the end of each prompt in which you changed the file, append one line saying who changed what, ending "Highest @id used: ab.N.", for example `2026-10-12 15:20: Session 11111111 (sims): added ab.21–ab.23 (the report's review, its hand-off, scenario C); ticked ab.7. Highest @id used: ab.23.`

## Procedures

### Start a tracker for a new effort

1. Agree with the user on the effort's name, the id prefix, and where the tracker lives. The default is the effort's plan or log folder, named `<effort>_tracker.taskpaper`.
2. Run `--init`, giving `--record` as the effort's main log or plan, and `--emoji` with one emoji that fits the effort's topic and that no other project in the hub has (`--hub-list` shows theirs). It also adds the tracker to the hub; a tracker made any other way needs `--hub-add`, which takes `--emoji` too. Left out, the hub gives the first unused emoji of its picker; the user can change it in the hub.
3. Seed the review items, hand-offs and work packages with `@needs`, link their documents with `--link`, then run `--add-hook-links` and `--check`.
4. Add a pointer to the tracker near the top of the effort's main log.
5. Note in the project's CLAUDE.md (or Claude Code's memory) that the tracker exists and must be read first.
6. Tell the user where the launcher is: its path is printed by `--init` and written into the tracker header.

### Every session working on an effort with a tracker

- **At the start:** read the tracker and the newest entries of the main record.
- **Every item you add** carries `@id`, `@type`, `@owner`, `@added`, accurate `@needs`, and `@sessionid($CLAUDE_CODE_SESSION_ID)`, or the id of the session it comes from if that is another session.
- **Your own 💻 tasks (`@owner(claude)`) carry their real status, always**. The user's hub lists the tasks Claude is running, meaning yours marked in progress, with when their session last did anything. A task you work on without marking it in progress is invisible to the user.
  - **When you start working on one**, run `--status <id> inprogress`, which also adds your `@sessionid`. Do this for work you do in the conversation too, not only for registered jobs.
  - **When you end a prompt before it is finished** and will not go on at once: if you wait for the user, follow "When you need the user's answer" and give the task `@needs` on your hand-off; if you wait for another task (another session's, a job), give it `@needs` on that task. Then run `--status <id> waiting --note "<what you wait for>"`. If you set it aside, run `--status <id> open`.
  - **When it is finished**, run `--tick <id> --note "<one-line outcome>"`, unless it is a registered job, which the sweep ticks.
  - Never leave a task in progress when you stop: the hub flags one whose session has been quiet for an hour and that has no registered job.
- **When you produce a document for the user** (a checkpoint report, which gathers several tasks' results or a result that changes what comes next, follows "At a checkpoint" below instead, which adds a huddle when other open sessions have related tasks):
  1. Put every question for the user inside the document, each with its options and your recommendation.
  2. Link it on its review item, "Review <document>" (a short name for the document and its version, for example "Review simulation report, version 1"), with `--link <id> <path> --to-review`: add the review item, or use the one that already exists for it (a report's review created early). A review that is ready for the user must link its document. Add any other file the user must read with `--link <id> <path> --also --to-review`, and files the user may only consult with `--also` alone, so that the review's checklist lists exactly the files to review.
  3. Add the hand-off that follows the review, back to you or to the session that will act on the user's comments, and gate the work that follows on it.
  4. Tick the task that wrote the document.
- **When you need the user's answer to go on:** put the question in a document, with its options and your recommendation, and give that document its review item. Add a hand-off for yourself, "Go back to <your title>: act on your comments in <document>", gated on that review, and give the waiting work `@needs` on the hand-off. Give all of them `@sessionid` with your id. If the user answers in chat and you can act at once, no item is needed: record the user's answer in the effort's log, and in the document if it holds the question.
- **When the user brings you back through a hand-off** (the user pastes its prompt, or gives you the instruction it names): if it is not ticked yet, tick it with `--tick <id> --note "<what the user asked>"`, then set the task you start in progress. Record the user's answers to the document's questions in the document, or in the effort's log if the user gave them in chat. The review it follows stays the user's to tick.
- **When a document goes back for revision**: this applies when the user asks for a new version, or the user's comments send a document back for rewriting, and the revision will take more than a short edit.
  - **Before starting**, turn its review item into your task. Edit the item line to `@type(work) @owner(claude) @status(inprogress)` with your `@session(title)` and `@sessionid`, and retitle it "Revise <document> to version N: <goal>". `--status` cannot change `@type` or `@owner`: edit the line as "Writing the file" says.
  - **Add a dated note** giving the scope and naming the version the user annotated. Never overwrite that annotated version; the new version is a new file.
  - **Dependent documents:** do the same for any document that must be revised after this one, and gate it with `@needs` on this item.
  - **Work that should wait for the new version** (for example a task gated on the hand-off that brought you back for this revision, now ticked): give it `@needs` on this item, so it does not show as ready.
  - **When the new version is posted**, give the item back to the user. Edit the line to `@type(review) @owner(me) @status(open)` and retitle it "Review <document>, version N". Drop `@started` (an open item carries none). Link the new version with `--link <id> <path> --to-review`, which puts it first and puts it in the checklist in place of the earlier version the user has not ticked, so the Open button opens it; the earlier versions stay linked after it. Add a dated line saying what changed and what is not yet cross-checked. Then add the hand-off that follows the review and move onto it the `@needs` of the work that waits.
- **At a checkpoint: huddle, then a report for the user**.
  - **When:** a result changes what should happen next (a choice between directions, a surprise, an assumption that failed); several Claude tasks have results on the same question; a phase of a long work package is finished; or the next step is expensive or hard to undo (long cluster runs, rewriting a manuscript section). Not for routine progress: no report only to say that work goes on.
  - **Huddle the sessions** with the `session-huddle` skill. The sessions in the huddle are the open sessions whose 💻 tasks in this tracker bear on the topic: read the tracker for those tasks and their `@sessionid`. The session that reaches the checkpoint convenes, unless the user names another. In the claim, propose the reports: which topics, one document per topic or one shared document, and who writes each. Each document has one writer, as the huddle skill requires; the other members send that writer their material (results, tables, file paths, pasted text), and the cross-check pairs read each report before it goes to the user. If no other open session has related tasks, write the report alone.
  - **Sessions that are not open** cannot be huddled: use only what they have posted (documents and result files in the effort's folders, their tracker items and notes), never their scratchpads or transcripts, and say in the report what is missing from them. The user can resume them for the next round.
  - **What a report holds:** what was done and by which task; the results, as tables and figures; what they mean and what limits them; the cross-check outcome; and a numbered **Decisions** section, each decision with its options, the recommendation, and what each option would start next. The user answers in the user's comments in the report. This section is the huddle's one question to the user: nobody asks the user separately, and nobody settles a decision in the user's place.
  - **In the tracker** (the convener is its single writer during the round; members send it their lines):
    1. Close the phase each report covers. If the work was one 💻 task, edit its title to name the phase it finished and tick it with `--tick <id> --note "<outcome>"`; add a new 💻 task for what follows. Plan long work as phases from the start, so a checkpoint ticks one.
    2. Add one review item per report: `@type(review) @owner(me)`, titled "Review checkpoint report: <topic>", with `@needs` on each 💻 task it reports, so the board draws arrows from them to it, and `@sessionid` of every session that contributed. Link the report with `--link … --to-review`; the note says where the decisions are, for example "Decisions: §5, D1–D4".
       - **A review is never ready without its document**. If you add the review before the report is written, for example so that the sweep adds no per-job results reviews, also add a 💻 task that writes the report (its writer's `@sessionid`; in progress while it is drafted and cross-checked), and give the review `@needs` on it. When the report is posted, link it on the review with `--link … --to-review` and tick the writing task.
    3. Add a hand-off back to each member session that will act on the report, gated on its review, and gate on those hand-offs what depends on the user's decisions: the next 💻 tasks, including tasks that already exist and wait for the same decision, in any session (look for waiting items whose notes name it), and the blocked part of a work package that is otherwise running: split that part into its own task. Work that goes on whatever the user decides is not gated. Then run `--check`, which warns about a waiting item with no open need.
    4. Once the user has finished a review, new results go in a new version or a new report (see "When a document goes back for revision").
  - **Close the round** as the huddle skill says, and send the user one message naming each report, its review item's id, and the decisions it asks for.
- **When you start a long-running job:** add its 💻 item (or use the work package's own item, if the job is the whole package), then register it with `--job-start` (`--slurm` for a cluster job, `--marker` for anything else) or run it through `--job-run`. Pass `--results` for each output that the user should open; the results review lists each as a file to review.
- **When the job finishes:** the sweep ticks it and adds the results review: the item that `@needs` the job, titled "Review the results of <job id>: …", which the sweep's Update log line also names. Do not tick a registered job by hand; to record a finished job at once rather than at the next sweep, run `--sweep --force`. If you write a report on the results, link it first on that review with `--link … --to-review`, and retitle the review "Review <report>". If the user then need not read the raw results, take them off the checklist: `--unlink <id> <path>`, then `--link <id> <path> --also`, which links them again after the report with no step. A job that cannot be registered is ticked by the session, which also adds the results review.
- **Writing the file:**
  - For status changes, ticks and document links, use `--status`, `--tick`, `--link` (with `--to-review` for the files the user must read) and `--unlink` rather than editing lines yourself: they re-read the file, check it did not change meanwhile, keep a backup, and keep `@started` consistent with the status.
  - If you must write a status by hand, for example on an item you add for another session (`--status inprogress` would stamp your own `@sessionid`), keep the same rule: an item in progress carries `@started(YYYY-MM-DD HH:MM)` with the time it started, and an item set to waiting or open carries none.
  - For everything else (adding items, editing an item line), re-read the file immediately before writing, and write only if it is unchanged since you read it, because other sessions edit it concurrently. Keep a copy of the previous version in your scratchpad.
  - After writing, run `--add-hook-links` and `--check`, and append your line to the Update log.
  - **A typical order**, posting a document: re-read and write the new items by hand; `--link … --to-review` the document; `--tick` the task that wrote it; `--add-hook-links`; `--check`; your Update log line.
- **In chat:** report what you added or changed, with the item ids.

### When the user works from the board and the hub

The user opens trackers in the Taskflow app, usually from the tracker header's **Open board** link: a `taskflow:///<path>` link (spaces as `%20`), made by `app_url()` in `taskflow.py` and written by `--init`. File › Open, Open Recent and Finder › Open With › Taskflow also work; each window runs its own `taskflow.py --app` server, with logs in `~/Library/Logs/Taskflow/`. After changing `macapp/Sources/`, rebuild with `macapp/build_app.sh --install`; changes to `taskflow.py` or `static/` take effect when a window is reopened.

The app opens the **hub** at launch (Window › Hub, ⇧⌘H): every tracker in the hub's list, one per project (named by its title without the word "tracker"), with its counts; the unblocked tasks across them, grouped into the user's own, hand-offs, and the tasks Claude is running (`@owner(claude)` in progress), ranked as "Tags and rules" says under `@needs`; a Progress view with a Gantt chart and daily activity built from `@added` and `@done`; and the details of the task the user clicks, with a tick box, its Open button and **All N** list of documents, and Open on its board. Apart from ticking and unticking (with the board's conflict check and backups) the hub only reads trackers, so keep `@owner`, `@type(session)` and `@status(inprogress)` accurate: they decide where a task appears.

The board and the hub write ticks, status changes, `@needs` edits, checklist steps and notes straight into the file, after its own conflict check, keeping backups in the `backups/` folder of your taskflow folder. Sessions see these changes on their next read. Do not undo them.

## Guardrails

- Your own global rules (CLAUDE.md) apply as always.
- Leave other sessions' materials alone: never archive, copy, move or extract another session's scratchpad files or transcript reports. Each session keeps its own records; if something of another session's seems at risk, tell the user or note it on that session's hand-off.
- Do not create, move or delete trackers, launchers or Hookmark bookmarks beyond what the effort needs. If a tracker moves, with the user's agreement, `--hub-add` its new path and `--hub-archive` the old one. Never put credentials or data values in a tracker.
