#!/usr/bin/env python3
"""
每天认识一位艺术家 — 每日引擎 / Daily Engine

工作流程：
  1. 按星期几确定今日栏目（columns.json）
  2. 从选题池取下一位待写艺术家（pool.json）
  3. 从开放图源 API 搜索公有领域图片
  4. 下载并压缩图片到 images/
  5. 按 artist-card-template.md 生成草稿到 _drafts/
  6. 输出审核清单，等待人工拍板

用法：
  python scripts/daily_engine.py              # 自动确定今日日期和栏目
  python scripts/daily_engine.py --date 2026-08-04  # 指定日期
  python scripts/daily_engine.py --column masters    # 指定栏目
  python scripts/daily_engine.py --dry-run          # 只输出计划，不下载不生成

依赖：Python 3.11+, requests, Pillow（PIL）
图源优先级：Met Museum Open Access > AIC Public Domain > Wikimedia Commons
"""

import json
import os
import sys
import argparse
import datetime
import urllib.request
import urllib.error
import re
from pathlib import Path

# ── 项目根目录（脚本所在目录的上一级）─────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# ── 路径常量 ────────────────────────────────────────────────────────
COLUMNS_PATH = PROJECT_ROOT / "data" / "columns.json"
POOL_PATH = PROJECT_ROOT / "data" / "pool.json"
TEMPLATE_PATH = PROJECT_ROOT / "_meta" / "artist-card-template.md"
ARTISTS_DIR = PROJECT_ROOT / "artists"
IMAGES_DIR = PROJECT_ROOT / "images"
DRAFTS_DIR = PROJECT_ROOT / "_drafts"
ARTISTS_JSON_PATH = PROJECT_ROOT / "data" / "artists.json"

# ── 图源 API ────────────────────────────────────────────────────────
MET_OBJECT_API = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{objectID}"
MET_SEARCH_API = "https://collectionapi.metmuseum.org/public/collection/v1/search"

AIC_SEARCH_API = "https://api.artic.edu/api/v1/artworks/search"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_today_column(columns_data):
    """根据星期几返回今日栏目名。"""
    weekday = datetime.date.today().weekday()  # 0=Mon ... 6=Sun
    for col_name, col_info in columns_data["columns"].items():
        if col_info["weekday"] == weekday:
            return col_name, col_info
    return "wildcard", columns_data["columns"]["wildcard"]


def pick_from_pool(pool_data, column):
    """从选题池取该栏目的第一位 status=queued 的艺术家。返回 (index, artist_dict) 或 None。"""
    if column not in pool_data.get("pools", {}):
        return None, None
    for i, artist in enumerate(pool_data["pools"][column]):
        if artist.get("status") == "queued":
            return i, artist
    return None, None


def search_met_images(artist_name_en, limit=5):
    """
    在 Met Museum API 中搜索艺术家的公有领域作品。
    返回匹配列表，每项含 objectID, title, primaryImageURL, date, isPublicDomain。
    """
    results = []
    try:
        query = f'{artist_name_en}'
        url = f"{MET_SEARCH_API}?q={urllib.parse.quote(query)}&hasImages=true&isPublicDomain=true&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "OneArtistADay/1.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.load(resp)
        for obj_id in data.get("objectIDs", [])[:limit]:
            obj_url = MET_OBJECT_API.format(objectID=obj_id)
            req2 = urllib.request.Request(obj_url, headers={"User-Agent": "OneArtistADay/1.0"})
            obj_data = json.load(urllib.request.urlopen(req2, timeout=30))
            if obj_data.get("primaryImage") and obj_data.get("isPublicDomain"):
                results.append({
                    "objectID": obj_id,
                    "title": obj_data.get("title", ""),
                    "date": obj_data.get("objectDate", ""),
                    "primaryImage": obj_data["primaryImage"],
                    "medium": obj_data.get("medium", ""),
                })
    except Exception as e:
        print(f"[WARN] Met API 搜索失败: {e}")
    return results


