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
