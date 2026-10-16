"""cover_head.py：首帧封面不改变时长/音轨/字幕轴。"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "skills" / "shared" / "scripts" / "cover_head.py"

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要 ffmpeg / ffprobe",
)


def _probe(path: Path, entries: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def test_selftest_passes():
    r = subprocess.run([sys.executable, str(SCRIPT), "selftest"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "首帧就是封面" in r.stdout


def test_add_keeps_duration_and_audio(tmp_path: Path):
    video = tmp_path / "main.mp4"
    cover = tmp_path / "cover.png"
    out = tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
        check=True,
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=360x640:d=1",
         "-frames:v", "1", str(cover)],
        check=True,
    )
    r = subprocess.run([sys.executable, str(SCRIPT), "add", "--video", str(video),
                        "--cover", str(cover), "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    d_before = float(_probe(video, "format=duration"))
    d_after = float(_probe(out, "format=duration"))
    assert abs(d_after - d_before) < 0.05, f"{d_before} -> {d_after}"
    assert _probe(out, "stream=codec_type") and "audio" in _probe(out, "stream=codec_type")

    c = subprocess.run([sys.executable, str(SCRIPT), "check", "--video", str(out),
                        "--cover", str(cover)], capture_output=True, text=True)
    assert c.returncode == 0, c.stdout + c.stderr
