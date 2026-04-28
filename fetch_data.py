"""
一次性抓取书籍数据，输出 data/books.json
来源：Wikidata（全球） + Open Library（中文/亚洲地区）
"""

import json
import re
import time
import sys
from pathlib import Path
import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "LiteraryMap/1.0 (educational project; contact: github.com/literary-map)",
    "Accept": "application/json",
})

OUT_DIR = Path(__file__).parent / "data"
OUT_DIR.mkdir(exist_ok=True)
OUT_FILE = OUT_DIR / "books.json"

# ─────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────

def parse_coord(coord_str):
    """'Point(-1.0 53.0)' → [lng, lat]"""
    m = re.match(r"Point\(([^ ]+) ([^)]+)\)", coord_str or "")
    if not m:
        return None
    lng, lat = float(m.group(1)), float(m.group(2))
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return [round(lng, 5), round(lat, 5)]


def wikidata_sparql(query, retries=3):
    url = "https://query.wikidata.org/sparql"
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params={"query": query, "format": "json"}, timeout=60)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  限速，等待 {wait}s…", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except Exception as e:
            print(f"  SPARQL 错误 (attempt {attempt+1}): {e}", flush=True)
            time.sleep(5)
    return []


# ─────────────────────────────────────────────
# 第一步：Wikidata
# ─────────────────────────────────────────────

# 覆盖多种文学体裁：
#   Q7725634 = literary work（文学作品，最宽泛）
#   P840     = narrative location（故事发生地）
SPARQL_TEMPLATE = """
SELECT DISTINCT ?book ?bookLabel ?authorLabel ?locationLabel ?coord ?coverUrl WHERE {{
  ?book wdt:P840 ?location .
  ?location wdt:P625 ?coord .
  OPTIONAL {{ ?book wdt:P50 ?author }}
  OPTIONAL {{ ?book wdt:P18 ?coverUrl }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh-hans,zh,en" . }}
}}
LIMIT 5000
OFFSET {offset}
"""
# 不限制 P31 类型，涵盖小说/诗集/剧本/散文/人文社科等一切有叙事地点的作品
# 语言优先级：简体中文 > 中文 > 英文


def fetch_wikidata():
    books = []
    seen = set()
    offset = 0

    while True:
        print(f"  Wikidata offset={offset}，已获取 {len(books)} 本…", flush=True)
        rows = wikidata_sparql(SPARQL_TEMPLATE.format(offset=offset))
        if not rows:
            break

        for row in rows:
            book_id = row.get("book", {}).get("value", "")
            coord_str = row.get("coord", {}).get("value", "")
            if not book_id or not coord_str:
                continue

            key = book_id + coord_str
            if key in seen:
                continue
            seen.add(key)

            coord = parse_coord(coord_str)
            if not coord:
                continue

            title = row.get("bookLabel", {}).get("value", "")
            # Q号表示 Wikidata 无此语言标签，保留 Q号（前端可异步补全）
            # 不再置空，保持原样

            cover = row.get("coverUrl", {}).get("value", "")
            # 转成 thumb URL
            if cover:
                fname = cover.split("/")[-1].split("?")[0]
                cover = f"https://commons.wikimedia.org/w/thumb.php?f={fname}&w=300"

            author = row.get("authorLabel", {}).get("value", "")
            # 作者若是 Q 号也置空（无意义）
            if re.match(r"^Q\d+$", author):
                author = ""

            books.append({
                "id": book_id,
                "title": title,
                "author": author,
                "location": row.get("locationLabel", {}).get("value", ""),
                "coord": coord,
                "cover": cover or None,
                "url": book_id,
                "source": "wikidata",
            })

        if len(rows) < 5000:
            break
        offset += 5000
        time.sleep(1)

    return books


# ─────────────────────────────────────────────
# 第二步：Open Library
# ─────────────────────────────────────────────

