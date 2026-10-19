"""subtitle_sync.py：改时长后必须验证字幕逐句对齐。"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "skills" / "shared" / "scripts" / "subtitle_sync.py"

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="需要 ffmpeg",
)


def test_selftest_passes():
    r = subprocess.run([sys.executable, str(SCRIPT), "selftest"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "逐句错位" in r.stdout


def test_parses_srt_and_ass(tmp_path: Path):
    sys.path.insert(0, str(SCRIPT.parent))
    import subtitle_sync

    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:03,000\n你好\n", encoding="utf-8")
    cues = subtitle_sync.load_cues(srt)
    assert len(cues) == 1
    assert cues[0][0] == pytest.approx(1.5)
    assert cues[0][2] == "你好"

    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:02.00,0:00:03.50,Default,,0,0,0,,{\\fad(100,100)}第一句\\N第二行\n",
        encoding="utf-8",
    )
    cues = subtitle_sync.load_cues(ass)
    assert len(cues) == 1
    assert cues[0][0] == pytest.approx(2.0)
    assert "第一句" in cues[0][2] and "第二行" in cues[0][2]


def test_offset_detection_rejects_drift():
    """构造一条逐句漂移的字幕，确认 checker 返回非 0（而不是只看总时长）。"""
    import tempfile
    import subtitle_sync

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        parts = []
        cursor = 0.0
        starts = [0.5, 2.0, 3.5]
        for i, st in enumerate(starts):
            if st > cursor:
                p = d / f"s{i}.wav"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                                "-i", "anullsrc=r=16000:cl=mono", "-t", f"{st-cursor:.3f}",
                                "-c:a", "pcm_s16le", str(p)], check=True)
                parts.append(p)
            p = d / f"t{i}.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "sine=frequency=500:duration=0.3:sample_rate=16000",
                            "-c:a", "pcm_s16le", str(p)], check=True)
            parts.append(p)
            cursor = st + 0.3
        lst = d / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        audio = d / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c:a", "pcm_s16le", str(audio)], check=True)
        video = d / "v.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=320x240:d=5:r=10", "-i", str(audio),
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(video)], check=True)

        drifted = d / "drift.srt"
        lines = []
        for i, st in enumerate(starts, 1):
            off = 0.06 * (i - 1)
            lines.append(f"{i}\n"
                         f"00:00:{st+off:06.3f} --> 00:00:{st+off+0.3:06.3f}\n"
                         f"第{i}句\n".replace(".", ","))
        drifted.write_text("\n".join(lines), encoding="utf-8")

        assert subtitle_sync.check(video, drifted, as_json=True) in (0, 3)
        r = subprocess.run([sys.executable, str(SCRIPT), "check", "--video", str(video),
                            "--sub", str(drifted), "--max-offset", "0.01", "--json"],
                           capture_output=True, text=True)
        assert r.returncode == 3, r.stdout + r.stderr
