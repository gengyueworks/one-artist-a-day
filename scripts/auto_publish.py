#!/usr/bin/env python3
"""
每天认识一位艺术家 — 全自动发布引擎 / Auto Publisher

工作流程：
  1. 按星期几确定今日栏目（columns.json）
  2. 从选题池取下一位待写艺术家（pool.json, status=queued）
  3. 从开放图源 API 搜索公有领域图片（Met → AIC），1955 年后去世者 link-only
  4. 调用 LLM（OpenAI 兼容 API）生成完整卡正文，遵循写作纪律 7 条
  5. 自检（禁用词 / 中文标点 / 必填字段），失败自动重试一次
  6. 写卡到 artists/YYYY/MM/，更新 pool.json / indexes/ / data/artists.json
  7. commit + push

用法：
  python scripts/auto_publish.py                  # 自动确定今日日期和栏目
  python scripts/auto_publish.py --date 2026-08-07   # 指定日期（测试用）
  python scripts/auto_publish.py --dry-run        # 只输出计划
  python scripts/auto_publish.py --no-commit      # 生成但不提交（本地预览）

环境变量（GitHub Actions Secrets 同名）：
  LLM_API_KEY   必填
  LLM_BASE_URL  默认 https://api.deepseek.com
  LLM_MODEL     默认 deepseek-chat

依赖：Python 3.9+, requests, Pillow
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

COLUMNS_PATH = PROJECT_ROOT / "data" / "columns.json"
POOL_PATH = PROJECT_ROOT / "data" / "pool.json"
ARTISTS_JSON_PATH = PROJECT_ROOT / "data" / "artists.json"
ARTISTS_DIR = PROJECT_ROOT / "artists"
IMAGES_DIR = PROJECT_ROOT / "images"
INDEXES_DIR = PROJECT_ROOT / "indexes"

MET_SEARCH_API = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT_API = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{objectID}"
AIC_SEARCH_API = "https://api.artic.edu/api/v1/artworks/search"
AIC_IMAGE_IIIF = "https://www.artic.edu/iiif/2/{image_id}/full/!1600,1600/0/default.jpg"

COLUMN_EMOJI = {"masters": "🎩", "her": "👑", "east": "🌊", "overlooked": "🔦", "modern": "🖥️", "makers": "🛠️", "wildcard": "🎲"}
GENDER_SYM = {"m": "♂", "f": "♀", "other": "⚧"}

COUNTRY_CODE = {
    "荷兰": "NL", "意大利": "IT", "日本": "JP", "法国": "FR", "英国": "GB",
    "德国": "DE", "西班牙": "ES", "比利时": "BE", "俄罗斯": "RU", "美国": "US",
    "墨西哥": "MX", "韩国": "KR", "中国": "CN", "挪威": "NO", "丹麦": "DK",
    "奥地利": "AT", "葡萄牙": "PT", "瑞士": "CH", "波兰": "PL", "捷克": "CZ",
    "瑞典": "SE", "芬兰": "FI", "爱尔兰": "IE", "希腊": "GR", "印度": "IN",
    "伊朗": "IR", "土耳其": "TR", "埃及": "EG", "巴西": "BR", "阿根廷": "AR",
    "澳大利亚": "AU", "加拿大": "CA", "罗马尼亚": "RO", "匈牙利": "HU",
    "乌克兰": "UA", "格鲁吉亚": "GE", "亚美尼亚": "AM", "阿塞拜疆": "AZ",
    "泰国": "TH", "越南": "VN", "印尼": "ID", "马来西亚": "MY", "蒙古": "MN",
}

BANNED_PHRASES = [
    "令人叹为观止", "不禁让人", "在那个时代", "深远影响", "无与伦比",
    "他的一生充满传奇", "这不仅仅是一幅画", "不朽的杰作", "艺术史上最伟大的",
    "叹为观止", "熠熠生辉", "匠心独运", "巧夺天工",
]

SYSTEM_PROMPT = """你是艺术史写作研究员，为中文读者撰写"每天认识一位艺术家"卡片。写作纪律（每条都是硬性要求）：

