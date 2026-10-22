---
name: feedback-kuaishou-max-4-tags
description: 快手话题标签上限 4 个，超过会导致发布静默失败（读回未核实）
metadata:
  type: feedback
---

快手话题标签最多 **4 个**，超过后脚本报「读回未核实」但不报明显错误，视频可能实际未发出。

**Why:** 2026-10-22 NBA-AI裁判发布时传了 5 个标签 `#NBA #AI科技 #人工智能 #科技知识 #涨知识`，两次读回均未见作品；改为 4 个后一次通过。

**How to apply:** `--tags` 参数控制在 4 个以内（含 4）；超过时删低相关性标签。
