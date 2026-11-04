"""Follow-ups on one explanation (scratch app on 127.0.0.1:8666), then the window scrolled to the chat.
  python3 run_followups.py <label> <id> "<follow-up>" ["<follow-up>" ...]   (one Glimmer call each)
Writes <label>-followups.json and <label>-followups-chat.png beside this file.
"""
import json
import sys

from run_screen import HERE, ask, drive, BASE


def main():
    label, tid, texts = sys.argv[1], sys.argv[2], sys.argv[3:]
    out = []
    for text in texts:
        events = ask(tid, text)
        out.append({"text": text, "first_word_s": next((e["at_s"] for e in events if e["type"] == "content"), None),
                    "total_s": events[-1]["at_s"], "kinds": sorted({e["type"] for e in events}),
                    "streamed": "".join(e["text"] for e in events if e["type"] == "content"),
                    "final": next((e["text"] for e in events if e["type"] == "answer"), None),
                    "spec": next((e["spec"] for e in events if e["type"] == "spec"), None)})
        print(json.dumps(out[-1], ensure_ascii=False)[:900], flush=True)
    page = drive(f"{BASE}/explain?id={tid}", HERE / f"{label}-followups",
                 [{"wait": 1200}, {"shot": "top", "w": 420, "h": 520},
                  {"eval": "(() => { const s = document.querySelector('.ex-scroll'); s.scrollTop = s.scrollHeight; "
                           "return document.querySelectorAll('.ex-message').length; })()"},
                  {"wait": 300}, {"shot": "chat", "w": 420, "h": 520}])
    (HERE / f"{label}-followups.json").write_text(json.dumps({"id": tid, "followups": out, "page": page},
                                                              ensure_ascii=False, indent=1))
    print(page[-4:])


if __name__ == "__main__":
    main()
