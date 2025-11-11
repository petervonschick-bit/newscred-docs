#!/usr/bin/env python3
"""
NVDA News Agent (MVP)
---------------------
- Fetches NVIDIA (NVDA) news from the last 15 minutes using:
  * GDELT DOC API (timespan=15min)
  * Google News RSS
- Normalizes & deduplicates by canonical URL (sha256 hash)
- Writes into MySQL:
  * Preferred: directly into your existing `articles` table (if minimal columns exist)
  * Fallback: into staging table `ingest_articles_stage` (provided in SQL below)
- Idempotent: repeated runs won't duplicate rows.

USAGE
-----
$ export NEWS_DB_JSON=/opt/newscred/db.json   # or any path to a JSON with host/user/password/database
$ python3 nvda_news_agent.py

The JSON should look like:
{
  "host": "192.168.10.100",
  "user": "webServer",
  "password": "webServer192.168.20.100",
  "database": "newscred",
  "port": 3306
}

CRON (every 5 minutes):
*/5 * * * * /usr/bin/env NEWS_DB_JSON=/opt/newscred/db.json /usr/bin/python3 /opt/newscred/nvda_news_agent.py >> /var/log/nvda_agent.log 2>&1
"""
import os
import sys
import json
import time
import math
import hashlib
import logging
import datetime as dt
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import requests
import feedparser
import pymysql

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q=NVIDIA%20OR%20NVDA&hl=en-US&gl=US&ceid=US:en"

KEYWORDS_HARD = {"nvidia","nvda","jensen huang","geforce","cuda","h100","b200","gb200"}

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)

def load_db_cfg():
    path = os.environ.get("NEWS_DB_JSON")
    if not path or not os.path.exists(path):
        logging.warning("NEWS_DB_JSON not set or file missing; falling back to default creds (localhost/newscred).")
        return dict(host="127.0.0.1", user="webServer", password="webServer192.168.20.100", database="newscred", port=3306)
    with open(path,"r",encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("port",3306)
    return data

def canonicalize_url(u: str) -> str:
    try:
        p = urlparse(u.strip())
        # Drop tracking params and AMP
        q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(("utm_","fbclid","gclid","mc_"))]
        path = re.sub(r"/amp(/|$)", "/", p.path, flags=re.IGNORECASE)
        # Remove trailing slashes
        path = re.sub(r"/+$","",path)
        p2 = p._replace(query=urlencode(q, doseq=True), path=path)
        return urlunparse(p2)
    except Exception:
        return u.strip()

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def within_last_minutes(published_dt: dt.datetime, minutes: int = 15) -> bool:
    # naive UTC compare
    now = dt.datetime.utcnow()
    return (now - published_dt) <= dt.timedelta(minutes=minutes + 2)  # +2 min tolerance

def parse_datetime(s: str) -> dt.datetime | None:
    # Try multiple common formats; fallback to now
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=None)
        except Exception:
            pass
    return None

def fetch_gdelt():
    params = dict(
        query="NVIDIA OR NVDA",
        mode="artlist",
        format="json",
        timespan="15min",
        maxrecords="250",
        sort="DateDesc",
    )
    r = requests.get(GDELT_DOC_API, params=params, timeout=30)
    r.raise_for_status()
    js = r.json()
    results = []
    for it in js.get("articles", []):
        url = it.get("url") or ""
        title = (it.get("title") or "").strip()
        source = (it.get("sourceDomain") or it.get("domain") or "").strip()
        ts = it.get("seendate") or it.get("date")
        # GDELT seendate is like '2025-11-10T13:45:00Z'
        published_at = parse_datetime(ts) or dt.datetime.utcnow()
        if not url or not title:
            continue
        results.append(dict(
            url=url, title=title, source=source, published_at=published_at, provider="gdelt"
        ))
    return results

def fetch_google_news():
    d = feedparser.parse(GOOGLE_NEWS_RSS)
    results = []
    for e in d.entries:
        url = e.link
        title = e.title
        source = getattr(e, "source", {}).get("title") if hasattr(e, "source") else (e.get("source") or "")
        pub = e.get("published") or e.get("updated") or ""
        published_at = parse_datetime(pub) or dt.datetime.utcnow()
        results.append(dict(
            url=url, title=title, source=source or "GoogleNews", published_at=published_at, provider="googlenews"
        ))
    return results

def is_relevant(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in KEYWORDS_HARD)

def connect_db(cfg):
    return pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        database=cfg["database"], port=cfg.get("port",3306), charset="utf8mb4", autocommit=False
    )

def table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None

