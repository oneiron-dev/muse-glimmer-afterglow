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
