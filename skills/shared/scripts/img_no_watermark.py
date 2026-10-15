#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 生图去水印工具（确定性处理，不重新生成、不计费）。

背景
----
国内生图服务（智谱 glm-image、硅基流动 Qwen-Image / Kolors 等）会在成图**右下角**
统一叠加「AI生成」可见标识，服务侧无法关闭。本工具负责：

1. `detect`  —— 定位标识的精确像素框（OCR 为主，几何兜底）
2. `cover`   —— 用「按列上下边界线性插值」重建背景，**保持原尺寸与画幅**（推荐用于已有图）
3. `gen`     —— 生成时**多留一条底边**，成功后把带标识的底边整条裁掉，得到**精确目标比例**的干净成品；
                服务返回的原始图**永不覆盖**，另存为 `<成品同目录>/assets/<名>_raw.<后缀>`
4. `process` —— 对已有图一把梭：detect → cover（或 crop）→ 校验

设计约束
--------
- 原图永远保留：`process` 只写新文件；`gen` 先落盘原始图再加工。
- 生成提示词自动追加「右下角留空」约定，降低标识压到画面元素上的概率。
- 收尾自动校验：成品再跑一次 detect，应检不到标识。

用法
----
    # 生成 + 自动去水印（图片模型/Key 走 .env 的 IMG_* 配置）
    python skills/shared/scripts/img_no_watermark.py gen \
        --prompt "竖版反诈科普封面插画……" --size 1008x2016 \
        -o outputs/<主题名>/cover.png

    # 处理已有图（保持原尺寸，用背景插值补掉右下角标识）
    python skills/shared/scripts/img_no_watermark.py process \
        -i outputs/<主题>/assets/cover_raw.png -o outputs/<主题>/cover.png

    # 只看检出的标识框，不改图
    python skills/shared/scripts/img_no_watermark.py detect -i 某图.png --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageOps
except ImportError as exc:  # pragma: no cover
    sys.exit(f"需要 numpy 与 Pillow：{exc}")

ROOT = Path(__file__).resolve().parents[3]          # <项目根>/skills/shared/scripts/x.py
TESSDATA_CANDIDATES = [
    ROOT / "skills" / "shared" / "tessdata",
    Path(os.environ.get("TESSDATA_PREFIX", "/nonexistent")),
    Path("/usr/share/tesseract-ocr/5/tessdata"),
    Path("/usr/share/tesseract-ocr/4.00/tessdata"),
    Path("/usr/share/tessdata"),
]

# 生图服务标识里可能被 OCR 认出的字形
WM_TOKENS = ("生成", "AI", "Al", "Aj", "A|", "A]", "A1", "A!", "生 成")

# 生成时追加的构图约定：右下角与底边留空
CLEAN_CORNER_RULE = (
    "构图要求：画面最底部与右下角约 8% 的边缘区域保持纯净背景，"
    "不要在那里放置任何文字、图标、线条、数字或装饰元素。"
)

DEFAULT_STRATEGY = "cover"

# 生图服务预设：Qwen 优先（便宜），中文文字不达标再切 GLM
PRESETS = {
    "qwen": {"base": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen-Image",
             "key_env": "SILICONFLOW_API_KEY"},
    "glm": {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-image",
            "key_env": "IMG_API_KEY"},
}