def columns_for(cur, table: str) -> set[str]:
    cur.execute(f"DESCRIBE `{table}`")
    return {row[0] for row in cur.fetchall()}

def ensure_stage(cur):
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ingest_articles_stage (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      url TEXT NOT NULL,
      url_hash CHAR(64) NOT NULL,
      title VARCHAR(512) NOT NULL,
      source VARCHAR(255) NULL,
      published_at DATETIME NULL,
      fetched_at DATETIME NOT NULL,
      lang VARCHAR(16) NULL,
      company VARCHAR(128) NULL,
      provider VARCHAR(64) NULL,
      UNIQUE KEY uniq_urlhash (url_hash)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

def upsert_into_articles(cur, row, available_cols: set[str]) -> bool:
    """
    Attempt to insert into existing `articles` table if it has the minimal required columns.
    Minimal set assumed: url, title, source, published_at, fetched_at, url_hash (or UNIQUE(url))
    Returns True if succeeded, False if not supported.
    """
    minimal = {"url", "title", "source", "published_at", "fetched_at"}
    has_urlhash = "url_hash" in available_cols
    if not minimal.issubset(available_cols):
        return False
    cols = ["url","title","source","published_at","fetched_at"]
    vals = [row["url"], row["title"], row["source"], row["published_at"], row["fetched_at"]]
    if "lang" in available_cols:
        cols.append("lang"); vals.append(row.get("lang"))
    if "company" in available_cols:
        cols.append("company"); vals.append("NVIDIA")
    if has_urlhash:
        cols.append("url_hash"); vals.append(row["url_hash"])
        sql = f"INSERT INTO articles ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))}) ON DUPLICATE KEY UPDATE title=VALUES(title), source=VALUES(source), published_at=VALUES(published_at)"
    else:
        # Rely on UNIQUE(url) if present; otherwise duplicates may appear
        sql = f"INSERT IGNORE INTO articles ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))})"
    cur.execute(sql, vals)
    return True

def insert_into_stage(cur, row):
    sql = """INSERT INTO ingest_articles_stage
             (url, url_hash, title, source, published_at, fetched_at, lang, company, provider)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
             ON DUPLICATE KEY UPDATE title=VALUES(title), source=VALUES(source), published_at=VALUES(published_at)"""
    cur.execute(sql, (
        row["url"], row["url_hash"], row["title"], row["source"],
        row["published_at"], row["fetched_at"], row.get("lang"),
        "NVIDIA", row.get("provider")
    ))

def main():
    cfg = load_db_cfg()
    logging.info(f"Connecting to MySQL: {cfg.get('host')} / DB={cfg.get('database')} user={cfg.get('user')}")
    conn = connect_db(cfg)
    cur = conn.cursor()

    # Decide target
    use_articles = False
    if table_exists(cur, "articles"):
        cols = columns_for(cur, "articles")
        use_articles = True if {"url","title","source","published_at","fetched_at"}.issubset(cols) else False
    if not use_articles:
        ensure_stage(cur)

    fetched = []
    try:
        fetched += fetch_gdelt()
    except Exception as e:
        logging.warning(f"GDELT fetch failed: {e}")
    try:
        fetched += fetch_google_news()
    except Exception as e:
        logging.warning(f"Google News fetch failed: {e}")

    # Normalize, filter by time/relevance, deduplicate by url_hash in-memory
    now = dt.datetime.utcnow()
    items = []
    seen = set()
    for r in fetched:
        url_c = canonicalize_url(r["url"])
        h = sha256(url_c)
        if h in seen: 
            continue
        seen.add(h)
        title = r["title"].strip()
        if not is_relevant(title + " " + url_c):
            # Keep only items that at least mention NVIDIA ecosystem strongly
            continue
        pub = r["published_at"]
        if not isinstance(pub, dt.datetime):
            pub = now
        if not within_last_minutes(pub, minutes=15):
            continue
        items.append({
            "url": url_c,
            "url_hash": h,
            "title": title[:512],
            "source": (r.get("source") or "unknown")[:255],
            "published_at": pub,
            "fetched_at": now,
            "lang": None,
            "provider": r.get("provider")
        })

    inserted = updated = staged = 0
    for row in items:
        if use_articles:
            ok = upsert_into_articles(cur, row, columns_for(cur,"articles"))
            if ok:
                inserted += cur.rowcount in (1,2)  # crude counter
            else:
                ensure_stage(cur)
                insert_into_stage(cur, row); staged += 1
        else:
            insert_into_stage(cur, row); staged += 1

    conn.commit()
    logging.info(f"Processed items: {len(items)} | staged: {staged} | direct_inserts(updates): ~{inserted}")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