1. 「一句话」是句有观点的定位，不能替换成任何其他艺术家也成立——能替换就是废话。
2. 「为什么是他」必须包含具体的年份、地点、事件三要素中至少两个，写一个具体的转折时刻。
3. 「看这一张」必须指出画面上的具体位置或具体元素（如"左下角那只手""光从右侧来"），禁止只说"色彩运用大胆"这类空话。
4. 「一个细节」只写别处不容易读到的具体事实（账本上的一笔钱、一封信里的一句话、颜料的来源）；不确定就空着，不要编。
5. 「他说过」只写真引语，附出处；没有可靠引语就空着。
6. 禁止出现以下词语（出现即重写）：令人叹为观止、不禁让人、在那个时代、深远影响、无与伦比、他的一生充满传奇、这不仅仅是一幅画、不朽的杰作、艺术史上最伟大的、叹为观止、熠熠生辉、匠心独运、巧夺天工。
7. 生卒年、作品年份、收藏地必须依据你的知识交叉核对，不确定的年份写"约"。
8. 全文用中文标点（，。！？；：""''），禁止英文标点混入中文句。
9. 语言风格：口语化、直接、有节奏感，像说话一样写；不用"然而""因此""综上所述"；段落要短，一段不超过 3-4 句。
10. JSON 硬性规则：所有字符串值内部禁止出现未转义的英文双引号字符（"）。正文中需要引号时一律用中文引号“”或『』，不得用 ASCII 双引号。这是合法 JSON 的前提，违反即输出无效。

只输出一个 JSON 对象（不要输出任何其他文字），结构严格如下：
{
  "frontmatter": {
    "slug": "英文小写连字符",
    "name_zh": "中文名",
    "name_en": "Original Name",
    "name_native": "母语原名（非拉丁语种填，否则空字符串）",
    "years": "YYYY–YYYY（用短横线 –）",
    "birthplace": "国家 城市",
    "country": "国家（中文）",
    "country_code": "ISO 两位码",
    "era": "时代（如 巴洛克 / 荷兰黄金时代）",
    "movements": ["流派1", "流派2"],
    "mediums": ["媒介1", "媒介2"],
    "gender": "m 或 f",
    "hero_caption": "《作品名》，年份",
    "image_license": "Public Domain",
    "image_source": "The Met (Open Access) 或 Art Institute of Chicago 或 Link Only",
    "image_source_url": "作品页面 URL"
  },
  "one_liner": "一句话定位",
  "why": "为什么是他（三到五句，含年份+地点+事件）",
  "look_at": {"title": "作品名", "year": "年份", "collection": "收藏地", "detail": "具体看哪里（两句到三句）"},
  "fun_fact": "一个细节，没有则空字符串",
  "quote": {"text": "引语中文翻译或原文", "original": "母语原文（没有则空字符串）", "source": "出处"},
  "extended": {"contemporaries": "同代艺术家名", "influenced": "影响的人或运动", "collections": "主要馆藏机构", "related": "相关流派或主题"}
}
"""


def log(msg):
    print(msg, flush=True)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"  ✅ 已保存: {path.name}")


def get_column_for_date(columns_data, target_date):
    weekday = datetime.date.fromisoformat(target_date).weekday()
    for col_name, col_info in columns_data["columns"].items():
        if col_info["weekday"] == weekday:
            return col_name, col_info
    return "wildcard", columns_data["columns"]["wildcard"]


def pick_from_pool(pool_data, column):
    artists = pool_data["pools"].get(column, [])
    for i, artist in enumerate(artists):
        if artist.get("status") == "queued":
            return i, artist
    return None, None


def parse_death_year(years_str):
    """从 '1452-1519' / '1593–c.1653' 解析死亡年份。"""
    years_str = years_str.replace("–", "-").replace("—", "-")
    m = re.findall(r"(?:c\.)?(\d{3,4})", years_str)
    if not m:
        return None
    return int(m[-1])


def normalize_name(s):
    """归一化艺术家名：小写 + 去重音，用于模糊匹配。"""
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def name_matches(artist_name_en, candidate):
    """候选作者名是否匹配目标艺术家。按姓氏/全名两种方式比对，防模糊搜索串台。"""
    target = normalize_name(artist_name_en)
    cand = normalize_name(candidate)
    if not cand:
        return False
    if target in cand or cand in target:
        return True
    t_parts = [p for p in target.split() if len(p) > 2]
    return bool(t_parts) and all(p in cand for p in t_parts)


def search_met_images(artist_name_en, limit=10):
    results = []
    try:
        url = f"{MET_SEARCH_API}?q={urllib.parse.quote(artist_name_en)}&hasImages=true&isPublicDomain=true&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "OneArtistADay/1.0"})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        for obj_id in (data.get("objectIDs") or [])[:limit]:
            try:
                obj_url = MET_OBJECT_API.format(objectID=obj_id)
                req2 = urllib.request.Request(obj_url, headers={"User-Agent": "OneArtistADay/1.0"})
                obj = json.load(urllib.request.urlopen(req2, timeout=30))
                artist_field = obj.get("artistDisplayName", "") or ""
                if not (obj.get("primaryImage") and obj.get("isPublicDomain")):
                    continue
                if not name_matches(artist_name_en, artist_field):
                    continue
                results.append({
                    "title": obj.get("title", ""),
                    "date": obj.get("objectDate", ""),
                    "medium": obj.get("medium", ""),
                    "primaryImage": obj["primaryImage"],
                    "image_source": "The Met (Open Access)",
                    "image_source_url": f"https://www.metmuseum.org/art/collection/search/{obj_id}",
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  [WARN] Met API 搜索失败: {e}")
    return results


def search_aic_images(artist_name_en, limit=10):
    results = []
    try:
        url = f"{AIC_SEARCH_API}?q={urllib.parse.quote(artist_name_en)}&limit={limit}&fields=id,title,date_display,medium_display,image_id,is_public_domain,artist_title"
        req = urllib.request.Request(url, headers={"User-Agent": "OneArtistADay/1.0"})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        for item in (data.get("data") or []):
            if not (item.get("is_public_domain") and item.get("image_id")):
                continue
            if not name_matches(artist_name_en, item.get("artist_title", "")):
                continue
            results.append({
                "title": item.get("title", ""),
                "date": item.get("date_display", ""),
                "medium": item.get("medium_display", ""),
                "primaryImage": AIC_IMAGE_IIIF.format(image_id=item["image_id"]),
                "image_source": "Art Institute of Chicago",
                "image_source_url": f"https://www.artic.edu/artworks/{item['id']}",
            })
    except Exception as e:
        log(f"  [WARN] AIC API 搜索失败: {e}")
    return results


def download_and_compress_image(url, output_path, max_side=1600, quality=85):
    try:
        from PIL import Image, ImageOps
        from io import BytesIO
    except ImportError:
        log("  [ERROR] 需要 Pillow: pip install pillow")
        sys.exit(1)

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
    log(f"  ✅ 图片已保存: {output_path.name} ({im.size[0]}×{im.size[1]}, {size_kb:.0f} KB)")


def extract_json(content):
    """从 LLM 输出中提取 JSON：先剥代码块围栏，再取首尾大括号之间的内容。"""
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content.strip())
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in response")
    content = content[start : end + 1]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 兜底修复：模型常在中文内容里写出未转义的 ASCII 引号，
        # 将"含中文内容的成对引号"整体替换为中文引号
        def swap(m):
            inner = m.group(1).replace('"', "“")
            return "“" + inner + "”"
        fixed = re.sub(r'"([^"]*[\u4e00-\u9fff][^"]*)"', swap, content)
        return json.loads(fixed)


def call_llm(prompt, api_key, base_url, model, retries=2):
    """调用 OpenAI 兼容 chat completions，要求 JSON 输出。"""
    import urllib.error

    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "max_tokens": 4000,
    }
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "OneArtistADay/1.0",
                },
            )
            resp = json.load(urllib.request.urlopen(req, timeout=300))
            content = resp["choices"][0]["message"]["content"]
            return extract_json(content)
        except Exception as e:
            log(f"  [WARN] LLM 调用第 {attempt + 1} 次失败: {e}")
            if attempt < retries:
                time.sleep(8 * (attempt + 1))
    return None


def build_llm_prompt(artist, column_info, image, target_date):
    img_desc = ""
    if image:
        img_desc = (
            f"\n候选作品图片信息（用于「看这一张」和 hero_caption，必须采用这张作品）：\n"
            f"- 作品名: {image['title']}\n- 年代: {image['date']}\n- 媒介: {image['medium']}\n"
            f"- 来源: {image['image_source']}\n- 作品页: {image['image_source_url']}\n"
        )
    elif image is None:
        img_desc = "\n该艺术家作品在公有领域图源中未找到，输出 hero_caption 为《代表作品名》，年份，image_license 用 Link Only。\n"
    else:
        img_desc = "\n该艺术家 1955 年后去世，版权期内：不存图，image_license 用 Link Only，image_source_url 给出作品链接，不要编造馆藏。\n"

    return f"""请为以下艺术家撰写一张"每天认识一位艺术家"卡片（日期 {target_date}）。