PLACE_COORDS = {
    "beijing": [116.39742, 39.90927], "peking": [116.39742, 39.90927],
    "shanghai": [121.47370, 31.23040],
    "guangzhou": [113.26436, 23.12911], "canton": [113.26436, 23.12911],
    "chengdu": [104.06657, 30.57231],
    "chongqing": [106.55160, 29.56300],
    "nanjing": [118.79680, 32.06030], "nanking": [118.79680, 32.06030],
    "hangzhou": [120.15361, 30.27415],
    "suzhou": [120.58530, 31.29900],
    "wuhan": [114.30540, 30.59310],
    "xian": [108.94020, 34.34160], "xi'an": [108.94020, 34.34160],
    "tianjin": [117.20100, 39.08420],
    "harbin": [126.53490, 45.80380],
    "kunming": [102.70830, 25.04530],
    "fuzhou": [119.29650, 26.07450],
    "xiamen": [118.08940, 24.47980], "amoy": [118.08940, 24.47980],
    "qingdao": [120.38260, 36.06710],
    "jinan": [116.99720, 36.65120],
    "zhengzhou": [113.62540, 34.75330],
    "changsha": [112.93880, 28.22820],
    "hong kong": [114.16940, 22.31930], "hongkong": [114.16940, 22.31930],
    "macau": [113.54390, 22.19870], "macao": [113.54390, 22.19870],
    "taiwan": [120.96050, 23.69780], "taipei": [121.56540, 25.03300],
    "manchuria": [125.32360, 45.74140],
    "tibet": [88.00000, 31.00000], "lhasa": [91.14090, 29.65000],
    "china": [104.19540, 35.86170],
    "singapore": [103.81980, 1.35210],
    "tokyo": [139.69170, 35.68950], "edo": [139.69170, 35.68950],
    "kyoto": [135.76800, 35.01160],
    "osaka": [135.50220, 34.69370],
    "hiroshima": [132.45940, 34.39330],
    "seoul": [126.97800, 37.56630],
    "busan": [129.07560, 35.17950],
    "pyongyang": [125.75430, 39.03920],
    "hanoi": [105.85416, 21.02780],
    "saigon": [106.66000, 10.75000], "ho chi minh": [106.66000, 10.75000],
    "bangkok": [100.49350, 13.75400],
    "rangoon": [96.19510, 16.86610], "yangon": [96.19510, 16.86610],
    "manila": [120.98220, 14.59950],
    "jakarta": [106.84130, -6.21460], "batavia": [106.84130, -6.21460],
    "kuala lumpur": [101.68690, 3.14120],
    "calcutta": [88.36300, 22.57270], "kolkata": [88.36300, 22.57270],
    "bombay": [72.87360, 19.07600], "mumbai": [72.87360, 19.07600],
    "delhi": [77.20900, 28.61400], "new delhi": [77.20900, 28.61400],
    "madras": [80.27070, 13.08270], "chennai": [80.27070, 13.08270],
    "lahore": [74.35870, 31.52040],
    "karachi": [67.01000, 24.86080],
    "cairo": [31.23570, 30.04420],
    "alexandria": [29.91870, 31.20890],
    "istanbul": [28.97840, 41.01380], "constantinople": [28.97840, 41.01380],
    "baghdad": [44.36610, 33.34580],
    "tehran": [51.42310, 35.69440],
    "jerusalem": [35.21640, 31.76890],
    "nairobi": [36.82190, -1.29210],
    "lagos": [3.37950, 6.45510],
    "johannesburg": [28.04630, -26.20270],
    "buenos aires": [-58.38160, -34.60370],
    "rio de janeiro": [-43.17290, -22.90680], "rio": [-43.17290, -22.90680],
    "mexico city": [-99.13320, 19.42470],
    "havana": [-82.38240, 23.13020],
    "bogota": [-74.08750, 4.71100],
    "lima": [-77.02820, -12.04320],
    "santiago": [-70.64830, -33.45690],
    "moscow": [37.61560, 55.75220],
    "saint petersburg": [30.31580, 59.93900], "leningrad": [30.31580, 59.93900],
    "odessa": [30.72330, 46.47470],
    "warsaw": [21.01220, 52.22977],
    "prague": [14.42060, 50.08804],
    "budapest": [19.04020, 47.49801],
    "bucharest": [26.09660, 44.43225],
}

ASIA_KEYWORDS = {
    "china", "beijing", "shanghai", "guangzhou", "hong kong", "taiwan",
    "chengdu", "chongqing", "nanjing", "hangzhou", "suzhou", "wuhan",
    "xian", "tianjin", "harbin", "kunming", "tibet", "manchuria",
    "macau", "taipei", "peking", "canton", "nanking", "amoy", "xiamen",
    "qingdao", "singapore", "tokyo", "kyoto", "osaka", "seoul",
    "hanoi", "saigon", "bangkok", "calcutta", "bombay", "kolkata",
}

