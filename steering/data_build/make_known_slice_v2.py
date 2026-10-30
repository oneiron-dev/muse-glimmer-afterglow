"""Build eval/data/known_slice_v2.jsonl: a second 100 NQ-open validation questions by known v1's rule (same filters, same
form), picked in sha256('known-slice-v2:<row>') order and disjoint from known v1, the public anchor pool v1, the SimpleQA
gate slice (any shared 3-word run) and forget v1/v2 (any shared 5-word run); plus its manifest. Frozen before any answer.
Run from the worktree root: python3 -I <this> <nq jsonl> <parquet sha256> <public_anchor_pool_v1.jsonl>"""
import hashlib, json, re, sys
from pathlib import Path
src, parquet_sha, pool_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
rows = [json.loads(l) for l in open(src)]
people = set()
for f in list(Path("eval/data/never_told").glob("*.jsonl")) + list(Path("eval/data/never_told_rewrites").glob("*.jsonl")):
    for r in map(json.loads, f.read_text().splitlines()):
        if r.get("spec", {}).get("person"):
            people.update(w.lower() for w in r["spec"]["person"].split())
for w in Path("worlds").iterdir():
    for f in w.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        name = (d.get("persona") or {}).get("name") if isinstance(d, dict) else None
        if name:
            people.update(x.lower() for x in name.split())
TIME = re.compile(r"\b(current|currently|latest|now|today|this year|recent|recently|new|newest|next|upcoming|last time|"
                  r"20(1[5-9]|2\d))\b", re.I)
HEALTH = re.compile(r"\b(health|disease|diseases|alzheimer'?s?|cancer|diabetes|diet|dieting|vitamins?|calories?|medical|"
                    r"medicine|medicines|drugs?|virus|viruses|infection|symptoms?|doctor|doctors|hospital|surgery|pregnan\w*|"
                    r"mental|depression|anxiety|fitness|exercise|weight|obesity|blood|heart|brain|organ|organs|patients?|"
                    r"therapy|syndrome|disorder|vaccine|nutrition|mood|suicide|addiction|alcohol|smoking|skin|injections?|"
                    r"subcutaneous|mouth|teeth|tooth|bones?|muscles?|lungs?|liver|kidneys?|pain|sleep|eat|eating|food|venom)\b",
                    re.I)
CHANGES = re.compile(r"\b(most|owner|owns|who are|who is the cast|record|records|retirement|ranked|ranking|champions?|"
                     r"leader|members|present|president|prime minister|speaker|cast of|bands in)\b", re.I)


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def runs(text, n):
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


v1 = [json.loads(l) for l in Path("eval/data/known_slice_v1.jsonl").read_text().splitlines() if l.strip()]
pool = [json.loads(l) for l in pool_path.read_text().splitlines() if l.strip()]
used_rows = {r["source_row"] for r in v1} | {r["nq_row"] for r in pool if r.get("nq_row") is not None}
used_questions = {" ".join(words(r["question"])) for r in v1 + pool}
gate = [json.loads(l) for l in Path("eval/data/simpleqa/gate_slice_v1.jsonl").read_text().splitlines() if l.strip()]
forget = [json.loads(l) for f in ("forget_slice_v1.jsonl", "forget_slice_v2.jsonl")
          for l in Path("eval/data", f).read_text().splitlines() if l.strip()]
gate_runs = set().union(*(runs(r["question"], 3) for r in gate))
forget_runs = set().union(*(runs(r["question"], 5) for r in forget))


def ok(r):
    q = r["question"]
    if TIME.search(q) or HEALTH.search(q) or CHANGES.search(q) or not r["answer"]:
        return False
    if any(len(a.split()) > 4 for a in r["answer"][:1]) or min(len(a.split()) for a in r["answer"]) > 4:
        return False
    if set(words(q)) & people:
        return False
    if r["row"] in used_rows or " ".join(words(q)) in used_questions:
        return False
    return not (runs(q, 3) & gate_runs or runs(q, 5) & forget_runs)


order = sorted(rows, key=lambda r: hashlib.sha256(f"known-slice-v2:{r['row']}".encode()).hexdigest())
picked = [r for r in order if ok(r)][:100]
out = Path("eval/data/known_slice_v2.jsonl")
with open(out, "w") as f:
    for i, r in enumerate(picked, 1):
        q = r["question"].strip()
        q = q[0].upper() + q[1:] + ("" if q.endswith("?") else "?")
        f.write(json.dumps({"id": f"K2{i:03d}", "question": q, "gold": r["answer"], "source_row": r["row"],
                            "source_question": r["question"]}) + "\n")
manifest = {
    "dataset": "google-research-datasets/nq_open", "revision": "5dd9790a83002ad084ddeb7c420dc716852c6f28",
    "split": "validation", "license": "cc-by-sa-3.0", "parquet_sha256": parquet_sha, "rows": len(rows),
    "pick": "sha256('known-slice-v2:<row>') ascending, first 100 that pass the filters",
    "filters": "known_slice_v1's filters unchanged (health/diet/fitness/mood/medical, answers that change over time, "
               "time-sensitive wording, first gold answer at most 4 words, no world or never-told names: %d tokens)"
               % len(people),
    "disjoint_from": {"known_slice_v1 rows and questions": len(v1),
                      "public_anchor_pool_v1 rows and questions (sha256 %s)" % hashlib.sha256(pool_path.read_bytes()).hexdigest(): len(pool),
                      "simpleqa gate slice: no shared 3-word run": len(gate),
                      "forget v1 + v2: no shared 5-word run": len(forget)},
    "form": "the question with its first letter capitalized and a question mark, asked bare in the product form",
    "use": "a second known slice (lead, 30 Oct 06:02): used in the keep rule only if the owner picks the known rule; else "
           "shown beside honesty-3; frozen by hash before any answer",
    "license_of_this_file": "derived from Natural Questions (open-domain split), released under CC BY-SA 3.0, the source's licence",
    "attribution": ["Kwiatkowski et al., Natural Questions: A Benchmark for Question Answering Research, TACL 7:453-466, 2019",
                    "Lee, Chang and Toutanova, Latent Retrieval for Weakly Supervised Open Domain Question Answering, ACL 2019"],
    "file": out.name, "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
Path("eval/data/known_slice_v2.manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
print(len(picked), manifest["sha256"])