def read_env_file(env_file: str | None = None) -> dict:
    """读项目 .env（只取值，不外显）。"""
    path = Path(env_file) if env_file else ROOT / ".env"
    data = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
            if m and not line.lstrip().startswith("#"):
                data[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except OSError:
        pass
    return data


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def find_tessdata() -> Path | None:
    """找到含 chi_sim 的 tessdata 目录。"""
    for d in TESSDATA_CANDIDATES:
        try:
            if (d / "chi_sim.traineddata").is_file():
                return d
        except OSError:
            continue
    return None


def _ocr_words(img_l: Image.Image, box: tuple[int, int, int, int], td: Path,
               scale: int = 4, ths=(None, 150, 170, 190)) -> list[tuple[str, int, int, int, int]]:
    """对 box 区域做多阈值 OCR，返回 [(文字, x, y, w, h)]（坐标已映射回原图）。"""
    x0, y0, x1, y1 = box
    crop = ImageOps.autocontrast(img_l.crop(box))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    out: list[tuple[str, int, int, int, int]] = []
    env = {**os.environ, "TESSDATA_PREFIX": str(td)}
    for th in ths:
        img = crop if th is None else crop.point(lambda v: 0 if v < th else 255)
        tmp = Path("/tmp/_nwm_ocr.png")
        img.save(tmp)
        try:
            res = subprocess.run(
                ["tesseract", str(tmp), "stdout", "-l", "chi_sim", "--psm", "6", "tsv"],
                capture_output=True, text=True, env=env,
            )
        except FileNotFoundError:
            return []
        for line in res.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 12 or not parts[11].strip() or parts[10] in ("", "-1"):
                continue
            out.append((parts[11], int(parts[6]) // scale + x0, int(parts[7]) // scale + y0,
                        int(parts[8]) // scale, int(parts[9]) // scale))
    return out


def _in_corner(bbox, W: int, H: int) -> bool:
    x0, y0, x1, y1 = bbox
    return (
        (W - x1) <= 0.12 * W and (H - y1) <= 0.06 * H
        and (x1 - x0) <= 0.30 * W and (y1 - y0) <= 0.07 * H
        and x0 >= 0.60 * W and y0 >= 0.88 * H
    )


def detect_watermark(img: Image.Image, verbose: bool = False):
    """定位右下角生图标识，返回 (x0, y0, x1, y1) 或 None。"""
    W, H = img.size
    win = (int(W * 0.68), int(H * 0.90), W, H)
    td = find_tessdata()
    if td:
        words = _ocr_words(img.convert("L"), win, td)
        hits = []
        for text, x, y, w, h in words:
            if any(tok in text for tok in WM_TOKENS):
                bbox = (x, y, x + w, y + h)
                if _in_corner(bbox, W, H):
                    hits.append(bbox)
        if hits:
            x0 = min(b[0] for b in hits); y0 = min(b[1] for b in hits)
            x1 = max(b[2] for b in hits); y1 = max(b[3] for b in hits)
            # 标识常被 OCR 拆成多段（如「AI」+「生成」），把同一行、字号相近的邻段并进来
            hmin = max(8, int(0.4 * (y1 - y0)))
            for text, x, y, w, h in words:
                if not text.strip() or h < hmin:
                    continue
                vo = min(y1, y + h) - max(y0, y)
                if vo >= 0.4 * min(y1 - y0, h) and (x - x1) <= 1.5 * (y1 - y0) \
                        and (x0 - (x + w)) <= 1.5 * (y1 - y0):
                    x0, y0 = min(x0, x), min(y0, y)
                    x1, y1 = max(x1, x + w), max(y1, y + h)
            if verbose:
                print(f"[detect] OCR 命中 {len(hits)} 处，合并同类段后框=({x0},{y0})-({x1},{y1})")
            return _pad((x0, y0, x1, y1), W, H)
        if verbose:
            print("[detect] OCR 未命中，转几何兜底")

    # 几何兜底：紧贴右下角的小型高对比簇
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    bx0, by0 = int(W * 0.78), int(H * 0.93)
    reg = a[by0:H, bx0:W]
    if reg.size == 0:
        return None
    bg = np.median(reg.reshape(-1, 3), axis=0)
    mask = np.abs(reg - bg).max(axis=2) > 60
    if mask.sum() < 12:
        return None
    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()) + bx0, int(ys.min()) + by0, int(xs.max()) + bx0 + 1, int(ys.max()) + by0 + 1)
    if not _in_corner(bbox, W, H):
        if verbose:
            print("[detect] 几何簇在角落之外，判定无标识")
        return None
    if verbose:
        print(f"[detect] 几何命中，框={bbox}")
    return _pad(bbox, W, H)


def _pad(bbox, W: int, H: int, pad: int = 3):
    x0, y0, x1, y1 = bbox
    return (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))


