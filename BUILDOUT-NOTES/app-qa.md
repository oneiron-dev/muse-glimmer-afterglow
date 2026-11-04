# App QA before the recording (session "Afterglow loom", Wed 4 Nov 16:00 to Thu 05:00)

What a viewer would see, found on scratch copies only (never the real runs/, never ports 8650-8653), and what was fixed.
Screenshots are headless Chrome (drive_page.mjs). Live model calls: mel slot 1, a few short turns.

## 1. Loom end to end (PR ui/loom-versions)

Checked on scratch port 8667, a fresh copy of runs-8661 (Mira day 1): send a second message, edit and resend it,
ask again on the edited reply, step through the versions, the question before tonight, end the day with a test-only
rollback night (receipt says no training ran), open day 2, answer the morning offer.

Found and fixed:
- Ask again could not be stepped from the reply. The stepper under your message showed "2/2" with the same words
  twice, and the first wording needed two steps back. Now loom.versions gives two steppers per turn: other wordings
  under your message (an edit is one step away at any depth) and the answers to the same words under the reply.
  Stepping to another wording opens the answer you were on last. Steppers stop at the ends instead of wrapping.
- The question before tonight said "branch", "line" and "main". It now says "You told me two different things today.
  Tonight I learn the one you pick." with "In the version of today you are on" and "In your other version", and says
  what happens with no answer. The duplicated "Before the night" label is gone.
