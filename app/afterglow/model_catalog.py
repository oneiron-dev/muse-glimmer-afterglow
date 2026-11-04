"""Settings > Models catalog: data only, no imports with side effects, no disk or network access.

Each choice is a dict with:
  id, slot ("speech" | "vision"), label (a plain title a person understands), model (the model's own name,
  shown small), adds (one plain line on what you get),
  runtime ("mlx-audio" | "whisper.cpp" | "mlx-vlm" | "none"),
  repo (Hugging Face repo id), revision (pinned commit sha or None to resolve at
    download time, then pin in the download manifest),
  files (None for every file in the repo, or an exact list of repo paths),
  single_file (True stores one flat file under models_dir(), False a folder),
  disk_gb, memory_gb (resident estimate when loaded on this computer),
  speed, speed_source, needs ("apple-silicon" | "cuda" | "any"),
  tested (True only after a run on this Mac in this lane).

Speech rows come from research/ASR-PICK.md, vision rows from research/VLM-PICK.md.
Only runtimes the app can load are listed (mlx-audio, whisper.cpp, mlx-vlm).
"""

# Pinned revisions (authoritative Hub metadata, staged by the parent):
#   projector meta-models/Muse-Glimmer-30B-GGUF @ b44f1072cf75e918a1b82a3a17b45c4389b076e7
#   default vision mlx-community/Qwen3.5-4B-MLX-4bit @ 32f3e8ecf65426fc3306969496342d504bfa13f3
GLIMMER_REPO = "meta-models/Muse-Glimmer-30B-GGUF"
GLIMMER_REVISION = "b44f1072cf75e918a1b82a3a17b45c4389b076e7"
MMPROJ_FILE = "mmproj-Muse-Glimmer-30B-Q4_K_M.gguf"
MMPROJ_SIZE = 1400328928
MMPROJ_SHA256 = "f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6"

QWEN35_4B_REPO = "mlx-community/Qwen3.5-4B-MLX-4bit"
QWEN35_4B_REVISION = "32f3e8ecf65426fc3306969496342d504bfa13f3"

CATALOG = [
    # --- speech: dictation models (ASR-PICK.md) ---
    {
        "id": "qwen3-asr-1.7b-8bit",
        "slot": "speech",
        "label": "English and Japanese",
        "model": "Qwen3-ASR 1.7B",
        "adds": "Best when you speak English, Japanese, or both in one sentence.",
        "runtime": "mlx-audio",
        "repo": "mlx-community/Qwen3-ASR-1.7B-8bit",
        "revision": None,
        "files": None,
        "single_file": False,
        "disk_gb": 2.5,
        "memory_gb": 3.5,
        "speed": "Writes down a few seconds of speech in under half a second on this Mac.",
        "speed_source": "https://github.com/holovchenko/qwen3-asr-swift/blob/main/docs/benchmarks/asr-wer.md",
        "needs": "apple-silicon",
        "tested": True,
    },
    {
        "id": "whisper-large-v3",
        "slot": "speech",
        "label": "Whisper",
        "model": "Whisper large-v3",
        "adds": "Works on almost any computer. A little slower, and weaker when you mix languages.",
        "runtime": "whisper.cpp",
        "repo": "ggerganov/whisper.cpp",
        "revision": None,
        "files": ["ggml-large-v3.bin"],
        "single_file": True,
        "disk_gb": 3.1,
        "memory_gb": 4.0,
        "speed": "",
        "speed_source": "research/ASR-PICK.md",
        "needs": "any",
        "tested": False,
    },
    {
        "id": "whisper-large-v3-turbo",
        "slot": "speech",
        "label": "Whisper, faster",
        "model": "Whisper large-v3 turbo",
        "adds": "Faster and smaller than Whisper, a little less accurate.",
        "runtime": "whisper.cpp",
        "repo": "ggerganov/whisper.cpp",
        "revision": None,
        "files": ["ggml-large-v3-turbo.bin"],
        "single_file": True,
        "disk_gb": 1.7,
        "memory_gb": 2.5,
        "speed": "",
        "speed_source": "https://www.fermionresearch.com/research/phonon-2/",
        "needs": "any",
        "tested": False,
    },
    {
        "id": "granite-speech-5.0-470m-turboctc",
        "slot": "speech",
        "label": "Small and light",
        "model": "Granite Speech 470M",
        "adds": "For computers with 16 GB of memory. Less tested with spoken Japanese.",
        "runtime": "mlx-audio",
        "repo": "ibm-granite/granite-speech-5.0-470m-turboctc",
        "revision": None,
        "files": None,
        "single_file": False,
        "disk_gb": 1.0,
        "memory_gb": 1.5,
        "speed": "",
        "speed_source": "research/ASR-PICK.md",
        "needs": "apple-silicon",
        "tested": False,
    },
    # --- vision: circle models (VLM-PICK.md) ---
    {
        "id": "glimmer-mmproj",
        "slot": "vision",
        "label": "Glimmer looks for itself",
        "model": "Glimmer's vision add-on",
        "adds": "Your agent looks at what you circle directly, with no extra model on this computer.",
        "runtime": "none",
        "repo": GLIMMER_REPO,
        "revision": GLIMMER_REVISION,
        "files": [MMPROJ_FILE],
        "single_file": True,
        "disk_gb": 1.4,
        "memory_gb": 1.6,
        "speed": "",
        "speed_source": "research/VLM-PICK.md",
        "needs": "any",
        "tested": False,
    },
    {
        "id": "qwen3.5-4b-mac",
        "slot": "vision",
        "label": "See pictures on this Mac",
        "model": "Qwen3.5 4B",
        "adds": "Sees the pictures, icons and emoji in what you circle and tells your agent what is there.",
        "runtime": "mlx-vlm",
        "repo": QWEN35_4B_REPO,
        "revision": QWEN35_4B_REVISION,
        "files": None,
        "single_file": False,
        "disk_gb": 3.1,
        "memory_gb": 5.0,
        "speed": "Takes about 1 to 3 seconds per question on this Mac.",
        "speed_source": "BUILDOUT-NOTES/models.md",
        "needs": "apple-silicon",
        "tested": True,
    },
    {
        "id": "qwen3.5-2b-mac",
        "slot": "vision",
        "label": "See pictures, lighter",
        "model": "Qwen3.5 2B",
        "adds": "Smaller, for computers with 16 GB of memory. Sees less detail.",
        "runtime": "mlx-vlm",
        "repo": "mlx-community/Qwen3.5-2B-MLX-4bit",
        "revision": None,
        "files": None,
        "single_file": False,
        "disk_gb": 1.8,
        "memory_gb": 3.0,
        "speed": "",
        "speed_source": "research/VLM-PICK.md",
        "needs": "apple-silicon",
        "tested": False,
    },
]


def by_id(choice_id):
    """One catalog dict by id, or None."""
    return next((c for c in CATALOG if c["id"] == choice_id), None)


def for_slot(slot):
    """Catalog dicts for a slot, best first (list order is the recommendation order)."""
    return [c for c in CATALOG if c["slot"] == slot]
