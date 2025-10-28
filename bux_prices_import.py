#!/usr/bin/env python3
"""
BUX Stock Prices Import Script
Letölti a legfrissebb napi árfolyamokat (OHLC) a BUX részvényekhez
"""

import pymysql
import yfinance as yf
import logging
from datetime import datetime, timedelta
import sys

# Logging beállítás
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/bux_prices_import.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Adatbázis konfiguráció
DB_CONFIG = {
    'host': '192.168.10.100',
    'user': 'webServer',
    'password': 'webServer192.168.20.100',
    'database': 'newscred',
    'charset': 'utf8mb4'
}

# Yahoo Finance ticker mapping (BÉT ticker → Yahoo ticker)
YAHOO_TICKER_MAP = {
    'OTP': 'OTP.BD',
    'MOL': 'MOL.BD',
    'RICHTER': 'RICHT.BD',
    'MTELEKOM': 'MTELEKOM.BD',
    '4IG': '4IG.BD',
    'OPUS': 'OPUS.BD',
    'ANY': 'ANY.BD',
    'BIF': 'BIF.BD',
    'ALTEO': 'ALTEO.BD',
    'WABERERS': 'WABERERS.BD',
    'AUTOWALLIS': 'AUTOWALLIS.BD',
    'MASTERPLAST': 'MASTERPLAST.BD',
    'GSPARK': 'GSPARK.BD',
    'APPENINN': 'APPENINN.BD',
    'CIGPANNONIA': 'CIGPANNONIA.BD',
    'PANNERGY': 'PANNERGY.BD',
    'DELTA': 'DELTA.BD'
}


def get_bet_products(connection):
    """BÉT termékek lekérése az adatbázisból"""
    
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute("""
            SELECT sp.id, sp.ticker, sp.company_name
            FROM stock_products sp
            JOIN stock_exchanges se ON sp.exchange_id = se.id
            WHERE se.mic_code = 'XBUD' OR se.exchange_name LIKE '%Budapest%'
            ORDER BY sp.ticker
        """)
        return cursor.fetchall()


def fetch_latest_price(ticker, yahoo_ticker):
    """Legfrissebb ár letöltése Yahoo Finance-ről"""
    
    try:
        # Yahoo Finance ticker objektum
        stock = yf.Ticker(yahoo_ticker)
        
        # Legutóbbi 5 nap adatai (biztosra megyünk)
        hist = stock.history(period='5d')
        
        if hist.empty:
            logger.warning(f"⚠ {ticker}: Nincs elérhető adat")
            return None
        
        # Legfrissebb nap
        latest = hist.iloc[-1]
        latest_date = hist.index[-1].date()
        
        price_data = {
            'trade_date': latest_date,
            'open_price': round(float(latest['Open']), 2),
            'high_price': round(float(latest['High']), 2),
            'low_price': round(float(latest['Low']), 2),
            'close_price': round(float(latest['Close']), 2),
            'volume': int(latest['Volume']) if latest['Volume'] > 0 else 0
        }
        
        logger.info(f"✓ {ticker:12} | {latest_date} | Close: {price_data['close_price']:>10,.2f} HUF | Vol: {price_data['volume']:>12,}")
        return price_data
        
    except Exception as e:
        logger.error(f"✗ {ticker}: Hiba az árfolyam letöltésekor - {e}")
        return None


def import_price(connection, product_id, ticker, price_data):
    """Árfolyam mentése az adatbázisba"""
    
    try:
        with connection.cursor() as cursor:
            sql = """
                INSERT INTO stock_prices
                (product_id, trade_date, open_price, high_price, low_price, close_price, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open_price = VALUES(open_price),
                    high_price = VALUES(high_price),
                    low_price = VALUES(low_price),
                    close_price = VALUES(close_price),
                    volume = VALUES(volume),
                    updated_at = CURRENT_TIMESTAMP
            """
            
            cursor.execute(sql, (
                product_id,
                price_data['trade_date'],
                price_data['open_price'],
                price_data['high_price'],
                price_data['low_price'],
                price_data['close_price'],
                price_data['volume']
            ))
            
            return cursor.rowcount
            
    except Exception as e:
        logger.error(f"✗ DB hiba {ticker} mentésekor: {e}")
        return 0