- Saving the answer made the card vanish with no confirmation. It now leaves one quiet line ("Saved. Tonight I learn
  Design review time: Thursday at 15:00.") with Change.
- The morning offer was a card inside the morning card, above the verdict, and named versions "Branch b2". It is now
  a section under the verdict (like What I propose), names each version by what you said on it, and asks about a fact
  only when it had no answer before the night. Its words fit a rolled-back night too ("trained on", not "learned").
- "You keep night ?'s update" when no night was ever kept: now "Your agent stays as it was."
- After the night, the bar read "Tomorrow" with today's date: it shows the next date.
- A day whose own night has run showed that night's card at its top as "Morning, day 1": the morning card now shows
  only a night before the open day.
- Loom error messages said "fork", "branch" and "loom": rewritten in plain words.
- "‹ Back to the first ›" under an edited day: now "Back to your first version".

Checks: 451 Python tests twice, the prompt fingerprint test, 10 loom UI tests (bun test src/loom/loom.test.jsx),
the Vite build. Live on 8667: three short turns on mel, both steppers, back to the first version, the night with no
answer, the morning answer saved for day 1 with day 2 open (day 2 has no answer file).

## 2. Shot S8: ?fact=F21 (PR ui/fact-link)

The loom merge (#188) dropped the line that reads ?fact= in the morning proof, so http://127.0.0.1:8650/?fact=F21
opened on "1 of 52, workshop town" instead of "3 of 52, arm hinge bolt torque". Restored, with a UI test
(loom.test.jsx). Checked on the scratch Ines copy (port 8669, day 10): 3 of 52, Before "45 ft-lbs", Now
"2.8 newton-metres". S7 (the morning card for night 9) is unchanged by #192: it still shows night 9.

## 3. Shot walk and every screen (PR ui/qa-walk)

Walked on the scratch Ines copy (port 8669; day 10, scratch day 4 for S10, a scratch day 12 for S4) and Mira (8667):
S2, S3, S4 (one live turn: "I don't have an accounts software recorded in your notes..."), S7, S8, S10, the voice
picker; then Chat, Memory (Notes, Days), Nights, Calendar, Mail, Tasks, Files, the side panel and every Settings
section, in a light and a dark sky, with the sidebar open and folded. Console clean on every page.

Found and fixed:
- S10 (and every rolled-back morning): the line "You still talk to night 9's update." was centered, because the
  Models page's "Keep loaded" switch reused the class name .mkeep globally. Renamed to .mloaded.
- S10: a day-4 morning said "You still talk to night 9's update" (today's latest). It now names the update kept
  before that night ("night 1"), and says nothing about an update when none was kept yet.
- S3: the corrected office note said "The model still holds 'room 2.14 second floor' until a night trains this",
  though night 9 was kept after the change and answers it 3 of 3. A changed note now says "Trained in night 9" once
  a kept night came after the change.
- One name per thing: the side panel tab said "Email", the sidebar "Mail". Now "Mail". Empty states said "the agent":
  now "your agent".
- Settings > Voice said "A voice steers the model directly (re:dvx dials)" and Default said "Glimmer as it is: no
  dials." Now "How your agent sounds, from your next message." and "Your agent as it is."
- The voice picker's text box cut its placeholder ("Describe a voice in one sentenc"): now "Describe a voice".
- Files: the empty state showed the tasks tick-box icon. It shows a file icon.
- Settings > Dictation: the heading "Try it" sat over a button "Try it". The button says "Record".

Seen, not changed (data or out of scope):
- Day 10 (real and scratch) turn 2 says "gullwing strut code KF-GW-542": keep it out of the S7 frame (Afterglow UI
  added a framing rule to the checklist).
- The scratch 8660 copy has extra test turns on day 10 (a thumbs-up emoji, larkspur-brief.md); the real day may not.
- Settings > Models once showed "Not answering at 100.88.57.107:18610" in one load, then green on the next: a slow
  health probe while mel was busy. The address itself is shown there by design.
- On load the thread scrolls to the latest message; the morning card is a scroll up (S7).

## 4. The notes follow the line; a sealed day keeps its line (PR ui/loom-notes, Sol review of main, findings 2 and 5)

- Picking a version did not pick its notes: remember() supersedes globally, so after an edit on b2 (Room 318) and a
  switch back to main, notes.json still had 318 active and 312 superseded, and night/consolidate.py trained 318.
  Now loom.line_notes gives the notes as one line has them: a note said today on a turn off the line gets status
  "other_version" (kept, so switching back restores it), and the note it had replaced is active again, or superseded
  by the line's next note (supersedes pointers are walked through set-aside notes). DaySession.fork and .switch
  write that state to notes.json, so recall, Memory and every night reader of notes.json see the line.
  consolidate.load_day also reads notes through loom_line.day_line_notes (the resolved line, other versions removed).
  A day without forks is untouched: line_notes returns the same list, and a test checks identity on every unforked
  recorded day.
- POST /api/branch/switch on a sealed day (its night has run) now answers 409 with a plain sentence; the line and
  loom-resolution.json stay as the night trained them.
- Tests: app LineNotesTest (the 312/318 repro both ways, a correction on main after a set-aside note, no rewrite
  without forks), the sealed switch, night test_loom_line (consolidate.load_day repro, identity on recorded days).

## 5. The loom's words say what the night does (PR ui/loom-honest)

The night reads only the line in loom-resolution.json (Meta lead, 16:1x): keep_side and add_branches are saved
but nothing trains on them before the 05:00 restart. The words promised more ("Tonight I learn Design review time:
Thursday at 15:00", "Tick it and I learn it too", "A later night learns what you picked").
- The question before tonight now picks a version, which is the thing the night trains: "The version of today you
  are on" or "You said “...”" with the facts each one says. Saving another version moves today to it (switch, then
  resolve) and says so ("Saving moves today to that version."). Saved: "Saved. Tonight's training uses the version
  you picked." No tick boxes for facts said only on another version.
- The morning offer says "Last night's training used the version of day N you ended on. If your other version was
  right, mark it here." and, saved, "Your pick is saved. Last night's result stays as it was." After an answer it
  offers a version only for facts that disagree with nothing (the other side of an answered conflict is not offered).
- Other versions are named by what you said on them differently (loom.version_words, GET /api/loom version_words,
  replacing first_words), so your first version reads "You said “The design review is on Thursday at 14:00.”".
- Checked live on 8667 (Mira, the forked day): picking the 15:00 version moved the day to it, set 14:00 aside in
  notes.json (other_version) and saved the answer on that line; the day-2 morning offer, unanswered and answered.

## 6. Raw and Continue read the shown version (PR ui/raw-turn-line, Sol QA of main #14)

App.raw_turn built the prompt from every day-log record up to the turn, so after an edit it showed (and Run used)
the discarded first wording and its answer before the replacement. It now walks loom.line_records for the line,
as DaySession._rebuild does. Test: RawTurnTest (an edited turn's raw prompt has only the edited words).

## 7. Thread and composer findings from the Sol QA of main (Afterglow UI's five)

- #14, PR #207: section 6.
- #8, PR #209: a rejected fetch or SSE error left Answering stuck; a handled 400/409 cleared the draft and the
  error. send() runs in try/catch/finally, the error stays under the thread (and on the empty-day screen), and the
  message box puts your words and files back. Checked headless with the server stopped.
- #4, PR #210: attachment ids restart at A001 each day, but the message box kept its chips across a day switch and
  the Files/Copied-today queue was unscoped. The box is keyed by person and day, queued files carry their day, and
  /api/chat refuses a message written for another day with a plain 409. Tests ChatScopeTest, lib.test.js.
- #12, PR #215: Edit and resend sent only the typed words, dropping the turn's file. /api/branch takes the
  attachment ids; Ask again keeps typed and attachments. Test EditKeepsFilesTest.
- #3, PR #216: after End the day, the box stayed open and wrote into the day just ended. It closes until the next
  day opens, the night card says when chat opens again, and with no result yet the app stays at night (no
  "Tomorrow" with an empty morning). No server-side sealed-chat guard: a recorded day whose night already ran
  (day 10 on the scratch copy) would refuse chat. Test Composer.test.jsx.

## 8. Re-walk on main after the fixes (17:0x, local build of main 5d6cc5d + #215 + #216)

- Loom on a fresh Mira copy (port 8667, three live turns on mel): edit, ask again, both steppers ("Versions of
  your message 2/2", "Answers to this message 2/2"); the question with "You said ...14:00" picked moved the day to
  main, notes.json had 14:00 active and both 15:00 notes other_version; Save showed "Saved. Tonight's training uses
  the version you picked."; End the day closed the box ("This day has ended. Open the next day to keep talking");
  the day-2 morning offer stayed quiet (answered); a switch on the sealed day 1 answered 409.
- Ines copy (port 8669): S7 night 9 card; S8 ?fact=F21 = 3 of 52, 45 ft-lbs then 2.8 newton-metres; S3 office
  note "Trained in night 9, answers it without notes 3 of 3 times"; S10 day 4 "You still talk to night 1's update".
- S2 risk: Memory > Notes search "torque" also lists "torque audit file: DX-47B" (F69, code-shaped). The S2 row in
  SHOT-MAP-APP.md now says search "bolt torque", which shows only the hinge bolt note.