艺术家档案：
- 中文名: {artist['name_zh']}
- 英文名: {artist['name_en']}
- 生卒: {artist['years']}
- 国别: {artist['country']}
- 今日栏目: {column_info['name_zh']}（{column_info['name_en']}）— {column_info['brief']}
{img_desc}
额外要求：
- frontmatter.slug 用 name_en 转小写连字符。
- 「为什么是他」按本栏目定位写：{column_info['brief']}
- 「看这一张」围绕上述作品展开；若没有候选图，选一幅该艺术家最著名的公有领域作品。
- 「一个细节」没有可靠事实就留空字符串。
- 所有事实基于你的知识，不确定的年份加"约"。"""


def validate_card(data, artist):
    """自检：必填字段、禁用词、标点。返回 (ok, 问题列表)。"""
    problems = []
    fm = data.get("frontmatter", {})
    required = ["name_zh", "name_en", "years", "country", "era", "hero_caption", "image_license", "image_source_url"]
    for k in required:
        if not fm.get(k):
            problems.append(f"frontmatter.{k} 为空")
    if not data.get("one_liner"):
        problems.append("one_liner 为空")
    if not data.get("why"):
        problems.append("why 为空")
    look = data.get("look_at", {})
    if not look.get("detail") or len(str(look.get("detail"))) < 10:
        problems.append("look_at.detail 过短或为空")

    full_text = json.dumps(data, ensure_ascii=False)
    for phrase in BANNED_PHRASES:
        if phrase in full_text:
            problems.append(f"含禁用词: {phrase}")

    # 中文标点检查：中文句内出现英文逗号/句号
    bad_punct = re.findall(r"[\u4e00-\u9fff][,.;!?]", full_text)
    if bad_punct:
        problems.append(f"中文后跟英文标点: {bad_punct[:5]}")

    return (not problems), problems


def build_card_markdown(data, artist, target_date, image_path_rel, image):
    fm = data["frontmatter"]
    look = data["look_at"]
    quote = data.get("quote", {}) or {}
    ext = data.get("extended", {}) or {}

    if image_path_rel:
        hero_line = f"![{fm['hero_caption']}](../../{image_path_rel})"
    elif fm.get("hero_image", "").startswith("http"):
        hero_line = f"[查看作品：{fm['hero_caption']}]({fm['hero_image']})"
    else:
        hero_line = f"![{fm['hero_caption']}]({fm['hero_image']})"
    sub_line = f"《{look.get('title', '')}》，{look.get('year', '')} · {look.get('collection', '')} · {fm.get('image_source', '')} 与授权"

    quote_block = ""
    if quote.get("text"):
        q = f"> {quote['text']}\n"
        if quote.get("original"):
            q += f">\n> _{quote['original']}_\n"
        q += f"\n— {quote.get('source', '')}\n"
        quote_block = q

    fun_fact = data.get("fun_fact", "").strip()
    fun_fact_block = fun_fact if fun_fact else "_（没有可靠细节，暂缺）_"

    related_line = f"- **本站相关**：[{ext.get('related', '')}](../../indexes/by-movement.md)" if ext.get("related") else "- **本站相关**：_（待补充）_"

    body = f"""# {fm['name_zh']} · {fm['name_en']}