def display_summary(connection):
    """Összesítő statisztika"""
    
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        # Legfrissebb árfolyamok
        cursor.execute("""
            SELECT 
                sp.ticker,
                sp.company_name,
                spr.trade_date,
                spr.close_price,
                spr.volume
            FROM stock_products sp
            JOIN stock_prices spr ON sp.id = spr.product_id
            JOIN stock_exchanges se ON sp.exchange_id = se.id
            WHERE se.mic_code = 'XBUD'
            AND spr.trade_date = (
                SELECT MAX(trade_date) FROM stock_prices WHERE product_id = sp.id
            )
            ORDER BY sp.ticker
            LIMIT 5
        """)
        
        logger.info("\n" + "=" * 80)
        logger.info("📊 BÉT Top 5 részvény - Legfrissebb árfolyamok")
        logger.info("=" * 80)
        
        for row in cursor.fetchall():
            logger.info(
                f"{row['ticker']:12} | {row['trade_date']} | "
                f"{row['close_price']:>10,.2f} HUF | "
                f"Vol: {row['volume']:>12,}"
            )
        
        # Összesített statisztika
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT sp.id) as product_count,
                COUNT(*) as price_count,
                MAX(spr.trade_date) as latest_date
            FROM stock_products sp
            JOIN stock_exchanges se ON sp.exchange_id = se.id
            LEFT JOIN stock_prices spr ON sp.id = spr.product_id
            WHERE se.mic_code = 'XBUD'
        """)
        
        stats = cursor.fetchone()
        logger.info("\n" + "=" * 80)
        logger.info(f"📈 Összesítés:")
        logger.info(f"  Termékek száma: {stats['product_count']}")
        logger.info(f"  Árfolyam rekordok: {stats['price_count']}")
        logger.info(f"  Legfrissebb dátum: {stats['latest_date']}")
        logger.info("=" * 80)


def main():
    """Fő import folyamat"""
    
    logger.info("=" * 80)
    logger.info("📈 BUX Latest Prices Import Script indítása")
    logger.info("=" * 80)
    
    # Adatbázis kapcsolat
    try:
        logger.info("Csatlakozás az adatbázishoz...")
        connection = pymysql.connect(**DB_CONFIG)
        logger.info("✓ Sikeres csatlakozás\n")
    except Exception as e:
        logger.error(f"✗ Adatbázis kapcsolódási hiba: {e}")
        return 1
    
    try:
        # BÉT termékek lekérése
        products = get_bet_products(connection)
        logger.info(f"📦 {len(products)} BÉT termék találva\n")
        
        if not products:
            logger.error("✗ Nincs BÉT termék az adatbázisban!")
            return 1
        
        # Árfolyamok letöltése és mentése
        logger.info("=" * 80)
        logger.info("📥 Árfolyamok letöltése Yahoo Finance-ről...")
        logger.info("=" * 80)
        
        success_count = 0
        insert_count = 0
        update_count = 0
        error_count = 0
        
        for product in products:
            ticker = product['ticker']
            product_id = product['id']
            
            # Yahoo ticker mapping
            yahoo_ticker = YAHOO_TICKER_MAP.get(ticker)
            
            if not yahoo_ticker:
                logger.warning(f"⚠ {ticker}: Nincs Yahoo ticker mapping")
                error_count += 1
                continue
            
            # Árfolyam letöltése
            price_data = fetch_latest_price(ticker, yahoo_ticker)
            
            if not price_data:
                error_count += 1
                continue
            
            # Mentés adatbázisba
            rowcount = import_price(connection, product_id, ticker, price_data)
            
            if rowcount == 1:
                insert_count += 1
            elif rowcount == 2:
                update_count += 1
            
            success_count += 1
        
        connection.commit()
        
        # Összesítés
        logger.info("\n" + "=" * 80)
        logger.info("📊 Import statisztika:")
        logger.info(f"  Sikeres letöltés: {success_count}/{len(products)}")
        logger.info(f"  Új árfolyam rekord: {insert_count}")
        logger.info(f"  Frissített rekord: {update_count}")
        logger.info(f"  Hibák: {error_count}")
        logger.info("=" * 80)
        
        # Részletes összesítő
        display_summary(connection)
        
        logger.info("\n✅ Import sikeresen befejezve!")
        return 0 if error_count == 0 else 1
        
    except Exception as e:
        logger.error(f"✗ Váratlan hiba az import során: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        connection.close()
        logger.info("\n🔒 Adatbázis kapcsolat lezárva")


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
