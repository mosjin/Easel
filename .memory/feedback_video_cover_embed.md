---
name: feedback-video-cover-embed
description: 自动发布视频时在视频开头嵌入封面静帧（约0.5秒），无需用户手动选封面
metadata:
  node_type: memory
  type: feedback
  created: 2026-10-23
  originSessionId: 6a993a87-48cf-499d-ab7e-cc24d52fedc6
  modified: 2026-10-23T03:57:47.146Z
---

自动发布视频时，用户没有机会手动设置封面——必须把封面图嵌入视频开头（约0.5秒静帧）。

**Why:** 技能自动上传视频文件时跳过了封面选择步骤；平台会截取视频第一帧作为缩略图，所以把封面图嵌进开头是唯一可靠的解法。（2026-10-23 用户全局规则）

**How to apply:**
1. 视频合成完后，用 ffmpeg 生成 0.5s 封面片段（静音轨 + 封面图，参数与主视频一致）：
   ```bash
   ffmpeg -loop 1 -i cover_vertical.png \
     -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
     -t 0.5 -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest \
     cover_clip.mp4
   ```
2. concat 拼接到主视频前面（重编码确保参数一致）：
   ```bash
   ffmpeg -f concat -safe 0 -i list.txt \
     -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 -ac 2 \
     final_with_cover.mp4
   ```
3. 发布时使用 `final_with_cover.mp4`，不用原始 `final.mp4`。
4. 发布清单里所有视频平台的文件路径统一改为带封面版本。

封面图选择：视频号/抖音/快手用竖版（9:16），B站可用横版（16:9）。
