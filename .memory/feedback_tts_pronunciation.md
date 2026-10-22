---
name: tts-pronunciation-rules
description: TTS发音规则：年份数字读法、特殊词汇读法，适用于所有视频TTS生成
metadata:
  node_type: memory
  type: feedback
  originSessionId: 71380f79-abcb-44cb-98fd-ea6d56251c07
  modified: 2026-10-21T15:27:12.605Z
---

## 年份数字读法（全局规则，2026-10-21确认）

- **2024年** → TTS文本写作 "二零二四年"（不写"两千二十四年"）
- **2024赛季** → TTS文本写作 "二零二四赛季"
- **2023-24赛季** → TTS文本写作 "二零二三到二四赛季"
- 同理，其他年份 YYYY年/YYYY赛季 均读作 "二零二X年/赛季"

**Why:** 中文TTS默认将"2024"读作"两千零二十四"，用户要求读作"二零二四"。

**How to apply:** 生成TTS的句子文本时，凡出现年份数字，在TTS_OVERRIDES中替换为中文数字读法。字幕显示可保留"2024年"原文，只有TTS输入文本需替换。

---

## Hawk-Eye 读法（全局规则，2026-10-21确认）

- **Hawk-Eye** → TTS文本写作 "Hawk Eye"（去掉中间的连字符 `-`）

**Why:** 中文TTS会把 `-` 作为停顿或读出来，导致发音不自然。用户要求读作 "Hawk Eye"。

**How to apply:** TTS_OVERRIDES中将"Hawk-Eye"替换为"Hawk Eye"。字幕显示可保留"Hawk-Eye"。
