"""Gate 1: offline Spanish WER, R2T2 vs faster-whisper large-v3-turbo, on
Pipesong's 100 phone-quality clips (8 kHz mu-law, 5 synthetic voices x 20
sentences). CPU only. Usage: probe_gate1.py r2t2|whisper [limit]"""
import json, re, sys, time, unicodedata
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
import torch
import jiwer

torch.set_num_threads(2)
ENGINE = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0
PS = Path("/root/claude/pipesong/benchmarks")
REFS = (PS / "prompts/tts_sentences_20.txt").read_text().strip().split("\n")
OUT = Path(__file__).parent / f"gate1_{ENGINE}.json"
R2T2_PATH = next((Path(__file__).parent / "hf/hub/models--netease-youdao--Confucius4-R2T2/snapshots").iterdir())

clips = sorted((PS / "audio/samples").glob("phone_*.wav"))
if LIMIT:
    clips = clips[:LIMIT]


def ref_for(p: Path) -> str:
    return REFS[int(p.stem.rsplit("_", 1)[1]) - 1]


def norm(s: str, accents: bool = True) -> str:
    s = s.lower()
    if not accents:
        s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load16k(p: Path) -> np.ndarray:
    wav, sr = sf.read(str(p), dtype="float32")
    return librosa.resample(wav, orig_sr=sr, target_sr=16000) if sr != 16000 else wav


if ENGINE == "r2t2":
    from r2t2 import R2T2ASRModel

    t0 = time.perf_counter()
    model = R2T2ASRModel.from_pretrained(str(R2T2_PATH), dtype=torch.bfloat16, device_map="cpu", max_new_tokens=256)
    load_s = time.perf_counter() - t0

    def run(audio: np.ndarray) -> str:
        return model.transcribe(audio=(audio, 16000), language="Spanish")[0].text
else:
    from faster_whisper import WhisperModel

    t0 = time.perf_counter()
    model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", cpu_threads=2,
                         download_root=str(Path(__file__).parent / "hf/whisper"))
    load_s = time.perf_counter() - t0

    def run(audio: np.ndarray) -> str:
        segs, _ = model.transcribe(audio, language="es")
        return " ".join(s.text.strip() for s in segs)

# Resumable: one JSON line per finished clip; a restart skips what is done.
PARTIAL = OUT.with_suffix(".jsonl")
rows = [json.loads(l) for l in PARTIAL.read_text().splitlines()] if PARTIAL.exists() else []
done = {r["file"] for r in rows}
for p in clips:
    if p.name in done:
        continue
    audio = load16k(p)
    t = time.perf_counter()
    hyp = run(audio)
    ms = (time.perf_counter() - t) * 1000
    row = {"file": p.name, "ref": ref_for(p), "hyp": hyp, "ms": round(ms), "audio_s": round(len(audio) / 16000, 2)}
    rows.append(row)
    with PARTIAL.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{p.name} {ms:.0f}ms rtf={ms / 1000 / (len(audio) / 16000):.2f} | {hyp[:70]}", flush=True)
rows = [r for r in rows if r["file"] in {p.name for p in clips}]

refs = [r["ref"] for r in rows]
hyps = [r["hyp"] for r in rows]
summary = {
    "engine": ENGINE,
    "clips": len(rows),
    "load_s": round(load_s, 1),
    "wer": round(jiwer.wer([norm(x) for x in refs], [norm(x) for x in hyps]), 4),
    "wer_no_accents": round(jiwer.wer([norm(x, False) for x in refs], [norm(x, False) for x in hyps]), 4),
    "rtf_mean": round(sum(r["ms"] / 1000 / r["audio_s"] for r in rows) / len(rows), 3),
    "per_voice": {},
}
for v in sorted({r["file"].rsplit("_", 1)[0] for r in rows}):
    sub = [r for r in rows if r["file"].startswith(v + "_")]
    summary["per_voice"][v] = round(jiwer.wer([norm(r["ref"]) for r in sub], [norm(r["hyp"]) for r in sub]), 4)
OUT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
print(json.dumps(summary, indent=1))
