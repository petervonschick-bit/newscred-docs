#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_worker.py - PRODUCTION verzió
Háttérben folyamatos claims/entities/sentiment feldolgozás
SystemD-vel futtatható, graceful shutdown
"""

import os
import json
import time
import re
import hashlib
import pymysql
import psutil
import signal
import sys
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# ===== CONFIG =====
CONFIG_FILE = "/opt/newscred/extract_config.json"
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

DB_CFG_FILE = CONFIG["database"]["config_file"]
HF_TOKEN = CONFIG["huggingface"]["token"]
NLI_MODEL = CONFIG["extraction"]["models"]["nli"]
NER_MODEL = CONFIG["extraction"]["models"]["ner"]
SENTIMENT_MODEL = CONFIG["extraction"]["models"]["sentiment"]
CPU_LIMIT = CONFIG["performance"]["cpu_limit_percent"]
BATCH_SIZE = CONFIG["performance"]["batch_size_prod"]
SLEEP = CONFIG["performance"]["sleep_between_requests"]
BATCH_SLEEP = CONFIG["performance"]["sleep_between_batches"]
LOG_DIR = CONFIG["logging"]["log_dir"]
LOG_FILE = os.path.join(LOG_DIR, CONFIG["logging"]["log_file_worker"])

# Entity és sentiment config
ENTITY_TYPES = CONFIG["extraction"]["entity_types"]
SENTIMENT_ONLY_DB = CONFIG["extraction"]["sentiment_only_db_companies"]
NLI_LABELS = CONFIG["extraction"]["nli_labels"]
NLI_TARGET = CONFIG["extraction"]["nli_target_labels"]

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
def normalize_for_hash(s: str) -> str:
    """normalizálás hash-hez"""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def make_claim_hash(article_id: int, claim_text: str) -> bytes:
    """SHA-256 hash 32 byte"""
    base = f"{article_id}|{normalize_for_hash(claim_text)}"
    return hashlib.sha256(base.encode("utf-8")).digest()

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

# ===== MODELS =====
def load_models():
    """Modellek betöltése (singleton-szerűen)"""
    log("📥 Loading models...")
    try:
        from transformers import pipeline
        
        nli_pipe = pipeline("zero-shot-classification", model=NLI_MODEL, device=-1)
        log("✅ NLI model loaded")
        
        ner_pipe = pipeline("ner", model=NER_MODEL, device=-1, aggregation_strategy="simple")
        log("✅ NER model loaded")
        
        sentiment_pipe = pipeline("sentiment-analysis", model=SENTIMENT_MODEL, device=-1)
        log("✅ Sentiment model loaded")
        
        return nli_pipe, ner_pipe, sentiment_pipe
    except Exception as e:
        log(f"❌ Model loading error: {e}")
        return None, None, None

# ===== NLI - CLAIM DETECTION =====
def extract_claims(text: str, nli_pipe) -> List[str]:
    """Mondatokból tényeket nyer ki (NLI)"""
    if not nli_pipe:
        return []
    
    sent_split = re.compile(r"(?<=[.!?…])\s+")
    sentences = sent_split.split(text)
    claims = []
    
    for s in sentences:
        s_clean = s.strip()
        
        if len(s_clean.split()) < 5 or len(s_clean) > 400:
            continue
        
        try:
            result = nli_pipe(
                s_clean,
                candidate_labels=NLI_LABELS,
                multi_label=False,
            )
            
            top_label = result.get("labels", ["opinion"])[0]
            if top_label in NLI_TARGET:
                claims.append(s_clean)
        except Exception as e:
            log(f"⚠️ NLI error: {e}")
            continue
        
        time.sleep(SLEEP)
    
    return claims

# ===== NER - ENTITY EXTRACTION =====
def extract_entities(text: str, ner_pipe) -> List[Dict]:
    """Entitások kinyerése (NER)"""
    if not ner_pipe:
        return []
    
    try:
        entities_raw = ner_pipe(text)
        entities = []
        
        for e in entities_raw:
            entity_type = e.get("entity_group", "O")
            
            if entity_type not in ENTITY_TYPES:
                continue
            
            entities.append({
                "type": entity_type,
                "text": e.get("word", "").strip(),
                "score": round(e.get("score", 0), 3),
                "start": e.get("start", 0),
                "end": e.get("end", 0)
            })
        
        return entities
    except Exception as e:
        log(f"⚠️ NER error: {e}")
        return []

# ===== SENTIMENT - COMPANY SENTIMENT =====
def get_db_companies(conn) -> Dict[str, int]:
    """BÉT cégek lekérése"""
    q = "SELECT id, company_name FROM stock_products WHERE status = 'active'"
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(q)
        rows = cur.fetchall()
    
    companies = {}
    for r in rows:
        companies[r["company_name"].lower()] = r["id"]
    
    return companies

def find_company_in_claim(claim_text: str, companies: Dict) -> Optional[Tuple[str, int]]:
    """Cégnév keresése"""
    claim_lower = claim_text.lower()
    
    for company_name, company_id in companies.items():
        if company_name in claim_lower:
            return (company_name, company_id)
    
    return None

def analyze_sentiment(text: str, sentiment_pipe) -> Optional[str]:
    """Sentiment analízis"""
    if not sentiment_pipe or len(text) > 512:
        return None
    
    try:
        result = sentiment_pipe(text)
        if result:
            label = result[0].get("label", "NEUTRAL").lower()
            return "positive" if label == "positive" else ("negative" if label == "negative" else "neutral")
    except Exception as e:
        log(f"⚠️ Sentiment error: {e}")
    
    return None

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

def get_pending_articles(conn, limit: int = 50) -> List[Dict]:
    """feldolgozandó cikkek"""
    q = """
        SELECT a.id AS article_id, t.text as body
        FROM article_texts t
        JOIN articles a ON a.id = t.article_id
        LEFT JOIN claims c ON c.article_id = a.id
        WHERE c.article_id IS NULL 
          AND a.status = 0
          AND t.text IS NOT NULL 
          AND LENGTH(t.text) > %s
        ORDER BY a.id DESC
        LIMIT %s
    """
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(q, (150, limit))
        return cur.fetchall()

def insert_claim(conn, article_id: int, claim_text: str, entities_list: List[Dict]):
    """claim mentése"""
    claim_hash = make_claim_hash(article_id, claim_text)
    entities_json = json.dumps({"entities": entities_list}, ensure_ascii=False)
    
    q = """
        INSERT INTO claims (article_id, claim, claim_hash, entities)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          entities = VALUES(entities)
    """
    
    with conn.cursor() as cur:
        cur.execute(q, (article_id, claim_text, claim_hash, entities_json))
    
    q2 = "SELECT LAST_INSERT_ID() as cid"
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(q2)
        result = cur.fetchone()
    
    return result.get("cid") if result else None

def insert_entities(conn, claim_id: int, entities_list: List[Dict]):
    """entitások mentése"""
    if not entities_list or not claim_id:
        return
    
    q = """
        INSERT INTO entities (claim_id, entity_type, entity_text, start_char, end_char, confidence)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    
    with conn.cursor() as cur:
        for e in entities_list:
            cur.execute(q, (
                claim_id,
                e.get("type"),
                e.get("text")[:255],
                e.get("start"),
                e.get("end"),
                e.get("score")
            ))

