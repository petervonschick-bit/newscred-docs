#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translate_worker.py - PRODUCTION verzió
Háttérben folyamatos fordítás, 100-as batchek, 40% CPU limit
"""

import os
import json
import time
import re
import requests
import pymysql
import psutil
import signal
import sys
from datetime import datetime
from typing import List, Optional

# ===== CONFIG =====
CONFIG_FILE = "/opt/newscred/translate_config.json"
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

DB_CFG_FILE = CONFIG["database"]["config_file"]
HF_TOKEN = CONFIG["huggingface"]["token"]
HF_MODEL = CONFIG["huggingface"]["model"]
PROVIDER = CONFIG["huggingface"]["provider"]
TIMEOUT = CONFIG["huggingface"]["timeout"]
MAX_RETRIES = CONFIG["huggingface"]["max_retries"]
MAX_CHARS = CONFIG["translation"]["max_chars_per_chunk"]
SLEEP = CONFIG["translation"]["sleep_between_requests"]
BATCH_SLEEP = CONFIG["translation"]["sleep_between_batches"]
CPU_LIMIT = CONFIG["performance"]["cpu_limit_percent"]
BATCH_SIZE = CONFIG["performance"]["batch_size_prod"]
LOG_DIR = CONFIG["logging"]["log_dir"]
LOG_FILE = os.path.join(LOG_DIR, CONFIG["logging"]["log_file_worker"])

# ===== GLOBAL =====
RUNNING = True

def signal_handler(sig, frame):
    global RUNNING
    log("🛑 SIGTERM received, shutting down...")
    RUNNING = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# ===== LOGGING =====
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"Log write error: {e}", flush=True)

# ===== UTIL =====
def _clean_for_json(s: str) -> str:
    """eltávolítja a vezérlőkaraktereket"""
    return ''.join(ch for ch in (s or "") if ch in '\t\n\r' or ord(ch) >= 32)

_SENT_SEP = re.compile(r'(?<=[\.\?\!…])\s+|\n+')
def split_text(s: str) -> List[str]:
    """szöveg darabolása mondatok szerint"""
    s = (s or "").strip()
    if not s:
        return []
    s = re.sub(r'[ \t]{2,}', ' ', s)
    parts, buf = [], ""
    for sent in _SENT_SEP.split(s):
        if not sent:
            continue
        while len(sent) > MAX_CHARS:
            parts.append(sent[:MAX_CHARS])
            sent = sent[MAX_CHARS:]
        if len(buf) + len(sent) + 1 <= MAX_CHARS:
            buf = (buf + " " + sent).strip()
        else:
            if buf:
                parts.append(buf)
            buf = sent
    if buf:
        parts.append(buf)
    return parts

def get_cpu_percent() -> float:
    """aktuális CPU% (www-data processz)"""
    try:
        for proc in psutil.process_iter(['pid', 'name', 'username']):
            try:
                if proc.info.get('username') == 'www-data':
                    return proc.cpu_percent(interval=0.1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        log(f"⚠️ CPU check error: {e}")
    return 0

def can_continue() -> bool:
    """40% alatt van-e?"""
    cpu = get_cpu_percent()
    return cpu < CPU_LIMIT

# ===== TRANSLATE =====
def hf_infer(text: str, attempt: int = 0) -> Optional[str]:
    """Hugging Face fordítás (retry logikával)"""
    if attempt > MAX_RETRIES:
        log(f"❌ Max retries exceeded for text ({len(text)} chars)")
        return None
    
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}"
    text = (text or "").strip()
    if not text:
        return ""

    payload = {"inputs": _clean_for_json(text)}
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
        
        # 503, 529 - service unavailable, retry
        if r.status_code in (503, 529):
            wait = 1 + attempt
            log(f"⚠️ HTTP {r.status_code} - retry in {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            return hf_infer(text, attempt + 1)
        
        # 200 OK
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data and "translation_text" in data[0]:
                return data[0]["translation_text"]
            if isinstance(data, dict) and "translation_text" in data:
                return data["translation_text"]
            log(f"⚠️ 200 OK but unexpected format")
            return None
        
        # 400 - rekurzív felezés
        if r.status_code == 400:
            if len(text) > MAX_CHARS:
                log(f"ℹ️ 400 - splitting text ({len(text)} chars)")
                mid = len(text) // 2
                left = hf_infer(text[:mid], attempt)
                right = hf_infer(text[mid:], attempt)
                if left and right:
                    return (left + "\n" + right).strip()
                return None
            else:
                log(f"❌ 400 Bad Request (text too short to split)")
                return None
        
        # Egyéb hiba
        log(f"❌ HTTP {r.status_code}: {r.text[:150]}")
        return None
        
    except requests.Timeout:
        log(f"⚠️ Timeout ({TIMEOUT}s) - retry (attempt {attempt + 1}/{MAX_RETRIES})")
        time.sleep(0.5 + attempt * 0.5)
        return hf_infer(text, attempt + 1)
    except Exception as e:
        log(f"⚠️ Request error: {type(e).__name__} - retry (attempt {attempt + 1}/{MAX_RETRIES})")
        time.sleep(0.5)
        return hf_infer(text, attempt + 1)

# ===== DATABASE =====
def db_connect():
    """adatbázis kapcsolat"""
    with open(DB_CFG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return pymysql.connect(
        host=cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        autocommit=True,
    )

def get_pending_articles(conn, limit: int = 100):
    """fordítandó cikkek lekérése (latest DESC)"""
    q = """
        SELECT t.article_id, t.text
        FROM article_texts t
        JOIN articles a ON a.id = t.article_id
        WHERE a.status = 0
          AND t.text IS NOT NULL
          AND (t.text_en IS NULL OR t.text_en = '')
        ORDER BY t.article_id DESC
        LIMIT %s;
    """
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(q, (limit,))
        return cur.fetchall()

def save_translation(conn, article_id: int, text_en: str):
    """fordítás mentése"""
    q = """
        UPDATE article_texts
        SET text_en = %s,
            en_provider = %s,
            en_updated_at = NOW()
        WHERE article_id = %s;
    """
    with conn.cursor() as cur:
        cur.execute(q, (text_en, PROVIDER, article_id))

# ===== MAIN LOOP =====
def main():
    log("=" * 80)
    log(f"🚀 TRANSLATE WORKER START")
    log(f"   Model: {HF_MODEL}")
    log(f"   Batch size: {BATCH_SIZE}")
    log(f"   CPU limit: {CPU_LIMIT}%")
    log(f"   Sleep between requests: {SLEEP}s")
    log("=" * 80)
    
    try:
        conn = db_connect()
        log("✅ DB connection OK\n")
    except Exception as e:
        log(f"❌ DB connection failed: {e}")
        return 1
    
    iteration = 0
    total_processed = 0
    total_skipped = 0
    
    try:
        while RUNNING:
            iteration += 1
            log(f"\n--- Iteration #{iteration} ---")
            
            # CPU check
            cpu = get_cpu_percent()
            log(f"CPU: {cpu:.1f}% (limit: {CPU_LIMIT}%)")
            
            if not can_continue():
                log(f"⏸️ CPU too high, waiting 30s...")
                time.sleep(30)
                continue
            
            # Cikkek lekérése
            rows = get_pending_articles(conn, BATCH_SIZE)
            if not rows:
                log("💤 No pending articles, waiting 60s...")
                time.sleep(60)
                continue
            
            log(f"📥 {len(rows)} articles to translate")
            
            batch_processed = 0
            for idx, row in enumerate(rows, 1):
                # CPU check minden cikk előtt
                if not can_continue():
                    log(f"⏸️ CPU limit reached, stopping batch")
                    break
                
                art_id = row["article_id"]
                text = row["text"]
                
                if not text:
                    log(f"  [{idx}] Article #{art_id}: empty text, SKIP")
                    total_skipped += 1
                    continue
                
                # Darabolás
                parts = split_text(text)
                if not parts:
                    log(f"  [{idx}] Article #{art_id}: no chunks, SKIP")
                    total_skipped += 1
                    continue
                
                # Fordítás
                translated_parts = []
                chunk_ok = 0
                for part in parts:
                    tr = hf_infer(part)
                    if tr:
                        translated_parts.append(tr)
                        chunk_ok += 1
                    time.sleep(SLEEP)
                
                # Mentés
                final_text = "\n".join(translated_parts).strip()
                if final_text:
                    save_translation(conn, art_id, final_text)
                    log(f"  [{idx}] Article #{art_id}: OK ({chunk_ok}/{len(parts)} chunks, {len(final_text)} chars)")
                    batch_processed += 1
                    total_processed += 1
                else:
                    log(f"  [{idx}] Article #{art_id}: FAIL (no translation)")
                    total_skipped += 1
            
            log(f"✅ Batch: {batch_processed}/{len(rows)} processed")
            log(f"📊 Total: {total_processed} processed, {total_skipped} skipped")
            
            # Batch delay
            log(f"⏳ Sleeping {BATCH_SLEEP}s before next batch...")
            time.sleep(BATCH_SLEEP)
    
    except KeyboardInterrupt:
        log("⏹️ Interrupted by user")
    except Exception as e:
        log(f"❌ FATAL ERROR: {type(e).__name__}: {e}")
        import traceback
        log(traceback.format_exc())
        return 1
    finally:
        try:
            conn.close()
            log("🔒 DB connection closed")
        except:
            pass
        log("\n" + "=" * 80)
        log(f"🛑 WORKER STOPPED")
        log(f"   Total iterations: {iteration}")
        log(f"   Total processed: {total_processed}")
        log(f"   Total skipped: {total_skipped}")
        log("=" * 80)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
