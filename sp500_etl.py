#!/usr/bin/env python3
"""
S&P 500 Stock ETL Pipeline
Forráa: Wikipedia S&P 500 lista
Cél: stock_products tábla feltöltése idempotens módon

Használat:
  python sp500_etl.py --dry-run (csak log, nem módosít)
  python sp500_etl.py --full (teljes sync)
  python sp500_etl.py --validate (csak ellenőrzés)
"""

import requests
import pandas as pd
from bs4 import BeautifulSoup
import mysql.connector
from mysql.connector import Error as MySQLError
import logging
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import argparse
import sys

# ============================================================================
# LOGGING SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('sp500_etl.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# KONFIGURÁCIÓS KONSTANSOK
# ============================================================================

WIKIPEDIA_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
EXPECTED_ROW_COUNT = 503  # ±5
MYSQL_CONFIG = {
    'host': '192.168.10.100',  # Database server IP
    'user': 'webServer',
    'password': 'webServer192.168.20.100',  # webServer user jelszó
    'database': 'newscred',  # Meglévő DB
    'port': 3306,
    'use_pure': True,
}

# ============================================================================
# WIKIPEDIA PARSER
# ============================================================================

class WikipediaSP500Parser:
    """Wikipedia S&P 500 tábla parszelése"""
    
    REQUIRED_COLUMNS = ['Symbol', 'Security', 'GICSSector', 'GICS Sub-Industry']
    OPTIONAL_COLUMNS = ['CIK', 'Date added', 'Founded', 'Headquarters Location']
    
    def __init__(self):
        self.df = None
        self.errors = []
    
    def fetch_page(self) -> str:
        """Wikipédia oldal letöltése"""
        try:
            logger.info(f"📥 Wiki oldal letöltése: {WIKIPEDIA_URL}")
            
            # User-Agent header (Wikipedia blokkolja az alapértelmezést)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(WIKIPEDIA_URL, headers=headers, timeout=10)
            response.raise_for_status()
            logger.info(f"✅ Wiki letöltés OK ({len(response.content)} bytes)")
            return response.text
        except requests.RequestException as e:
            logger.error(f"❌ Wiki letöltés hiba: {e}")
            raise
    
    def parse_html_table(self, html: str) -> pd.DataFrame:
        """HTML tábla parszelése"""
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # Első táblázat keresése
            table = soup.find('table', {'class': 'wikitable'})
            if not table:
                raise ValueError("Wikitable nem található!")
            
            logger.info("📊 Tábla parszelése...")
            
            # Oszlopok
            headers = []
            for th in table.find_all('th'):
                headers.append(th.get_text(strip=True))
            
            # Sorok
            rows = []
            for tr in table.find_all('tr')[1:]:  # Skip header
                cols = []
                for td in tr.find_all('td'):
                    cols.append(td.get_text(strip=True))
                if cols:
                    rows.append(cols)
            
            # DataFrame
            df = pd.DataFrame(rows, columns=headers)
            logger.info(f"✅ Parszelés OK: {len(df)} sor, {len(df.columns)} oszlop")
            
            return df
        
        except Exception as e:
            logger.error(f"❌ Parszelés hiba: {e}")
            raise
    
    def validate_columns(self, df: pd.DataFrame) -> bool:
        """Oszlopok validálása"""
        missing = [col for col in self.REQUIRED_COLUMNS if col not in df.columns]
        
        if missing:
            logger.error(f"❌ Hiányzó oszlopok: {missing}")
            return False
        
        logger.info(f"✅ Oszlopok OK: {', '.join(df.columns)}")
        return True
    
    def validate_row_count(self, df: pd.DataFrame) -> bool:
        """Sorok száma validálása"""
        row_count = len(df)
        min_count = EXPECTED_ROW_COUNT - 5
        max_count = EXPECTED_ROW_COUNT + 5
        
        if min_count <= row_count <= max_count:
            logger.info(f"✅ Sorok száma OK: {row_count} (várt: ~{EXPECTED_ROW_COUNT})")
            return True
        else:
            logger.error(f"❌ Sorok száma gyanús: {row_count} (várt: {min_count}-{max_count})")
            return False
    
    def normalize_ticker(self, ticker: str) -> str:
        """
        Ticker normalizálása
        - Pont (.) MEGTARTÁSA (S&P 500 formátum)
        - Szóköz eltávolítása
        - Nagybetűsítés
        """
        ticker = ticker.strip().upper()
        logger.debug(f"Ticker normalizálva: '{ticker}'")
        return ticker
    
    def parse(self) -> pd.DataFrame:
        """Teljes parse flow"""
        html = self.fetch_page()
        df = self.parse_html_table(html)
        
        if not self.validate_columns(df):
            raise ValueError("Oszlop validáció sikertelen!")
        
        if not self.validate_row_count(df):
            logger.warning("⚠️ Sorok száma gyanús, de folytatunk...")
        
        # Normalizálás
        df['Symbol'] = df['Symbol'].apply(self.normalize_ticker)
        
        self.df = df
        logger.info(f"✅ Parse kész: {len(df)} érvényes sor")
        
        return df

