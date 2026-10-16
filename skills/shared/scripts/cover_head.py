#!/usr/bin/env python3
"""
cover_head.py - 给成片「首帧加封面」（默认选项，可关）

作用：把封面图覆盖在视频**开头 0.1 秒**（默认）画面上，使平台抓首帧当缩略图时看到的是封面。
约束：**不改变正片时长、不动音轨、不改变字幕时间轴**（封面只是盖在最前面几帧上）。

用法：
  # 加封面（默认）
  python skills/shared/scripts/cover_head.py add \
    --video outputs/主题/成品.mp4 --cover outputs/主题/封面.png -o outputs/主题/成品_带封面.mp4

  # 不加封面（用户明确说「不加封面」时）→ 直接跳过本步骤，或显式检查
  python skills/shared/scripts/cover_head.py off --video outputs/主题/成品.mp4

  # 校验首帧是否等于封面
  python skills/shared/scripts/cover_head.py check --video outputs/主题/成品_带封面.mp4 --cover outputs/主题/封面.png

  python skills/shared/scripts/cover_head.py selftest
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_DUR = 0.1      # 封面显示时长（秒），默认 0.1
MEAN_DIFF_MAX = 6.0    # 首帧与封面平均像素差上限（0-255）
GUARD_MAX = 8.0        # 允许的时长偏差（秒）


def _die(msg: str, code: int = 1):
    print(f"❌ {msg}", file=sys.stderr)
    raise SystemExit(code)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: str | Path, entries: str = "format=duration") -> dict:
    r = _run(["ffprobe", "-v", "error", "-show_entries", entries,
              "-of", "json", str(path)])
    if r.returncode != 0:
        _die(f"ffprobe 失败：{path}")
    return json.loads(r.stdout or "{}")


def duration_of(path: str | Path) -> float:
    return float(probe(path).get("format", {}).get("duration", 0) or 0)


def video_size(path: str | Path) -> tuple[int, int]:
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)])
    w, h = (r.stdout.strip().split(",") + ["0", "0"])[:2]
    return int(w), int(h)


def _frame(path: str | Path, t: float, out: Path) -> Path:
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path),
              "-frames:v", "1", "-y", str(out)])
    if r.returncode != 0 or not out.is_file():
        _die(f"抽帧失败：{path} @ {t}s")
    return out


def _mean_diff(a: Path, b: Path) -> float:
    try:
        from PIL import Image, ImageChops
    except ImportError:
        _die("需要 Pillow（PIL）")
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        ib = ib.resize(ia.size)
    d = ImageChops.difference(ia, ib).convert("L")
    data = d.tobytes()  # mode "L"：每像素 1 字节（避免 Image.getdata 弃用告警）
    return sum(data) / len(data)


def cmd_add(a) -> int:
    video, cover, out = Path(a.video), Path(a.cover), Path(a.output)
    if not video.is_file():
        _die(f"视频不存在：{video}")
    if not cover.is_file():
        _die(f"封面不存在：{cover}")
    w, h = video_size(video)
    if a.size:
        w, h = (int(x) for x in a.size.lower().split("x"))
    dur = float(a.duration)
    vdur = duration_of(video)
    # 末尾留 10ms 余量：避免 -t 落在下一帧边界上多出一帧（时长被视为「不占用正片时间」）
    t_end = max(0.05, vdur - 0.01)
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-loop", "1", "-i", str(cover),
           "-i", str(video),
           "-filter_complex",
           f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
           f"crop={w}:{h},setsar=1,format=rgba[ov];"
           f"[1:v]format=rgba[base];"
           f"[base][ov]overlay=0:0:enable='lt(t,{dur})':format=auto[v]",
           "-map", "[v]", "-map", "0:a?",
           "-map", "1:a?",
           "-t", f"{t_end:.3f}",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart",
           str(out)]
    r = _run(cmd)
    if r.returncode != 0:
        _die(f"加封面失败：\n{r.stderr.strip()[-400:]}", code=4)
    ndur = duration_of(out)
    if abs(ndur - vdur) > GUARD_MAX:
        _die(f"时长被改变了（{vdur:.2f}s → {ndur:.2f}s），请检查输入", code=5)
    print(f"✅ 已加封面：{out}\n   封面显示 {dur}s（首帧），正片时长 {ndur:.2f}s（原 {vdur:.2f}s）")
    return 0


def cmd_off(a) -> int:
    print(f"⏭  本次不加封面：直接使用 {Path(a.video).name}（正片本身即首帧）")
    return 0


def cmd_check(a) -> int:
    video, cover = Path(a.video), Path(a.cover)
    with tempfile.TemporaryDirectory() as d:
        f0 = _frame(video, 0.0, Path(d) / "f0.png")
        m = _mean_diff(f0, cover)
        fmid = _frame(video, float(a.duration) + 0.3, Path(d) / "f1.png")
        m2 = _mean_diff(fmid, cover)
    ok = m <= MEAN_DIFF_MAX and m2 > MEAN_DIFF_MAX
    print(f"首帧 vs 封面  平均差 {m:.2f}（≤{MEAN_DIFF_MAX} 判通过）")
    print(f"封面后一帧 vs 封面 平均差 {m2:.2f}（> {MEAN_DIFF_MAX} 说明已回到正片）")
    print(("✅ 首帧就是封面，且不占用正片时长" if ok else "⚠️ 首帧不是封面或封面未被撤下"))
    return 0 if ok else 6


def cmd_selftest(_a) -> int:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cover = d / "cover.png"
        video = d / "main.mp4"
        r = _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                  "-i", "color=c=red:s=540x960:d=2", "-frames:v", "1", str(cover)])
        r2 = _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                   "-i", "color=c=blue:s=540x960:d=3",
                   "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   "-shortest", str(video)])
        if r.returncode or r2.returncode:
            _die("自测素材生成失败")
        out = d / "out.mp4"
        ns = argparse.Namespace(video=str(video), cover=str(cover), output=str(out),
                                duration=DEFAULT_DUR, size=None)
        if cmd_add(ns) != 0:
            return 1
        if abs(duration_of(out) - duration_of(video)) > GUARD_MAX:
            _die("自测：时长不一致")
        ck = argparse.Namespace(video=str(out), cover=str(cover), duration=0.1)
        return cmd_check(ck)


def main() -> int:
    ap = argparse.ArgumentParser(description="给成片首帧加封面（默认选项，可关）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="在开头叠加封面（不改变时长/音轨/字幕轴）")
    p.add_argument("--video", required=True)
    p.add_argument("--cover", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--duration", type=float, default=DEFAULT_DUR, help=f"封面显示秒数（默认 {DEFAULT_DUR}）")
    p.add_argument("--size", help="强制画幅 WxH（默认对齐视频）")
    p.set_defaults(func=cmd_add)

    q = sub.add_parser("off", help="不加封面（用户明确要求时）")
    q.add_argument("--video", required=True)
    q.set_defaults(func=cmd_off)

    c = sub.add_parser("check", help="校验首帧是否为封面")
    c.add_argument("--video", required=True)
    c.add_argument("--cover", required=True)
    c.add_argument("--duration", type=float, default=DEFAULT_DUR)
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("selftest", help="自测")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
