# 🎨 One Artist A Day / 每天认识一位艺术家

> 每天一张艺术家卡片，一年认识 365 位。中文写作，开放版权图片，七栏轮转。

[![License: CC0](https://img.shields.io/badge/License-CC0-blue.svg)](LICENSE)

---

## 这是什么

一个日更型的艺术史知识库。每天发布一位艺术家的中文介绍卡片，包含：

- **一句话定位** — 不是维基百科式的履历罗列，是这个人在艺术史上不可替代的位置
- **为什么是他/她** — 带有具体年份、地点、事件的转折叙述
- **看这一张** — 一幅公有领域名作的细读（看哪里、为什么）
- **一个细节** — 别处不容易读到的具体事实（宁可空着也不编）
- **他说过** — 真实引语（没有就不放）

所有图片均来自 **公有领域（Public Domain）** 或 **CC0** 授权的博物馆馆藏。

## 每周七栏

项目按星期几分配七个固定栏目：

| 星期 | 栏目 | 说明 |
|------|------|------|
| 周一 | **巨匠 Masters** | 绕不开的大师，也要写出别人没写过的那一面 |
| 周二 | **她 Her** | 被艺术史除名的女性——作品本身站得住才收录 |
| 周三 | **东方 East** | 中国、日本、韩国、印度、伊斯兰世界 |
| 周四 | **遗珠 Overlooked** | 同代人里最好的那个，却没进教科书 |
| 周五 | **现代 Modern** | 20 世纪至今（多数在版权期内，只链接不存图） |
| 周六 | **匠人 Makers** | 建筑师、雕塑家、摄影师、版画师、织物与陶瓷 |
| 周日 | **点名 Wildcard** | 读者提名，开 issue 即可参与 |

详见 [data/columns.json](data/columns.json)。

## 卡片示例

今天的样板卡：

- [🎩 伦勃朗·范·莱恩（巨匠）](artists/2026/08/2026-08-03-rembrandt.md)
- [👑 阿尔泰米西娅·真蒂莱斯基（她）](artists/2026/08/2026-08-03-artemisia-gentileschi.md)
- [🌊 葛饰北斋（东方）](artists/2026/08/2026-08-03-hokusai.md)

每张卡遵循统一模板：[_meta/artist-card-template.md](_meta/artist-card-template.md)。

## 目录结构

```
├── artists/
│   └── YYYY/MM/
│       ├── YYYY-MM-DD-slug.md      # 每日卡片
│       └── ...
├── images/
│   └── YYYY-MM-DD-slug.jpg         # 公有领域主图（最长边 ≤1600px）
├── data/
│   ├── columns.json                # 七栏定义
│   ├── pool.json                   # 选题池
│   └── artists.json                # 结构化数据（供外部消费）
├── indexes/
│   ├── by-date.md                  # 按日期索引
│   ├── by-era.md                   # 按时代索引
│   ├── by-movement.md              # 按流派索引
│   └── by-country.md               # 按国家/地区索引（含女性视角标注）
├── _meta/
│   └── artist-card-template.md      # 卡片模板 + 写作纪律
├── _drafts/                        # 待审核草稿
├── scripts/                        # 自动化脚本
├── README.md
├── LICENSE                         # CC0 1.0 Universal
└── CONTRIBUTING.md                 # 贡献指南
```

## 图片来源

| 来源 | 授权 | API |
|------|------|-----|
| The Metropolitan Museum of Art | Public Domain / CC0 | [collectionapi.metmuseum.org](https://collectionapi.metmuseum.org/) |
| Art Institute of Chicago | Public Domain | [api.artic.edu](https://api.artic.edu/) |
| Wikimedia Commons | Various | [commons.wikimedia.org](https://commons.wikimedia.org) |

**红线：1955 年后去世的艺术家的作品一律不存图，只提供链接（`image_license: link-only`）。**

## 自动化 / Automation

本项目已实现**每日自动发卡**，流水线为：

```
定时触发（每天 08:30）→ 按星期选栏目 → 选题池取人 → 搜公有领域图（Met → AIC）
→ LLM 撰写正文（写作纪律 7 条 + 禁用词/标点自检）→ 更新 pool / indexes / artists.json
→ commit + push
```

- **本地定时**：launchd 任务 `com.local.one-artist-a-day`（每天 08:30，通过本机 relay 调用 LLM）
- **云端定时（可选）**：[.github/workflows/daily.yml](.github/workflows/daily.yml) 每天 UTC 00:30 触发，需在仓库 Secrets 配置 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（任何 OpenAI 兼容 API）
- **手动补发**：`python scripts/auto_publish.py --date YYYY-MM-DD --push`
- 生成的卡片 `verified: false`，事实信息（生卒年/作品年份/收藏地）需人工核对后改为 `true`

## 与艺术史网站的衔接

本仓库是 [艺术史知识库](https://github.com/gengyueworks/art-history-kb) 的**每日更新前端**。

- 仓库中的 `data/artists.json` 可被艺术史网站直接消费为新的艺术家卡
- 每张卡的 markdown 正文可导入知识库的"作品卡"或"艺术家卡"
- 长期目标：365 天后，本仓库的内容回流充实到艺术史知识库的 889 张现有卡中

## 如何贡献

1. **提名艺术家** — 开 issue，标注栏目（`her` / `east` / `overlooked` 等）
2. **写一张卡** — Fork → 按 [_meta/artist-card-template.md](_meta/artist-card-template.md) 模板写 → 提 PR
3. **纠错** — 发现事实错误直接提 issue 或 PR，附来源链接
4. **翻译** — 欢迎将卡片翻译成其他语言（在同级目录创建 `.en.md` / `.ja.md` 等）

详细规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 写作纪律

每张卡提交前必须通过以下检查（完整清单见模板末尾注释）：

1. 「一句话」不能替换成任何其他艺术家也成立 —— 能替换就是废话
2. 「为什么是他」必须有年份、地点、事件至少两个要素
3. 「看这一张」必须指出画面上的具体位置或元素
4. 「一个细节」宁可空着，也不要编；所有事实必须能追到来源
5. **禁用词表**：令人叹为观止 / 不禁让人 / 在那个时代 / 深远影响 / 无与伦比 / 他的一生充满传奇 / 这不仅仅是一幅画
6. 图片只用公有领域或 CC0
7. 生卒年、作品年份、收藏地必须交叉核对两个独立来源

## 许可证

本项目内容采用 [CC0 1.0 Universal](LICENSE)（公共领域 dedication）。你可以自由复制、修改、分发、商用，无需署名。

图片各自遵循其原始授权（见每张卡的 frontmatter `image_license` 字段）。

---

*每天一张，一年之后你会比大多数美院毕业生认识的艺术家还多。*
