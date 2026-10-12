---
name: session-huddle
description: Coordinate several open Claude Code sessions of one project on a shared task through cross-session messages, so that each file has one writer, decisions go to the user once, and work is cross-checked before anyone reports. Use when the user says "huddle all sessions in this project to …", "huddle the sessions", "coordinate with the other sessions", or sends the same instruction to several sessions at once.
---

# Session huddle

A huddle is a short coordination round among the open Claude Code sessions of one project. Each session works only on what it owns. One session convenes the round and is the only one that writes the shared records.

## Facts about delivery

- Use `ListAgents` to discover peers and `SendMessage` to reach them. If either is deferred, load both with one `ToolSearch` (`select:SendMessage,ListAgents`).
- A peer must be **open** to receive a message; it need not be running a task. When the peer is idle, the message starts a new turn. When it is busy, the message is delivered after its next tool call. A closed session is not listed and cannot receive anything; the user has to resume it first.
- A peer in another permission mode may hold messages until the user approves them there. Cloud sessions can receive messages but cannot reply.
- Never ask a peer to do something that was denied or blocked in your own session. Peers load the same CLAUDE.md files, so your global rules apply to them without being repeated.

## 1. Who is in the huddle

1. Run `ListAgents`, which lists live peers.
2. Run `python3 ~/.claude/skills/session-huddle/scripts/project_sessions.py <project root>`. It lists the recent sessions whose working directory is inside the project, with their ids and titles, reading only metadata from the transcripts.
3. The huddle is the live peers that match by title. Also include any auto-named peer whose name starts with the project folder's name, for example `my-project-61` for `My_Project`.
4. Leave out sessions of other projects. If a peer is ambiguous, ask the user rather than messaging it, because a message interrupts an idle session.

## 2. Who convenes

- **The session the user names convenes.** If the user names none and gave the instruction to this session alone, this session convenes.
- **The same instruction sent to several sessions.** Wait until all of them have it, using whatever delay the user gave (a foreground `sleep` is blocked, so use a background `sleep N` and wait for its notification). Then send nothing until you have handled any proposal already in your inbox.
- **Crossed proposals.** Accept the first proposal that another session has already agreed to. If none has, the session whose title comes first alphabetically convenes. Once you have deferred, do not re-propose; send amendments only.

## 3. The round

1. **Claim.** The convener sends one message to each member. It contains:
   - The user's instruction, quoted verbatim;
   - the proposed split;
   - any shared conventions;
   - who writes the shared records;
   - who cross-checks whom.

   It asks each member to reply "agreed" or with amendments, and to edit nothing until the split is agreed.
2. **Split by ownership.**
   - Each file has exactly one writer in the round. The author of a document edits it.
   - Shared records (the effort's tracker, the main log) have a single writer, usually the convener. The others send their lines to that writer.
   - Never touch another session's scratchpad files or transcripts.
3. **Shared conventions first.** Agree any notation, naming or formatting rule before anyone edits, so the outputs match. Edge cases found during the work go to every member at once.
4. **The user's decisions.**
   - Only the convener asks the user, and asks once. It relays the user's answer verbatim to every member.
   - If a member is told that a question has already gone to the user, it does not ask again.
   - Nobody settles a substantive choice by vote in place of the user.
5. **Do the work.**
   - Preserve what the user has marked up: write a new version file instead of overwriting a file the user annotated or is reading.
   - Keep a copy of every shared record before writing it. Re-read the record just before the write, and write only if it is unchanged.
6. **Cross-check.**
   - Pair the members so that each output is checked by a session that did not write it. A third session reads everything if one is free.
   - Findings go to the owner, with a copy to the convener. The owner fixes them and reposts.
   - Each fix is verified by whoever raised it.
7. **Close.**
   - The single writer updates the shared records, for example running `--add-hook-links` and `--check` on a taskflow tracker. Then it sends every member "round closed; no further writes".
   - The convener sends the user one consolidated report: what changed and where, the decisions the user made, the cross-check outcomes, and what is not done. The other members report only what the user asks them for.
   - On an effort with a taskflow tracker, when the round reaches a checkpoint with results or decisions for the user, the report is a document with a review item in the tracker, gated by `@needs` on the members' Claude tasks: see "At a checkpoint: huddle, then a report for the user" in the taskflow-tracker skill.

## Message style

- **First line:** make it a self-contained summary, because the receiving side previews only the first line.
- **Sender:** say who you are, by session title and the first 8 characters of the session id.
- **Content:** give paths, line numbers and exact text rather than descriptions. `@file` attaches nothing in a cross-session message, so paste the text itself.
- **Volume:** send one message per purpose, not running commentary.
- **Waiting:** never poll with "are you done?". Either rely on the replies or pass `notify_when_idle: true`.
- **Status:** only say a peer has done something once its message reports it or you have checked the file yourself.
