#!/usr/bin/env python3
"""
subtitle_sync.py - 校验成片「字幕时间轴 vs 实际人声」是否逐句对齐

为什么需要它：
    改视频时长（删句、合并句、换配音、调语速）之后，字幕很容易整体滞后或越走越偏。
    常见根因是拿「分段音频文件的时长」去累加时间轴：mp3 每段带编码器填充（约 50-60ms），
    18 段就是 1 秒，字幕因此越到后面越落后，而总时长看起来只差一点点，肉眼难判断。
    正确做法是从**拼接后的整条音轨**反算每句起止，再用「每句起音点」验证一遍。

判据：
    逐句在字幕起点附近搜索人声起音点，计算 offset = 起音点 - 字幕起点。
    - 单句 |offset| > 0.15s  → FAIL（该句明显错位）
    - 末句 offset - 首句 offset > 0.20s → FAIL（累积漂移，时间轴是"拼"出来的）
    - 整体恒定 ±0.05s 属正常：语音本身有起音爬升，不是错位。

用法：
    python skills/shared/scripts/subtitle_sync.py check --video 成品.mp4 --sub 字幕.srt
    python skills/shared/scripts/subtitle_sync.py check --video 成品.mp4 --sub 字幕.ass --json
    python skills/shared/scripts/subtitle_sync.py selftest
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SR = 16000
FRAME = 0.010          # 10ms 分析帧
MAX_OFFSET = 0.15      # 单句容差（秒）
MAX_DRIFT = 0.20       # 首末句漂移容差（秒）
SEARCH_BACK = 0.30     # 起点前搜索范围
SEARCH_FWD = 0.80      # 起点后搜索范围


def _die(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ── 字幕解析 ────────────────────────────────────────────────────────
def _ts(sec: float) -> float:
    return sec


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        time_line = next((l for l in lines if "-->" in l), None)
        if not time_line:
            continue
        m = re.match(r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", time_line)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / (10 ** len(str(g[3])))
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / (10 ** len(str(g[7])))
        text_lines = lines[lines.index(time_line) + 1:]
        cues.append((start, end, " ".join(text_lines).strip()))
    return cues


def parse_ass(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for line in text.replace("\r", "").split("\n"):
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue
        def to_sec(t: str) -> float:
            h, m, s = t.strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        body = re.sub(r"\{[^}]*\}", "", fields[9]).replace("\\N", " ").replace("\\n", " ").strip()
        cues.append((to_sec(fields[1]), to_sec(fields[2]), body))
    return cues


def load_cues(path: Path) -> list[tuple[float, float, str]]:
    if not path.is_file():
        _die(f"字幕文件不存在: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    cues = parse_ass(text) if path.suffix.lower() == ".ass" else parse_srt(text)
    if not cues:
        _die(f"没解析出字幕条目: {path}（仅支持 SRT / ASS）")
    return cues


# ── 音频与起音检测 ──────────────────────────────────────────────────
def decode_pcm(video: Path) -> "list[float]":
    if shutil.which("ffmpeg") is None:
        _die("需要 ffmpeg")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:a:0",
         "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        _die(f"无法解码音频: {video}（{r.stderr.decode('utf-8', 'replace')[-200:]}）")
    import array
    arr = array.array("h")
    arr.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    return [v / 32768.0 for v in arr]


def _rms(samples: list[float], a: int, b: int) -> float:
    a = max(0, a)
    b = min(len(samples), b)
    if b <= a:
        return 0.0
    total = 0.0
    for v in samples[a:b]:
        total += v * v
    return (total / (b - a)) ** 0.5


def find_onset(samples: list[float], start: float) -> float | None:
    """在 [start-SEARCH_BACK, start+SEARCH_FWD] 内找第一个越过噪声阈值的分析帧。"""
    fl = int(FRAME * SR)
    a = int((start - SEARCH_BACK) * SR)
    b = int((start + SEARCH_FWD) * SR)
    frames = [(_rms(samples, i, i + fl), i) for i in range(a, b, fl)]
    if not frames:
        return None
    levels = sorted(x[0] for x in frames)
    floor = levels[len(levels) // 10] if levels else 0.0
    thresh = max(0.008, floor * 3.0)
    for level, i in frames:
        if level > thresh:
            return i / SR
    return None


# ── 主检查 ──────────────────────────────────────────────────────────
def check(video: Path, sub: Path, tol: float = MAX_OFFSET, drift: float = MAX_DRIFT,
          as_json: bool = False) -> int:
    if not video.is_file():
        _die(f"视频不存在: {video}")
    cues = load_cues(sub)
    samples = decode_pcm(video)
    audio_len = len(samples) / SR

    rows = []
    for i, (st, en, text) in enumerate(cues, 1):
        if st > audio_len:
            rows.append({"index": i, "start": round(st, 3), "offset": None,
                         "text": text[:24], "error": "字幕起点超出音轨"})
            continue
        onset = find_onset(samples, st)
        rows.append({"index": i, "start": round(st, 3), "end": round(en, 3),
                     "onset": None if onset is None else round(onset, 3),
                     "offset": None if onset is None else round(onset - st, 3),
                     "text": text[:24]})

    measured = [r for r in rows if r.get("offset") is not None]
    missing = [r for r in rows if r.get("offset") is None]
    offsets = [r["offset"] for r in measured]
    drift_val = (offsets[-1] - offsets[0]) if len(offsets) >= 2 else 0.0
    worst = max((abs(o) for o in offsets), default=0.0)
    bad_single = [r for r in measured if abs(r["offset"]) > tol]
    ok = not missing and not bad_single and abs(drift_val) <= drift

    if as_json:
        print(json.dumps({
            "ok": ok, "cues": len(cues), "audio_duration": round(audio_len, 3),
            "max_abs_offset": round(worst, 3), "drift": round(drift_val, 3),
            "tolerance": tol, "drift_tolerance": drift,
            "unmeasured": [r["index"] for r in missing],
            "misaligned": [r["index"] for r in bad_single],
            "rows": rows,
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 3

    print(f"audio {audio_len:.2f}s · cues {len(cues)}")
    for r in rows:
        off = r.get("offset")
        flag = ""
        if off is None:
            flag = "  <no onset>"
        elif abs(off) > tol:
            flag = "  <MISALIGNED>"
        shown = "  n/a" if off is None else f"{off:+.3f}s"
        print(f"  {r['index']:2d} start={r['start']:6.2f} offset={shown}{flag}  {r['text']}")
    print(f"max |offset| {worst:.3f}s · drift(first→last) {drift_val:+.3f}s "
          f"· tolerance {tol:.2f}s / drift {drift:.2f}s")
    if ok:
        print("PASS 字幕与声音逐句对齐")
        return 0
    if missing:
        print(f"FAIL {len(missing)} 句未测到人声起音（字幕为空句、或该句没有声音）")
    if bad_single:
        print(f"FAIL {len(bad_single)} 句错位超过 {tol:.2f}s")
    if abs(drift_val) > drift:
        print(f"FAIL 首末句漂移 {drift_val:+.3f}s，时间轴是累加拼出来的，不是从整条音轨反算的")
    return 3


# ── 自检 ────────────────────────────────────────────────────────────
def selftest() -> int:
    if shutil.which("ffmpeg") is None:
        _die("selftest 需要 ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # 3 段 0.4s 的 440Hz 音，分别在 1.0 / 3.0 / 5.0 秒起（中间用真静音隔开）
        starts = [1.0, 3.0, 5.0]
        parts: list[Path] = []
        cursor = 0.0
        for i, st in enumerate(starts):
            if st > cursor + 0.001:
                p = d / f"s{i}.wav"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                                "-i", "anullsrc=r=16000:cl=mono",
                                "-t", f"{st - cursor:.3f}", "-c:a", "pcm_s16le", str(p)],
                               check=True)
                parts.append(p)
            p = d / f"t{i}.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=0.4:sample_rate=16000",
                            "-c:a", "pcm_s16le", str(p)], check=True)
            parts.append(p)
            cursor = st + 0.4
        p = d / "tail.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "anullsrc=r=16000:cl=mono", "-t", "0.8",
                        "-c:a", "pcm_s16le", str(p)], check=True)
        parts.append(p)
        lst = d / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        audio = d / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c:a", "pcm_s16le", str(audio)], check=True)
        video = d / "v.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=320x240:d=6:r=10", "-i", str(audio),
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(video)], check=True)

        def write_srt(path: Path, offset: float) -> None:
            def fmt(x: float) -> str:
                h = int(x // 3600); m = int(x % 3600 // 60); s = x % 60
                return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
            lines = []
            for i, st in enumerate(starts, 1):
                lines.append(f"{i}\n{fmt(st+offset)} --> {fmt(st+offset+0.4)}\n第{i}句\n")
            path.write_text("\n".join(lines), encoding="utf-8")

        good = d / "good.srt"
        write_srt(good, 0.0)
        r = subprocess.run([sys.executable, __file__, "check", "--video", str(video),
                            "--sub", str(good)], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout + r.stderr, file=sys.stderr)
            _die("selftest 失败：正确字幕未通过")
        print("[ok] 正确字幕 → PASS")

        bad = d / "bad.srt"
        write_srt(bad, 0.30)
        r = subprocess.run([sys.executable, __file__, "check", "--video", str(video),
                            "--sub", str(bad)], capture_output=True, text=True)
        if r.returncode == 0:
            print(r.stdout, file=sys.stderr)
            _die("selftest 失败：错位字幕被误判为 PASS")
        print("[ok] 错位 300ms 的字幕 → FAIL")

    print("[PASS] selftest 通过：能测出逐句错位，也能放行正常字幕")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验字幕时间轴与成片人声是否逐句对齐")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="逐句比对字幕起点与真实起音点")
    c.add_argument("--video", required=True, type=Path)
    c.add_argument("--sub", required=True, type=Path, help="SRT 或 ASS")
    c.add_argument("--max-offset", type=float, default=MAX_OFFSET)
    c.add_argument("--max-drift", type=float, default=MAX_DRIFT)
    c.add_argument("--json", action="store_true")

    sub.add_parser("selftest", help="用合成音自检")

    args = ap.parse_args(argv)
    if args.cmd == "check":
        return check(args.video, args.sub, args.max_offset, args.max_drift, args.json)
    if args.cmd == "selftest":
        return selftest()
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