{hero_line}

<sub>{sub_line}</sub>

## 一句话

> {data['one_liner']}

## 为什么是他

{data['why']}

## 看这一张

**《{look.get('title', '')}》（{look.get('year', '')}）** — {look.get('collection', '')}

{look.get('detail', '')}

## 一个细节

{fun_fact_block}

## 他说过

{quote_block or "> _（未找到可靠引语，暂缺）_"}

## 延伸

- **同代人**：{ext.get('contemporaries', '')}
- **影响了**：{ext.get('influenced', '')}
- **馆藏**：{ext.get('collections', '')}
- **本站相关**：{ext.get('related', '')}
"""

    frontmatter_lines = []
    for k in ["date", "column", "slug", "name_zh", "name_en", "name_native", "years",
              "birthplace", "country", "era", "movements", "mediums", "gender",
              "hero_image", "hero_caption", "image_license", "image_source", "image_source_url"]:
        if k in ("movements", "mediums"):
            v = fm.get(k, [])
            frontmatter_lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            frontmatter_lines.append(f"{k}: {fm.get(k, '')}")
    frontmatter_lines.append("verified: false")

    frontmatter = "---\n" + "\n".join(frontmatter_lines) + "\n---\n"

    checklist = """<!--
写作纪律自查（本卡由自动引擎生成，verified 为 false，提交前请人工核对生卒年与作品信息）
1. 「一句话」不可替换 → 能替换就是废话
2. 「为什么是他」含 年份+地点+事件 ≥2 要素
3. 「看这一张」指出画面具体位置/元素
4. 「一个细节」宁可空着也不编
5. 禁用词表已机器扫描
6. 图片只用 PD 或 CC0；1955 年后去世者 link-only
7. 生卒年/作品年份/收藏地交叉核对后把 verified 改 true
-->"""
    return frontmatter + "\n" + body + "\n" + checklist


def update_by_date_index(target_date, column_name, column_info, fm, card_rel_path):
    path = INDEXES_DIR / "by-date.md"
    text = path.read_text(encoding="utf-8")
    year, month = target_date[:4], target_date[5:7]
    emoji = COLUMN_EMOJI.get(column_name, "")
    col_zh = column_info["name_zh"]
    line = f"| {target_date} | {emoji} {col_zh} {column_info['name_en']} | [{fm['name_zh']}]({card_rel_path}) | `{Path(card_rel_path).name}` |"

    month_header = f"### {int(month)} 月"
    lines = text.splitlines()
    if month_header not in text:
        # 新建月份小节：插在年份小节表格之后（首个 --- 分隔线之前）
        year_header = f"## {year} 年"
        if year_header in text:
            yi = next(i for i, l in enumerate(lines) if l.strip() == year_header)
            j = yi + 1
            while j < len(lines) and lines[j].strip() and not lines[j].startswith("---"):
                j += 1
            block = ["", f"### {int(month)} 月", "",
                     "| 日期 | 栏目 | 艺术家 | 文件 |",
                     "|------|------|--------|------|", line]
            lines[j:j] = block
            text = "\n".join(lines)
        else:
            text = text.rstrip() + f"\n\n## {year} 年\n\n{month_header}\n\n| 日期 | 栏目 | 艺术家 | 文件 |\n|------|------|--------|------|\n{line}\n"
    else:
        mi = next(i for i, l in enumerate(lines) if l.strip() == month_header)
        last_row = None
        for j in range(mi + 1, len(lines)):
            if lines[j].startswith("|") and not lines[j].startswith("|----"):
                last_row = j
            elif last_row is not None and not lines[j].startswith("|"):
                break
        if last_row is not None:
            lines.insert(last_row + 1, line)
            text = "\n".join(lines)
        else:
            lines.insert(mi + 1, "| 日期 | 栏目 | 艺术家 | 文件 |")
            lines.insert(mi + 2, "|------|------|--------|------|")
            lines.insert(mi + 3, line)
            text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    log(f"  ✅ 已更新: indexes/by-date.md")


def update_era_index(fm, column_name, column_info, target_date, card_rel_path):
    """by-era.md：按 era 关键词归入时代小节表格。"""
    path = INDEXES_DIR / "by-era.md"
    text = path.read_text(encoding="utf-8")
    era = fm.get("era", "")
    era_bucket = None
    for key in ["东方", "现代与当代", "印象派", "浪漫主义", "新古典", "巴洛克", "文艺复兴", "中世纪", "古代"]:
        if key in era or key in " ".join(fm.get("movements", [])):
            era_bucket = key
            break
    if era_bucket is None:
        era_bucket = "现代与当代" if parse_death_year(fm.get("years", "")) and parse_death_year(fm["years"]) >= 1900 else "巴洛克"

    emoji = COLUMN_EMOJI.get(column_name, "")
    col_zh = column_info["name_zh"]
    gender = GENDER_SYM.get(fm.get("gender", ""), "♂")
    col_display = f"**{col_zh}**" if column_name == "her" else col_zh
    line = f"| {fm['name_zh']} {fm['name_en']} | {fm.get('country', '')} | {gender} | {col_display} | [→](../{card_rel_path}) |"

    # 找到对应时代的 ### 小节（或 ## 小节），在其表格末尾插入
    lines = text.splitlines()
    insert_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == "## " + era_bucket or (ln.strip().startswith("## ") and era_bucket in ln):
            # 找到该小节内最后一个表格行（| 开头）后的空行
            j = i + 1
            last_table_row = None
            while j < len(lines) and not lines[j].startswith("## "):
                if lines[j].lstrip().startswith("|") and "**" not in lines[j] and "（待补充" not in lines[j]:
                    last_table_row = j
                j += 1
            if last_table_row is not None:
                insert_idx = last_table_row + 1
                break
            else:
                # 小节存在但无表格内容：替换（待补充）行
                for k in range(i, j):
                    if "（待补充" in lines[k]:
                        # 在待补充行前插入表头+行
                        header = f"| 艺术家 | 国别 | 性别 | 栏目 | 卡片 |\n|--------|------|------|------|------|"
                        lines[k] = header
                        lines.insert(k + 1, line)
                        text = "\n".join(lines)
                        path.write_text(text, encoding="utf-8")
                        log(f"  ✅ 已更新: indexes/by-era.md（{era_bucket}）")
                        return
                insert_idx = j if j < len(lines) else len(lines)
                break
    if insert_idx is None:
        # 没有该时代小节：在文件末尾加
        text = text.rstrip() + f"\n## {era_bucket}\n\n| 艺术家 | 国别 | 性别 | 栏目 | 卡片 |\n|--------|------|------|------|------|\n{line}\n"
        path.write_text(text, encoding="utf-8")
        log(f"  ✅ 已更新: indexes/by-era.md（新建 {era_bucket}）")
        return
    lines.insert(insert_idx, line)
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    log(f"  ✅ 已更新: indexes/by-era.md（{era_bucket}）")


def update_movement_index(fm, column_name, column_info, card_rel_path):
    path = INDEXES_DIR / "by-movement.md"
    text = path.read_text(encoding="utf-8")
    movements = fm.get("movements", [])
    if not movements:
        return
    gender = GENDER_SYM.get(fm.get("gender", ""), "♂")
    col_zh = column_info["name_zh"]
    col_display = f"**{col_zh}**" if column_name == "her" else col_zh
    for mv in movements:
        header = f"### {mv}"
        line = f"- [{fm['name_zh']}](../{card_rel_path}) — {fm.get('years', '')}, {gender}, {col_display}"
        lines = text.splitlines()
        found = False
        for i, ln in enumerate(lines):
            if ln.strip().startswith("### ") and ln.strip() == header:
                j = i + 1
                while j < len(lines) and not lines[j].startswith("### ") and not lines[j].startswith("## "):
                    j += 1
                # 找小节内最后一个列表项
                last_item = None
                for k in range(i + 1, j):
                    if lines[k].lstrip().startswith("- ["):
                        last_item = k
                if last_item is not None:
                    lines.insert(last_item + 1, line)
                else:
                    for k in range(i + 1, j):
                        if "（待补充" in lines[k]:
                            lines[k] = line
                            break
                    else:
                        lines.insert(j, line)
                text = "\n".join(lines)
                found = True
                break
        if not found:
            text = text.rstrip() + f"\n### {mv}\n\n{line}\n"
        # 跳过"西方流派"容器直接处理（上文已全局处理）
    path.write_text(text, encoding="utf-8")
    log(f"  ✅ 已更新: indexes/by-movement.md")


def update_country_index(fm, column_name, column_info, card_rel_path):
    path = INDEXES_DIR / "by-country.md"
    text = path.read_text(encoding="utf-8")
    country = fm.get("country", "")
    cc = fm.get("country_code", COUNTRY_CODE.get(country, ""))
    if not country or not cc:
        return
    gender = GENDER_SYM.get(fm.get("gender", ""), "♂")
    col_zh = column_info["name_zh"]
    col_display = f"**{col_zh}**" if column_name == "her" else col_zh
    era = fm.get("era", "")
    line = f"- [{fm['name_zh']} {fm['name_en']}](../{card_rel_path}) — {fm.get('years', '')}, {gender}, {era}, {col_display}"

    header = f"### 🇺🇳 {country}"
    # 尝试匹配任何国旗前缀的该国小节
    lines = text.splitlines()
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("### ") and country in ln:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("### ") and not lines[j].startswith("## "):
                j += 1
            last_item = None
            for k in range(i + 1, j):
                if lines[k].lstrip().startswith("- ["):
                    last_item = k
            if last_item is not None:
                lines.insert(last_item + 1, line)
            else:
                for k in range(i + 1, j):
                    if "（待补充" in lines[k]:
                        lines[k] = line
                        break
                else:
                    lines.insert(j, line)
            text = "\n".join(lines)
            found = True
            break
    if not found:
        # 新建国家小节，放到对应大洲
        continent = "东亚 East Asia" if cc in ("CN", "JP", "KR", "MN") else (
            "美洲 Americas" if cc in ("US", "MX", "CA", "BR", "AR") else (
            "欧洲 Europe" if cc not in ("IN", "IR", "TR", "EG") else "其他 Others"))
        section = f"## {continent}"
        insert = f"{section}\n\n### {country}\n\n{line}\n"
        if section in text:
            # 在该 section 的末尾（下一个 ## 前）插入
            lines = text.splitlines()
            idx = None
            for i, ln in enumerate(lines):
                if ln.strip() == section:
                    j = i + 1
                    while j < len(lines) and not lines[j].startswith("## "):
                        j += 1
                    idx = j
                    break
            lines.insert(idx, "")
            lines.insert(idx, f"### {country}")
            lines.insert(idx + 2, line)
            text = "\n".join(lines)
        else:
            text = text.rstrip() + f"\n\n{insert}"
    path.write_text(text, encoding="utf-8")
    log(f"  ✅ 已更新: indexes/by-country.md")


def update_artists_json(fm, data, artist, target_date, card_rel_path):
    rec = {
        "date": target_date,
        "column": fm.get("column", ""),
        "slug": fm.get("slug", ""),
        "name_zh": fm.get("name_zh", ""),
        "name_en": fm.get("name_en", ""),
        "name_native": fm.get("name_native", ""),
        "years": fm.get("years", ""),
        "birthplace": fm.get("birthplace", ""),
        "country": fm.get("country", ""),
        "country_code": fm.get("country_code", ""),
        "era": fm.get("era", ""),
        "movements": fm.get("movements", []),
        "mediums": fm.get("mediums", []),
        "gender": fm.get("gender", ""),
        "hero_image": fm.get("hero_image", ""),
        "hero_caption": fm.get("hero_caption", ""),
        "image_license": fm.get("image_license", ""),
        "image_source": fm.get("image_source", ""),
        "image_source_url": fm.get("image_source_url", ""),
        "verified": False,
        "one_liner": data.get("one_liner", ""),
        "turning_point": {"year": None, "event": "", "location": fm.get("birthplace", "")},
        "key_work": data.get("look_at", {}),
        "fun_fact": data.get("fun_fact", ""),
        "quote": data.get("quote", {}),
        "card_path": card_rel_path,
    }
    arts = load_json(ARTISTS_JSON_PATH)
    arts.setdefault("artists", [])
    arts["artists"].append(rec)
    stats = arts.setdefault("statistics", {})
    stats["total_artists"] = len(arts["artists"])
    g = fm.get("gender", "")
    stats.setdefault("by_gender", {}).setdefault(g, 0)
    stats["by_gender"][g] += 1
    stats.setdefault("by_column", {}).setdefault(fm.get("column", ""), 0)
    stats["by_column"][fm["column"]] += 1
    stats.setdefault("by_country", {}).setdefault(fm.get("country_code", ""), 0)
    stats["by_country"][fm["country_code"]] += 1
    save_json(ARTISTS_JSON_PATH, arts)


def git(*args):
    r = subprocess.run(["git", "-C", str(PROJECT_ROOT), *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def commit_and_push(files, message, do_push=False):
    git("add", "--", *files)
    code, _ = git("diff", "--cached", "--quiet")
    if code == 0:
        log("  无变更，跳过提交")
        return True
    env = dict(os.environ)
    env["GIT_EDITOR"] = "true"
    r = subprocess.run(["git", "-C", str(PROJECT_ROOT), "commit", "-m", message], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        log(f"  [ERROR] commit 失败: {r.stderr[:300]}")
        return False
    log(f"  ✅ commit: {message}")
    if os.environ.get("GITHUB_ACTIONS") == "true" or do_push:
        r = subprocess.run(["git", "-C", str(PROJECT_ROOT), "push"], capture_output=True, text=True)
        if r.returncode != 0:
            log(f"  [ERROR] push 失败: {r.stderr[:300]}")
            return False
        log("  ✅ push 成功")
    return True


def already_published(target_date):
    year, month = target_date[:4], target_date[5:7]
    d = ARTISTS_DIR / year / month
    if not d.exists():
        return False
    return any(f.name.startswith(target_date) and f.suffix == ".md" for f in d.iterdir())


def main():
    parser = argparse.ArgumentParser(description="每天认识一位艺术家 — 全自动发布引擎")
    parser.add_argument("--date", help="指定日期 (YYYY-MM-DD)", default=None)
    parser.add_argument("--dry-run", action="store_true", help="只输出计划")
    parser.add_argument("--no-commit", action="store_true", help="生成但不提交")
    parser.add_argument("--push", action="store_true", help="提交后立即 push（本地定时任务用）")
    parser.add_argument("--llm-api-key", default=None, help="覆盖 LLM_API_KEY（测试用）")
    parser.add_argument("--llm-base-url", default=None, help="覆盖 LLM_BASE_URL")
    parser.add_argument("--llm-model", default=None, help="覆盖 LLM_MODEL")
    args = parser.parse_args()

    api_key = args.llm_api_key or os.environ.get("LLM_API_KEY", "")
    base_url = args.llm_base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
    model = args.llm_model or os.environ.get("LLM_MODEL", "deepseek-chat")

    if not api_key and not args.dry_run:
        log("❌ 缺少 LLM_API_KEY（环境变量）")
        sys.exit(1)

    columns_data = load_json(COLUMNS_PATH)
    pool_data = load_json(POOL_PATH)

    target_date = args.date or datetime.date.today().isoformat()
    column_name, column_info = get_column_for_date(columns_data, target_date)

    log(f"📅 日期: {target_date}（星期{datetime.date.fromisoformat(target_date).weekday() + 1}）")
    log(f"📂 栏目: {column_info['name_zh']} {column_name}（{column_info['brief']}）")

    if already_published(target_date):
        log(f"⚠️  {target_date} 已有卡片，跳过")
        sys.exit(0)

    idx, artist = pick_from_pool(pool_data, column_name)
    if artist is None:
        log(f"⚠️  栏目 '{column_name}' 选题池已空，请补充 pool.json")
        sys.exit(0)

    log(f"🎨 今日艺术家: {artist['name_zh']}（{artist['name_en']}）{artist['years']} · {artist['country']}")

    death_year = parse_death_year(artist["years"])
    link_only = death_year is not None and death_year > 1955

    image = None
    image_license = "Public Domain"
    if link_only:
        log("🔒 1955 年后去世，版权期内 → link-only（不存图）")
        image_license = "Link Only"
    else:
        log("🔍 搜索公有领域图片（Met → AIC）...")
        matches = search_met_images(artist["name_en"]) or search_aic_images(artist["name_en"])
        if matches:
            image = matches[0]
            log(f"  🖼️ 候选: 《{image['title']}》（{image['date']}）- {image['image_source']}")
        else:
            log("  ⚠️ 未找到公有领域图片，卡片将用链接展示代表作品")

    if args.dry_run:
        log("[DRY RUN] 计划如上，未调用 LLM、未下载图片。")
        return

    log("✍️ 调用 LLM 撰写卡片...")
    prompt = build_llm_prompt(artist, column_info, image, target_date)
    data = call_llm(prompt, api_key, base_url, model)
    if data is None:
        log("❌ LLM 生成失败（已重试），今天未发布")
        sys.exit(1)

    ok, problems = validate_card(data, artist)
    if not ok:
        log("⚠️ 自检未通过，重试一次:")
        for p in problems:
            log(f"   - {p}")
        fix_hint = "\n修正要求：" + "；".join(problems)
        data = call_llm(prompt + fix_hint, api_key, base_url, model, retries=1)
        if data is None:
            log("❌ 重试失败，今天未发布")
            sys.exit(1)
        ok, problems = validate_card(data, artist)
        if not ok:
            log("❌ 自检仍未通过，今天未发布:")
            for p in problems:
                log(f"   - {p}")
            sys.exit(1)
    log("  ✅ 自检通过（禁用词 / 标点 / 必填字段）")

    fm = data["frontmatter"]
    fm["date"] = target_date
    fm["column"] = column_name
    fm["slug"] = fm.get("slug") or re.sub(r"[^a-z0-9-]+", "-", artist["name_en"].lower()).strip("-")
    fm.setdefault("name_zh", artist["name_zh"])
    fm.setdefault("name_en", artist["name_en"])
    fm.setdefault("years", artist["years"])
    fm.setdefault("country", artist["country"])
    fm.setdefault("country_code", COUNTRY_CODE.get(artist["country"], ""))
    fm.setdefault("image_license", image_license)
    if image:
        fm.setdefault("image_source", image["image_source"])
        fm.setdefault("image_source_url", image["image_source_url"])

    slug = fm["slug"]
    year, month = target_date[:4], target_date[5:7]
    out_dir = ARTISTS_DIR / year / month
    out_dir.mkdir(parents=True, exist_ok=True)

    image_path_rel = None
    if image and not link_only:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        img_name = f"{target_date}-{slug}.jpg"
        try:
            download_and_compress_image(image["primaryImage"], IMAGES_DIR / img_name)
            image_path_rel = f"images/{img_name}"
            fm["hero_image"] = image_path_rel
        except Exception as e:
            log(f"  ⚠️ 图片下载失败（{str(e)[:80]}），降级为链接展示")
            image_path_rel = None
            fm["hero_image"] = image["image_source_url"]
    else:
        fm["hero_image"] = image["image_source_url"] if image else ""

    card_path = out_dir / f"{target_date}-{slug}.md"
    card_rel = f"artists/{year}/{month}/{card_path.name}"
    markdown = build_card_markdown(data, artist, target_date, image_path_rel, image)
    card_path.write_text(markdown, encoding="utf-8")
    log(f"  ✅ 卡片已生成: {card_rel}")

    # 更新 pool
    pool_data["pools"][column_name][idx]["status"] = "published"
    pool_data["total_queued"] = max(0, pool_data.get("total_queued", 0) - 1)
    save_json(POOL_PATH, pool_data)

    # 更新索引
    update_by_date_index(target_date, column_name, column_info, fm, f"{year}/{month}/{card_path.name}")
    update_era_index(fm, column_name, column_info, target_date, card_rel)
    update_movement_index(fm, column_name, column_info, card_rel)
    update_country_index(fm, column_name, column_info, card_rel)
    update_artists_json(fm, data, artist, target_date, card_rel)

    if args.no_commit:
        log("  （--no-commit，未提交）")
        return

    files = [card_rel]
    if image_path_rel:
        files.append(image_path_rel)
    files += [str(POOL_PATH.relative_to(PROJECT_ROOT)),
              str(ARTISTS_JSON_PATH.relative_to(PROJECT_ROOT))]
    for idx_file in INDEXES_DIR.glob("*.md"):
        files.append(str(idx_file.relative_to(PROJECT_ROOT)))

    if commit_and_push(files, f"🎨 {target_date}: {fm['name_zh']}（{column_info['name_zh']}）", do_push=args.push):
        log(f"\n🎉 发布完成！https://github.com/gengyueworks/one-artist-a-day/blob/main/{card_rel}")
        log(f"   ⚠️ 卡片 verified=false，请抽空人工核对生卒年与作品信息后改 true")


if __name__ == "__main__":
    import urllib.request  # noqa: F401
    main()
