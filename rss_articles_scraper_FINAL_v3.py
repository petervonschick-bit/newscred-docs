#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rss_articles_scraper_FINAL_v3.py
- FIX: articles.source_id mostantól rss_feeds.source_id (nem rss_feeds.id)
- limit paraméter támogatás
- timezone-aware dátum
Dátum: 2025-10-26
"""

import os
import sys
import time
import json
import hashlib
import logging
import requests
import feedparser
import pymysql
import argparse
from datetime import datetime, UTC

DB_CONFIG_PATH = os.getenv("NEWS_DB_JSON", "/opt/newscred/db.json")
LOG_FILE = "/tmp/rss_scraper.log"
REQUEST_TIMEOUT = 10

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/121.0",
]

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def load_db_config():
    if not os.path.exists(DB_CONFIG_PATH):
        raise FileNotFoundError(f"Hiányzó konfigurációs fájl: {DB_CONFIG_PATH}")
    with open(DB_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def connect_db():
    cfg = load_db_config()
    conn = pymysql.connect(
        host=cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )
    return conn

def parse_feed(feed_url, ua_index_seed: int):
    """RSS feed letöltése Requests-szel, majd feedparser parse."""
    logger.info(f"📥 Feed feldolgozása: {feed_url}")
    headers = {
        "User-Agent": USER_AGENTS[ua_index_seed % len(USER_AGENTS)],
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    try:
        resp = requests.get(feed_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"⚠ Feed letöltési hiba: {feed_url} - {e}")
        return []

    feed = feedparser.parse(resp.content)
    if getattr(feed, "bozo", 0):
        logger.warning(f"⚠ Feed parsing hiba: {feed_url} - {feed.bozo_exception}")
        return []

    entries = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        published = entry.get("published_parsed")
        if published:
            published_at = datetime.fromtimestamp(time.mktime(published), tz=UTC)
        else:
            published_at = datetime.now(UTC)

        summary = entry.get("summary", "")
        entries.append({
            "title": title,
            "link": link,
            "published_at": published_at,
            "summary": summary,
        })

    logger.info(f"📰 Talált cikkek száma: {len(entries)}")
    return entries

def save_articles(conn, articles, source_id: int):
    if not articles:
        return 0
    inserted = 0

    sql = """
        INSERT INTO articles (source_id, title, link, link_hash, summary, published_at, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE title = VALUES(title)
    """

    with conn.cursor() as cur:
        for a in articles:
            link_hash = hashlib.md5(a["link"].encode("utf-8")).hexdigest()
            try:
                cur.execute(sql, (
                    source_id,           # <- I T T  A  L É N Y E G !
                    a["title"],
                    a["link"],
                    link_hash,
                    a["summary"],
                    a["published_at"],
                ))
                inserted += cur.rowcount
            except Exception as e:
                logger.error(f"❌ DB hiba ({a['link']}): {e}")
                conn.rollback()
        conn.commit()

    logger.info(f"💾 {inserted} cikk mentve az adatbázisba.")
    return inserted

def main(limit: int):
    logger.info("=" * 90)
    logger.info(f"📥 RSS Feedek feldolgozása (limit={limit})")
    logger.info("=" * 90)

    conn = connect_db()
    cur = conn.cursor()

    # FONTOS: hozzuk a source_id-t is!
    cur.execute(f"""
        SELECT id, source_id, feed_url, domain
        FROM rss_feeds
        LIMIT {int(limit)};
    """)
    feeds = cur.fetchall()

    total_found = 0
    total_inserted = 0

    for i, feed in enumerate(feeds, 1):
        # domain lehet NULL → logban kezeljük
        logger.info(f"📊 [{i}/{len(feeds)}] - {feed.get('domain') or 'n/a'}")
        # UA választáshoz nyugodtan használjuk a feed saját id-ját
        articles = parse_feed(feed["feed_url"], ua_index_seed=feed["id"])
        total_found += len(articles)

        # 🔑 I T T  A  L É N Y E G: a cikkek source_id-ja a feeds.source_id!
        total_inserted += save_articles(conn, articles, source_id=int(feed["source_id"]))

    logger.info("=" * 90)
    logger.info("📊 Scraping statisztika:")
    logger.info(f"  Feldolgozott feedek: {len(feeds)}")
    logger.info(f"  Talált cikkek: {total_found}")
    logger.info(f"  Mentett új cikkek: {total_inserted}")
    logger.info("=" * 90)

    conn.close()
    logger.info("🔒 Adatbázis kapcsolat lezárva")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RSS cikkletöltő és adatbázisba író eszköz")
    parser.add_argument("--limit", type=int, default=5, help="hány feedet dolgozzon fel")
    args = parser.parse_args()

    try:
        main(args.limit)
        logger.info("✅ Scraping sikeresen befejezve!")
    except KeyboardInterrupt:
        logger.warning("⛔ Megszakítva felhasználó által.")
    except Exception as e:
        logger.exception(f"💥 Végzetes hiba: {e}")
        sys.exit(1)