def insert_company_sentiment(conn, claim_id: int, company_id: int, sentiment: str, mention_text: str):
    """cégsentiemnt mentése"""
    q = """
        INSERT INTO company_sentiment (claim_id, company_id, sentiment_label, mention_text)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          sentiment_label = VALUES(sentiment_label)
    """
    
    with conn.cursor() as cur:
        cur.execute(q, (claim_id, company_id, sentiment, mention_text[:255]))

# ===== MAIN LOOP =====
def main():
    log("=" * 80)
    log(f"🚀 EXTRACT WORKER START")
    log(f"   NLI: {NLI_MODEL}")
    log(f"   NER: {NER_MODEL}")
    log(f"   Sentiment: {SENTIMENT_MODEL}")
    log(f"   Batch size: {BATCH_SIZE}")
    log(f"   CPU limit: {CPU_LIMIT}%")
    log("=" * 80)
    
    # Modellek betöltése
    nli_pipe, ner_pipe, sentiment_pipe = load_models()
    
    if not nli_pipe:
        log("❌ NLI model required!")
        return 1
    
    log("")
    
    try:
        conn = db_connect()
        log("✅ DB connection OK\n")
    except Exception as e:
        log(f"❌ DB connection failed: {e}")
        return 1
    
    iteration = 0
    total_claims = 0
    total_entities = 0
    total_sentiments = 0
    
    try:
        # DB cégek gyorsítótárazása
        companies = get_db_companies(conn)
        log(f"📊 {len(companies)} companies cached\n")
        
        while RUNNING:
            iteration += 1
            log(f"\n--- Iteration #{iteration} ---")
            
            # CPU check
            cpu = get_cpu_percent()
            log(f"CPU: {cpu:.1f}% (limit: {CPU_LIMIT}%)")
            
            if not can_continue():
                log(f"⏸️ CPU too high, waiting 60s...")
                time.sleep(60)
                continue
            
            # Cikkek lekérése
            rows = get_pending_articles(conn, BATCH_SIZE)
            if not rows:
                log("💤 No pending articles, waiting 120s...")
                time.sleep(120)
                continue
            
            log(f"📥 {len(rows)} articles to process")
            
            batch_claims = 0
            for idx, row in enumerate(rows, 1):
                # CPU check minden cikk előtt
                if not can_continue():
                    log(f"⏸️ CPU limit reached, stopping batch")
                    break
                
                art_id = row["article_id"]
                body = row["body"]
                
                if not body or len(body) < 150:
                    log(f"  [{idx}] Article #{art_id}: too short, SKIP")
                    continue
                
                # Claims extraction
                claims = extract_claims(body, nli_pipe)
                if not claims:
                    log(f"  [{idx}] Article #{art_id}: no claims, SKIP")
                    continue
                
                # Claimek feldolgozása
                art_claims = 0
                art_entities = 0
                art_sentiments = 0
                
                for claim_text in claims:
                    entities_list = extract_entities(claim_text, ner_pipe)
                    claim_id = insert_claim(conn, art_id, claim_text, entities_list)
                    
                    if not claim_id:
                        continue
                    
                    if entities_list:
                        insert_entities(conn, claim_id, entities_list)
                        art_entities += len(entities_list)
                    
                    # Sentiment (csak DB cégeknél)
                    if SENTIMENT_ONLY_DB and ner_pipe:
                        company_match = find_company_in_claim(claim_text, companies)
                        if company_match:
                            company_name, company_id = company_match
                            sentiment = analyze_sentiment(claim_text, sentiment_pipe)
                            if sentiment:
                                insert_company_sentiment(conn, claim_id, company_id, sentiment, company_name)
                                art_sentiments += 1
                    
                    art_claims += 1
                    time.sleep(SLEEP)
                
                if art_claims > 0:
                    log(f"  [{idx}] Article #{art_id}: {art_claims} claims, {art_entities} entities, {art_sentiments} sentiments")
                    batch_claims += art_claims
                    total_claims += art_claims
                    total_entities += art_entities
                    total_sentiments += art_sentiments
            
            log(f"✅ Batch: {batch_claims} claims processed")
            log(f"📊 Total: {total_claims} claims, {total_entities} entities, {total_sentiments} sentiments")
            
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
        log(f"   Total claims: {total_claims}")
        log(f"   Total entities: {total_entities}")
        log(f"   Total sentiments: {total_sentiments}")
        log("=" * 80)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
