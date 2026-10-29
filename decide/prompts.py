"""The decision prompts and option lists, shared by the row builder (decide/rows.py) and the live decider
(decide/decider.py). Stdlib only. Changing a string here changes the frozen test rows: rebuild nothing, ask the lead."""

# The id each decision logs with its prompt; bump it with any change to the strings below.
PROMPT_IDS = {"route": "route-v1", "duplicate": "duplicate-v1", "supersede": "supersede-v1"}
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


# Owner design (29 Oct, META-AUTO-REASONING): the one-pass pair decision also offers "ask the user" and "think it
# through", so Glimmer picks escalation itself in the same pass. The first two options are the pair options above.
AUTO4_PROMPT_IDS = {"duplicate": "duplicate-auto4-v1", "supersede": "supersede-auto4-v1"}
AUTO4_ESCALATIONS = {"3": "not sure: ask the user", "4": "not sure yet: think it through first"}


def pair_options_auto4(kind):
    return {**pair_options(kind), **AUTO4_ESCALATIONS}


def pair_prompt_auto4(kind, a, b):
    return pair_prompt(kind, a, b).replace(options_text(pair_options(kind)), options_text(pair_options_auto4(kind)))
