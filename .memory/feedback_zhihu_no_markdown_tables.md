---
name: feedback-zhihu-no-markdown-tables
description: 知乎不渲染Markdown表格，改用纯文字列表
metadata:
  type: feedback
---

知乎文章不用 Markdown 表格语法（`| col | col |` / `|---|---|`）。

**Why:** 知乎编辑器（Draft.js）不渲染 Markdown 表格，发布后原样显示为 `|---|---|` 文本，读者看到的是乱码式排版。2026-10-22 实测发现，修改后重新发布修复。

**How to apply:** 写知乎文章时，需要对比/表格内容统一改为纯文字列表格式：
```
**X**：字段A——值；字段B——值
**Y**：字段A——值；字段B——值
```
或用无序列表分项描述。修改现有文章后需重新发布（知乎无原地编辑，只能新发），旧文章需用户手动删除。