def download_and_compress_image(url, output_path, max_side=1600, quality=85):
    """下载图片并用 PIL 压缩到 max_side × max_side 以内。"""
    from PIL import Image, ImageOps
    from io import BytesIO

    req = urllib.request.Request(url, headers={"User-Agent": "OneArtistADay/1.0"})
    data = urllib.request.urlopen(req, timeout=90).read()
    im = Image.open(BytesIO(data))
    im = ImageOps.exif_transpose(im)

    w, h = im.size
    longest = max(w, h)
    if longest > max_side:
        scale = max_side / longest
        im = im.resize((int(w * scale + 0.5), int(h * scale + 0.5)), Image.LANCZOS)

    im.convert("RGB").save(str(output_path), "JPEG", quality=quality, optimize=True)
    size_kb = os.path.getsize(output_path) / 1024
    print(f"  ✅ 图片已保存: {output_path.name} ({im.size[0]}×{im.size[1]}, {size_kb:.0f} KB)")
    return str(output_path)


def generate_draft(artist, column_info, image_path, met_match=None):
    """
    按 template 生成草稿 markdown。
    注意：这是骨架草稿，正文内容需要 AI/人工填充。
    只生成 frontmatter 和结构框架。
    """
    slug = slugify(artist["name_en"])
    date_str = datetime.date.today().isoformat()
    year, month = date_str[:4], date_str[5:7]

    # 构造 frontmatter
    frontmatter = f"""---
date: {date_str}
column: {column_info["name_en"].lower()}
slug: {slug}
name_zh: {artist["name_zh"]}
name_en: {artist["name_en"]}
name_native: {artist.get("name_native", "")}
years: {artist["years"]}
birthplace: {artist.get("birthplace", "")}
country: {artist["country"]}
era: _TODO_
movements: [_TODO_]
mediums: [_TODO_]
gender: _TODO_
hero_image: images/{Path(image_path).name}
hero_caption: 《_TODO_》，_年份_
image_license: Public Domain
image_source: _TODO_
image_source_url: {met_match and met_match.get("primaryImage") or "_TODO_"}
verified: false
---"""

    body = f"""# {artist['name_zh']} · {artist['name_en']}

![hero_caption](../../images/{Path(image_path).name})

<sub>《_TODO_》 · _收藏地_ · _图片来源与授权_</sub>

## 一句话

> _（一句有观点的定位。不能替换成任何其他艺术家也成立。）_

## 为什么是他

_（三到五句。必须包含具体的年份、地点、事件。）_

## 看这一张

**《_TODO_》（_年份_）** — _收藏地_

_（告诉读者具体看哪里：画面上的具体位置或元素。）_

## 一个细节

_（一个别处不容易读到的具体事实。没有就留空，不要凑。）_

## 他说过

> _引语原文（如有母语原文附一行）_

— _出处_

## 延伸

- **同代人**：
- **影响了**：
- **馆藏**：
- **本站相关**：

---

<!-- 写作纪律自查（提交前逐条通过）
1. 「一句话」不可替换 → 能替换就是废话
2. 「为什么是他」含 年份+地点+事件 ≥2 要素
3. 「看这一张」指出画面具体位置/元素
4. 「一个细节」宁可空着也不编
5. 禁用词：令人叹为观止 / 不禁让人 / 在那个时代 / 深远影响 / 无与伦比 /
   他的一生充满传奇 / 这不仅仅是一幅画
6. 图片只用 PD 或 CC0
7. 生卒年/作品年份/收藏地交叉核对两个独立来源
-->
"""

    draft_content = frontmatter + "\n" + body

    # 保存到 _drafts/
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = DRAFTS_DIR / f"{date_str}-{slug}.md"
    save_json is not applicable here; use open
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(draft_content)

    print(f"  📝 草稿已生成: {draft_path}")
    return str(draft_path)


def slugify(name):
    """将名字转为 URL-safe 的 slug。"""
    name = name.lower()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name