# ============================================================================
# MYSQL ADATBÁZIS RÉTEG
# ============================================================================

class StockDatabase:
    """MySQL adatbázis műveletek"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.connection = None
        self.cursor = None
    
    def connect(self) -> bool:
        """Csatlakozás MySQL-hez"""
        try:
            self.connection = mysql.connector.connect(**self.config)
            self.cursor = self.connection.cursor(dictionary=True)
            logger.info(f"✅ MySQL csatlakozás: {self.config['host']}/{self.config['database']}")
            return True
        except MySQLError as e:
            logger.error(f"❌ MySQL hiba: {e}")
            return False
    
    def disconnect(self):
        """Csatlakozás bezárása"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
            logger.info("✅ MySQL csatlakozás bezárva")
    
    def get_exchange_id(self, exchange_name: str) -> Optional[int]:
        """Exchange ID keresése (NYSE/NASDAQ)"""
        query = """
        SELECT id FROM stock_exchanges 
        WHERE exchange_name = %s AND status = 'active'
        LIMIT 1
        """
        self.cursor.execute(query, (exchange_name,))
        result = self.cursor.fetchone()
        
        if result:
            logger.debug(f"Exchange ID '{exchange_name}': {result['id']}")
            return result['id']
        
        logger.warning(f"⚠️ Exchange nem található: {exchange_name}")
        return None
    
    def get_existing_stock(self, ticker: str, exchange_id: int) -> Optional[Dict]:
        """Meglévő részvény keresése"""
        query = """
        SELECT id, company_name, sector, industry, is_sp500, updated_at
        FROM stock_products 
        WHERE ticker = %s AND exchange_id = %s
        """
        self.cursor.execute(query, (ticker, exchange_id))
        return self.cursor.fetchone()
    
    def upsert_stock(self, stock_data: Dict, exchange_id: int, dry_run: bool = False) -> Tuple[bool, str]:
        """
        Idempotent UPSERT
        
        Logic:
          - Ha EXISTS: UPDATE (csak ha megváltozott)
          - Ha NEM EXISTS: INSERT
          - is_sp500 = 1 mindkét esetben
        """
        
        ticker = stock_data['ticker']
        company_name = stock_data['company_name']
        sector = stock_data.get('sector')
        industry = stock_data.get('industry')
        
        existing = self.get_existing_stock(ticker, exchange_id)
        
        if existing:
            # UPDATE path
            existing_id = existing['id']
            
            # Megváltozott-e az adat?
            changed = (
                existing['company_name'] != company_name or
                existing['sector'] != sector or
                existing['industry'] != industry or
                existing['is_sp500'] != 1
            )
            
            if changed:
                if dry_run:
                    logger.info(f"[DRY-RUN] UPDATE {ticker}: {company_name}")
                    return True, "UPDATE (dry-run)"
                
                update_query = """
                UPDATE stock_products 
                SET company_name = %s, sector = %s, industry = %s, 
                    is_sp500 = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """
                self.cursor.execute(update_query, 
                    (company_name, sector, industry, existing_id))
                self.connection.commit()
                
                logger.info(f"✏️ UPDATE: {ticker} ({existing_id})")
                return True, "UPDATE"
            else:
                logger.debug(f"⏭️ SKIP: {ticker} (nincs változás)")
                return False, "SKIP"
        
        else:
            # INSERT path
            if dry_run:
                logger.info(f"[DRY-RUN] INSERT {ticker}: {company_name}")
                return True, "INSERT (dry-run)"
            
            insert_query = """
            INSERT INTO stock_products 
            (exchange_id, ticker, company_name, sector, industry, 
             currency, status, is_sp500, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'USD', 'active', 1, 
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
            self.cursor.execute(insert_query,
                (exchange_id, ticker, company_name, sector, industry))
            self.connection.commit()
            
            logger.info(f"➕ INSERT: {ticker}")
            return True, "INSERT"

# ============================================================================
# ETL ORCHESTRATOR
# ============================================================================

class SP500ETLPipeline:
    """ETL Pipeline vezérlő"""
    
    def __init__(self, mysql_config: Dict, dry_run: bool = False, validate_only: bool = False):
        self.mysql_config = mysql_config
        self.dry_run = dry_run
        self.validate_only = validate_only
        self.db = None
        self.parser = None
        self.stats = {
            'total_rows': 0,
            'inserted': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
        }
    
    def run(self) -> bool:
        """ETL teljes futtatása"""
        logger.info("=" * 70)
        logger.info("🚀 S&P 500 ETL Pipeline INDÍTÁS")
        logger.info(f"   Mód: {'DRY-RUN' if self.dry_run else 'FULL'}")
        logger.info("=" * 70)
        
        try:
            # 1. Wiki parse
            self.parser = WikipediaSP500Parser()
            df = self.parser.parse()
            self.stats['total_rows'] = len(df)
            
            if self.validate_only:
                logger.info("✅ Validáció OK, nincs DB módosítás")
                return True
            
            # 2. DB csatlakozás
            self.db = StockDatabase(self.mysql_config)
            if not self.db.connect():
                return False
            
            # 3. NYSE és NASDAQ feldolgozása
            for exchange_name in ['NYSE', 'NASDAQ']:
                self._process_exchange(df, exchange_name)
            
            self.db.disconnect()
            
            # 4. Statisztika
            self._print_stats()
            
            logger.info("=" * 70)
            logger.info("✅ ETL Pipeline KÉSZ")
            logger.info("=" * 70)
            
            return True
        
        except Exception as e:
            logger.error(f"❌ ETL hiba: {e}", exc_info=True)
            if self.db:
                self.db.disconnect()
            return False
    
    def _process_exchange(self, df: pd.DataFrame, exchange_name: str):
        """Exchange feldolgozása"""
        logger.info(f"\n📊 {exchange_name} feldolgozása...")
        
        exchange_id = self.db.get_exchange_id(exchange_name)
        if not exchange_id:
            logger.warning(f"⚠️ {exchange_name} kihagyva (nincs DB-ben)")
            return
        
        # NYSE/NASDAQ szűrés (Wikipedia nem jelöli, így mindent feldolgozunk)
        for idx, row in df.iterrows():
            stock_data = {
                'ticker': row['Symbol'],
                'company_name': row['Security'],
                'sector': row.get('GICSSector'),  # Updated column name (no space)
                'industry': row.get('GICS Sub-Industry'),
            }
            
            success, action = self.db.upsert_stock(
                stock_data, exchange_id, self.dry_run
            )
            
            if success:
                if action == 'INSERT' or action == 'INSERT (dry-run)':
                    self.stats['inserted'] += 1
                elif action == 'UPDATE' or action == 'UPDATE (dry-run)':
                    self.stats['updated'] += 1
                elif action == 'SKIP':
                    self.stats['skipped'] += 1
            else:
                self.stats['errors'] += 1
    
    def _print_stats(self):
        """Statisztika kiírása"""
        logger.info("\n" + "=" * 70)
        logger.info("📈 FUTÁS STATISZTIKA")
        logger.info("=" * 70)
        logger.info(f"  Feldolgozott sorok:  {self.stats['total_rows']}")
        logger.info(f"  Beszúrt:             {self.stats['inserted']}")
        logger.info(f"  Frissített:          {self.stats['updated']}")
        logger.info(f"  Átugrott:            {self.stats['skipped']}")
        logger.info(f"  Hibák:               {self.stats['errors']}")
        logger.info("=" * 70)

# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='S&P 500 ETL Pipeline - Wikipedia → MySQL'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Csak logolás, nincs DB módosítás'
    )
    parser.add_argument(
        '--validate',
        action='store_true',
        help='Csak Wiki validáció, nincs DB módosítás'
    )
    parser.add_argument(
        '--full',
        action='store_true',
        help='Teljes sync (alapértelmezett)'
    )
    parser.add_argument(
        '--host',
        default='192.168.10.100',
        help='MySQL host (default: 192.168.10.100)'
    )
    parser.add_argument(
        '--user',
        default='webServer',
        help='MySQL user (default: webServer)'
    )
    parser.add_argument(
        '--password',
        default='webServer192.168.20.100',
        help='MySQL password'
    )
    parser.add_argument(
        '--database',
        default='newscred',
        help='Database name (default: newscred)'
    )
    
    args = parser.parse_args()
    
    # Config módosítása
    config = MYSQL_CONFIG.copy()
    config['host'] = args.host
    config['user'] = args.user
    config['password'] = args.password
    config['database'] = args.database
    
    # ETL futtatás
    pipeline = SP500ETLPipeline(
        mysql_config=config,
        dry_run=args.dry_run,
        validate_only=args.validate
    )
    
    success = pipeline.run()
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
