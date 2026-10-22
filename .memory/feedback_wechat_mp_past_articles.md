---
name: feedback-wechat-mp-past-articles
description: 公众号往期推荐必须填真实文章链接，不留占位符
metadata:
  type: feedback
---

公众号文章的"往期推荐"栏目必须填入真实已发布文章的链接，不能留占位符（如 `【往期推荐占位 1】`）。

**Why:** 2026-10-22 初稿把占位符直接带进了草稿箱，用户发现后要求修正。占位符出现在对外文章中是明显的制作失误。

**How to apply:**
1. 发布前用 `weixin_mp_stats.py stats` 拉取已发布文章列表
2. 从中选 2-3 篇话题相关的，用真实标题 + `https://mp.weixin.qq.com/s/<short_id>` 链接填入
3. 发布前扫描文章全文，确认没有含"占位"/"TODO"/"待填"/"待补充"等字样的行