def main():
    parser = argparse.ArgumentParser(description="每天认识一位艺术家 — 每日引擎")
    parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)", default=None)
    parser.add_argument("--column", help="指定栏目名 (masters/her/east/...)", default=None)
    parser.add_argument("--dry-run", action="store_true", help="只输出计划，不下载不生成")
    args = parser.parse_args()

    # 加载数据
    columns_data = load_json(COLUMNS_PATH)
    pool_data = load_json(POOL_PATH)

    # 确定日期和栏目
    if args.date:
        target_date = args.date
    else:
        target_date = datetime.date.today().isoformat()

    if args.column:
        column_name = args.column
        column_info = columns_data["columns"].get(column_name)
        if not column_info:
            print(f"❌ 栏目 '{args.column}' 不存在。可用栏目: {list(columns_data['columns'].keys())}")
            sys.exit(1)
    else:
        column_name, column_info = get_today_column(columns_data)

    print(f"📅 日期: {target_date}")
    print(f"📂 栏目: {column_info['name_zh']} ({column_name})")
    print()

    # 取选题
    idx, artist = pick_from_pool(pool_data, column_name)
    if not artist:
        print(f"⚠️  栏目 '{column_name}' 的选题池已空！请补充 pool.json。")
        sys.exit(0)

    print(f"🎨 今日艺术家: {artist['name_zh']} ({artist['name_en']})")
    print(f"   生卒年: {artist['years']} | 国别: {artist['country']}")
    print()

    if args.dry_run:
        print("[DRY RUN] 计划完成。加上 --download 可执行实际下载和生成。")
        return

    # 搜索图片
    print("🔍 搜索公有领域图片...")
    matches = search_met_images(artist["name_en"], limit=5)

    if not matches:
        print(f"⚠️  未在 Met Museum 找到 {artist['name_en']} 的公有领域图片。")
        print("   建议：手动搜索 Wikimedia Commons 或 AIC，然后填写图片信息。")
        # 仍然生成无图草稿
        slug = slugify(artist["name_en"])
        dummy_img_path = IMAGES_DIR / f"{target_date}-{slug}.jpg"
        draft_path = generate_draft(artist, column_info, str(dummy_img_path), None)
        print("\n✅ 完成！请检查草稿并补充内容:")
        print(f"   草稿: {draft_path}")
        return

    print(f"  找到 {len(matches)} 张候选图片:")
    for i, m in enumerate(matches):
        print(f"    [{i+1}] {m['title']} ({m['date']}) - {m['medium'][:40]}")

    # 使用第一张匹配
    best = matches[0]
    print(f"\n  选择: [{best['title']}] (objectID: {best['objectID']})")

    # 下载图片
    slug = slugify(artist["name_en"])
    img_filename = f"{target_date}-{slug}.jpg"
    img_path = IMAGES_DIR / img_filename
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    download_and_compress_image(best["primaryImage"], img_path)

    # 生成草稿
    draft_path = generate_draft(artist, column_info, str(img_path), best)

    # 输出审核清单
    print()
    print("=" * 50)
    print("📋 审核清单 / REVIEW CHECKLIST")
    print("=" * 50)
    print(f"""  1. 打开草稿: {draft_path}
  2. 填充 TODO 字段（frontmatter + 正文）
  3. 验证生卒年（至少两个独立来源）
  4. 验证作品年份和收藏地
  5. 通过写作纪律 7 条自查
  6. 将草稿移至 artists/{target_date[:4]}/{target_date[5:7]}/
  7. 更新 pool.json（status: queued → published）
  8. 更新 indexes/ 和 data/artists.json
  9. 提交并推送
""")
    print("💡 提示: 草稿中的 TODO 标记是需要你用史料和判断力填充的部分。")
    print("   引擎负责素材准备（选题+图片），你负责写作和事实核查。")


if __name__ == "__main__":
    # 确保 urllib.parse 可用
    import urllib.parse
    main()