OL_QUERIES = [
    # 中国
    "Beijing", "Shanghai", "Hong Kong", "Guangzhou", "Chengdu",
    "Nanjing", "Hangzhou", "Wuhan", "Xian", "Taiwan", "Tibet",
    "Manchuria", "Chongqing", "Suzhou", "Harbin", "Peking",
    "Tianjin", "Kunming", "Fuzhou", "Xiamen", "Qingdao",
    # 日本
    "Tokyo", "Kyoto", "Osaka", "Hiroshima", "Edo",
    # 韩国
    "Seoul", "Busan", "Pyongyang",
    # 东南亚
    "Singapore", "Bangkok", "Hanoi", "Saigon", "Rangoon",
    "Manila", "Jakarta", "Kuala Lumpur",
    # 南亚
    "Calcutta", "Bombay", "Delhi", "Madras", "Lahore", "Karachi",
    # 中东
    "Cairo", "Istanbul", "Baghdad", "Tehran", "Jerusalem",
    # 非洲
    "Nairobi", "Lagos", "Johannesburg", "Alexandria",
    # 拉丁美洲
    "Buenos Aires", "Rio de Janeiro", "Mexico City", "Havana",
    "Bogota", "Lima", "Santiago",
    # 俄罗斯/东欧
    "Moscow", "Saint Petersburg", "Leningrad", "Odessa",
    "Warsaw", "Prague", "Budapest", "Bucharest",
]

nominatim_cache = {}

def geocode(place_str):
    """地名→[lng,lat]，先查内置表，再 Nominatim"""
    key = place_str.lower().strip()
    # 精确
    if key in PLACE_COORDS:
        return PLACE_COORDS[key]
    # 部分匹配
    for k, v in PLACE_COORDS.items():
        if k in key:
            return v
    # Nominatim（严格限速）
    if key in nominatim_cache:
        return nominatim_cache[key]
    try:
        time.sleep(1.2)
        r = SESSION.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place_str, "format": "json", "limit": 1},
            headers={"Accept-Language": "en"},
            timeout=10,
        )
        data = r.json()
        if data:
            coord = [round(float(data[0]["lon"]), 5), round(float(data[0]["lat"]), 5)]
            nominatim_cache[key] = coord
            return coord
    except Exception as e:
        print(f"    Nominatim 失败: {place_str} → {e}", flush=True)
    nominatim_cache[key] = None
    return None


def is_asia_related(places):
    joined = " ".join(places).lower()
    return any(k in joined for k in ASIA_KEYWORDS)


def fetch_openlibrary():
    books = []
    seen = set()

    for city in OL_QUERIES:
        print(f"  Open Library: {city}…", flush=True)
        try:
            r = SESSION.get(
                "https://openlibrary.org/search.json",
                params={
                    "q": city,
                    "place": city,
                    "limit": 100,
                    "fields": "key,title,author_name,place,cover_i",
                },
                timeout=20,
            )
            r.raise_for_status()
            docs = r.json().get("docs", [])
        except Exception as e:
            print(f"    请求失败: {e}", flush=True)
            time.sleep(2)
            continue

        for doc in docs:
            key = doc.get("key", "")
            places = doc.get("place", [])
            if not key or not places:
                continue
            if key in seen:
                continue
            if not is_asia_related(places):
                continue
            seen.add(key)

            # 坐标
            coord = None
            for p in places:
                coord = geocode(p)
                if coord and geocode(p) != PLACE_COORDS.get("china"):
                    break
            if not coord:
                coord = geocode(city)
            if not coord:
                continue

            cover_i = doc.get("cover_i")
            cover = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg" if cover_i else None

            loc = next((p for p in places if p.lower() not in ("china", "zhong guo")), places[0] if places else "")

            books.append({
                "id": f"https://openlibrary.org{key}",
                "title": doc.get("title", ""),
                "author": ", ".join(doc.get("author_name", [])),
                "location": loc,
                "coord": coord,
                "cover": cover,
                "url": f"https://openlibrary.org{key}",
                "source": "openlibrary",
            })

        time.sleep(0.5)

    return books


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def main():
    print("=== 抓取 Wikidata ===", flush=True)
    wd_books = fetch_wikidata()
    print(f"Wikidata: {len(wd_books)} 本", flush=True)

    print("\n=== 抓取 Open Library ===", flush=True)
    ol_books = fetch_openlibrary()
    print(f"Open Library: {len(ol_books)} 本", flush=True)

    all_books = wd_books + ol_books
    print(f"\n合计: {len(all_books)} 本", flush=True)

    # 写文件
    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(all_books),
        "books": all_books,
    }
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_kb = OUT_FILE.stat().st_size // 1024
    print(f"已写入 {OUT_FILE}  ({size_kb} KB)", flush=True)


if __name__ == "__main__":
    main()
