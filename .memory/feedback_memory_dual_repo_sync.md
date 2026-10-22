---
name: feedback_memory_dual_repo_sync
description: 新经验教训必须同时写入当前 repo .memory 和 opc_multi_platform .memory，并 push to main
metadata:
  node_type: memory
  type: feedback
  originSessionId: 5749b121-7690-424f-8d4d-c6934d88fc21
  modified: 2026-10-22T12:53:41.528Z
---

每轮产出新经验教训时，必须同时写入两处：

1. 当前 workspace memory（结构化 feedback/project/reference 文件）
2. `/home/jin/works/opc_multi_platform/.memory/` 对应日期文件（`YYYY-MM-DD.md`，追加到「经验教训」段落）

写完后 `cd /home/jin/works/opc_multi_platform && git add .memory/ && git commit -m "记忆：<简述>" && git push`

**Why:** 用户要求跨项目复用经验，opc_multi_platform 是多平台发布主仓，记忆需在那里留痕并上库。

**How to apply:** 每次准备写 feedback/project 记忆时，额外在 opc_multi_platform 的当天日期文件里追加相同内容，再 push。