def cover_region(img: Image.Image, bbox, feather: int = 3) -> Image.Image:
    """用背景重建 bbox：按列在上下边界之间线性插值（底部贴边则用上方斜率外推）。"""
    src = np.asarray(img.convert("RGB")).astype(np.float32)
    H, W, _ = src.shape
    x0, y0, x1, y1 = bbox
    out = src.copy()

    for x in range(x0, x1):
        above = src[max(0, y0 - 4):y0, x].mean(axis=0) if y0 - 4 >= 0 else src[y0, x]
        if y1 < H - 1:
            below = src[y1:min(H, y1 + 4), x].mean(axis=0)
            ramp = np.linspace(0.0, 1.0, y1 - y0, dtype=np.float32).reshape(-1, 1)
            col = above[None, :] * (1 - ramp) + below[None, :] * ramp
        else:
            # 贴着底边：用上方两点估斜率做外推，保持渐变连续
            r1 = src[max(0, y0 - 4):y0, x].mean(axis=0) if y0 >= 4 else src[y0, x]
            r0 = src[max(0, y0 - 10):max(0, y0 - 6), x].mean(axis=0) if y0 >= 10 else r1
            slope = (r1 - r0) / 5.0
            ramp = np.arange(1, y1 - y0 + 1, dtype=np.float32).reshape(-1, 1)
            col = r1[None, :] + slope[None, :] * (ramp + 2.0)
        out[y0:y1, x] = np.clip(col, 0, 255)

    # 左右边缘羽化，避免硬边
    if feather > 0:
        alpha = np.ones((y1 - y0, x1 - x0), dtype=np.float32)
        f = min(feather, (x1 - x0) // 2)
        for i in range(f):
            v = (i + 1) / (f + 1)
            alpha[:, i] = v
            alpha[:, -1 - i] = v
        a3 = alpha[..., None]
        out[y0:y1, x0:x1] = out[y0:y1, x0:x1] * a3 + src[y0:y1, x0:x1] * (1 - a3)

    return Image.fromarray(out.round().astype(np.uint8), "RGB")


def target_height(width: int, ratio: str) -> int:
    """按 '9:16' 这类比例算出目标高度。"""
    m = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", ratio)
    if not m:
        raise ValueError(f"比例格式应为 W:H，收到 {ratio!r}")
    w, h = int(m.group(1)), int(m.group(2))
    return round(width * h / w)


def cut_smoothness(a: np.ndarray, y: int, band: int = 24) -> float:
    """裁切线两侧相邻带的平均色差（越小越平滑，说明切在纯背景上）。"""
    y0, y1 = max(0, y - band), min(a.shape[0], y + band)
    if y - band < 0 or y + band > a.shape[0]:
        return 999.0
    up = a[y - band:y].reshape(-1, 3).mean(axis=0)
    dn = a[y:y + band].reshape(-1, 3).mean(axis=0)
    return float(np.abs(up - dn).max())


def content_frac(a: np.ndarray, y0: int, y1: int) -> float:
    """区域内「偏离该行中位色 >30」的像素比例：用于判断某条横带是否落在纯背景上。"""
    y0, y1 = max(0, y0), min(a.shape[0], y1)
    if y1 <= y0:
        return 1.0
    step = max(1, (y1 - y0) // 40)
    fracs = []
    for y in range(y0, y1, step):
        row = a[y]
        med = np.median(row, axis=0)
        fracs.append(float((np.abs(row - med).max(axis=1) > 30).mean()))
    return float(np.mean(fracs))


def strip_metadata(img: Image.Image) -> Image.Image:
    """丢弃 EXIF / 文本元数据（服务侧埋的 AIGC 标识）。"""
    clean = Image.new(img.mode, img.size)
    clean.putdata(list(img.getdata()) if img.mode != "RGB" else img.convert("RGB").getdata())
    return clean


def save(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path)

# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_detect(args) -> int:
    img = Image.open(args.input)
    bbox = detect_watermark(img, verbose=True)
    if args.json:
        print(json.dumps({"file": str(args.input), "size": img.size, "watermark_bbox": bbox},
                         ensure_ascii=False))
    else:
        print(f"图片 {args.input} {img.size[0]}x{img.size[1]}")
        print("检出标识框：" + (f"({bbox[0]},{bbox[1]})-({bbox[2]},{bbox[3]})" if bbox else "无"))
    return 0 if bbox else 3


def _process_one(in_path: Path, out_path: Path, strategy: str, ratio: str | None,
                 bbox_override=None) -> dict:
    img = Image.open(in_path)
    W, H = img.size
    report = {"input": str(in_path), "output": str(out_path), "size_in": [W, H]}
    bbox = bbox_override or detect_watermark(img, verbose=True)
    if bbox is None:
        report["watermark_bbox"] = None
        report["strategy"] = "none"
        if out_path.resolve() != in_path.resolve():
            save(img, out_path)
            report["size_out"] = [W, H]
        print("未检出右下角标识，原样输出。")
        return report
    report["watermark_bbox"] = list(bbox)

    use_crop = False
    forced = strategy == "crop"
    if strategy in ("auto", "crop") and ratio:
        y_cut = target_height(W, ratio)
        rgb = np.asarray(img.convert("RGB")).astype(np.float32)
        smooth = cut_smoothness(rgb, y_cut)
        clean = content_frac(rgb, y_cut - 140, y_cut + 140)
        report["cut_check"] = {"y_cut": y_cut, "smoothness": round(smooth, 2),
                               "content_frac": round(clean, 4)}
        if y_cut <= bbox[1] and (H - y_cut) >= 4 and (
                forced or (smooth < 20.0 and clean < 0.08)):
            use_crop = True
        elif forced:
            report["warning"] = "强制裁切：裁切线附近仍有画面内容，可能切到主体"
    if strategy == "cover":
        use_crop = False

    if use_crop:
        out = img.crop((0, 0, W, y_cut))
        report["strategy"] = "crop"
        report["note"] = f"裁掉底部 {H - y_cut}px（标识整条移除），成品 {W}x{y_cut}"
    else:
        out = cover_region(img, bbox)
        report["strategy"] = "cover"
        report["note"] = f"背景插值覆盖标识区 {bbox[2]-bbox[0]}x{bbox[3]-bbox[1]}px，尺寸与画幅不变"

    out = strip_metadata(out)
    save(out, out_path)
    report["size_out"] = list(out.size)

    left = detect_watermark(Image.open(out_path))
    report["verify_bbox_after"] = list(left) if left else None
    report["ok"] = left is None
    return report


def cmd_process(args) -> int:
    in_path, out_path = Path(args.input), Path(args.output)
    if not in_path.is_file():
        print(f"输入不存在：{in_path}", file=sys.stderr)
        return 2
    bbox = None
    if args.box:
        bbox = tuple(int(v) for v in args.box.split(","))
    rep = _process_one(in_path, out_path, args.strategy, args.ratio, bbox)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep.get("ok") else 4


def _ai_image_cmd() -> list[str]:
    return [sys.executable, str(ROOT / "skills" / "shared" / "scripts" / "ai_image.py")]


def cmd_gen(args) -> int:
    out_path = Path(args.output)
    if not str(out_path).startswith("outputs/"):
        print("输出必须位于 outputs/<主题名>/ 下", file=sys.stderr)
        return 2
    stem = out_path.stem
    raw_dir = out_path.parent if out_path.parent.name == "assets" else out_path.parent / "assets"
    raw_path = raw_dir / f"{stem}_raw{out_path.suffix}"

    prompt = args.prompt
    if not args.no_clean_corner:
        prompt = f"{prompt}\n\n{CLEAN_CORNER_RULE}"

    cmd = _ai_image_cmd() + [
        "text2img", "--prompt", prompt, "--size", args.size, "--n", "1",
        "--output", str(raw_path),
    ]
    if args.preset:
        env = read_env_file(args.env_file)
        pre = PRESETS[args.preset]
        key = env.get(pre["key_env"]) or os.environ.get(pre["key_env"], "")
        if not key:
            print(f"预设 {args.preset} 缺少 key（.env 里的 {pre['key_env']}）", file=sys.stderr)
            return 2
        os.environ["IMG_BASE_URL"] = pre["base"]
        os.environ["IMG_MODEL"] = pre["model"]
        os.environ["IMG_API_KEY"] = key
        print(f"[预设] {args.preset} → {pre['model']} @ {pre['base']}")
    if args.model:
        os.environ["IMG_MODEL"] = args.model
    if args.base_url:
        os.environ["IMG_BASE_URL"] = args.base_url
    if args.env_file:
        cmd += ["--env-file", args.env_file]
    print("== 生成原图 ==")
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        print("生成失败，未产出成品。", file=sys.stderr)
        return rc
    print(f"原始图（已保留）：{raw_path}")

    print("== 去水印 ==")
    rep = _process_one(raw_path, out_path, args.strategy, args.ratio)
    rep["raw_kept"] = str(raw_path)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"\n成品：{out_path}  {rep['size_out'][0]}x{rep['size_out'][1]}  "
          f"({'✅ 无标识' if rep.get('ok') else '⚠️ 仍有疑似标识，请人工看一眼'})")
    return 0 if rep.get("ok") else 4


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI 生图去水印（detect / cover / process / gen）")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="只定位右下角生图标识")
    d.add_argument("-i", "--input", required=True)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_detect)

    pr = sub.add_parser("process", help="对已有图去水印（默认背景插值，保持原尺寸）")
    pr.add_argument("-i", "--input", required=True)
    pr.add_argument("-o", "--output", required=True)
    pr.add_argument("--strategy", choices=["auto", "cover", "crop"], default=DEFAULT_STRATEGY)
    pr.add_argument("--ratio", default="9:16", help="crop 策略下的目标画幅，默认 9:16")
    pr.add_argument("--box", help="手工指定标识框 x0,y0,x1,y1")
    pr.set_defaults(func=cmd_process)

    g = sub.add_parser("gen", help="生成 + 自动去水印（保留原始图）")
    g.add_argument("--prompt", required=True)
    g.add_argument("--size", default="1008x2016",
                   help="生成尺寸，建议比目标画幅高约 12%%（默认 1008x2016 → 裁成 9:16）")
    g.add_argument("-o", "--output", required=True, help="成品路径，须在 outputs/<主题>/ 下")
    g.add_argument("--ratio", default="9:16", help="成品画幅，默认 9:16")
    g.add_argument("--strategy", choices=["auto", "cover", "crop"], default="auto",
                   help="auto（默认：底边干净就裁，否则背景插值）/ crop（强制裁）/ cover（不裁）")
    g.add_argument("--no-clean-corner", action="store_true",
                   help="不在提示词里追加「右下角留空」约定")
    g.add_argument("--preset", choices=sorted(PRESETS),
                   help="生图服务预设：qwen（默认推荐，便宜）/ glm（中文更稳）")
    g.add_argument("--model", help="覆盖 .env 的图片模型")
    g.add_argument("--base-url", help="覆盖 .env 的图片服务地址")
    g.add_argument("--env-file", help="指定 .env")
    g.set_defaults(func=cmd_gen)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
