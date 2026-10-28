"""The decision prompts and option lists, shared by the row builder (decide/rows.py) and the live decider
(decide/decider.py). Stdlib only. Changing a string here changes the frozen test rows: rebuild nothing, ask the lead."""

ROUTE_ASK = "none of these notes answers it (ask {first})"
ROUTE_TASK = ("Which note answers this question? Reply with the note's number only. "
              "Reply 0 if none of these notes answers it.")
DUP_OPTIONS = {"1": "the same fact, said twice (keep one)", "2": "different facts (keep both)"}
SUP_OPTIONS = {"1": "the new note replaces the earlier one (a correction)", "2": "both notes stay true (keep both)"}


def note_line(item):
    return f"{item['key']}: {item.get('full_value') or item['value']}"


def options_text(options):
    return "\n".join(f"{k} = {v}" for k, v in options.items())


def route_prompt(first, items, question):
    notes = "\n".join(f"{n}. {note_line(it)}" for n, it in enumerate(items, 1))
    return (f"Notes I keep about {first}:\n{notes}\n0. {ROUTE_ASK.format(first=first)}\n\n"
            f"{first} asks: {question}\n\n{ROUTE_TASK}")


def route_options(first, items):
    return {"0": ROUTE_ASK.format(first=first), **{str(n): note_line(it) for n, it in enumerate(items, 1)}}


def pair_prompt(kind, a, b):
    if kind == "duplicate":
        return (f"Two notes from the same day:\nNote A: {a}\nNote B: {b}\n\nAre they the same fact?\n"
                f"{options_text(DUP_OPTIONS)}\nReply with the number only.")
    return (f"An earlier note and a new note:\nEarlier: {a}\nNew: {b}\n\nDoes the new note replace the earlier one?\n"
            f"{options_text(SUP_OPTIONS)}\nReply with the number only.")


def pair_options(kind):
    return DUP_OPTIONS if kind == "duplicate" else SUP_OPTIONS
