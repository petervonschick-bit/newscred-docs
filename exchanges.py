# GUI Routes - Stock Exchanges
from flask import Blueprint, url_for
import logging
from .helpers import render_page, q_all, q_one

log = logging.getLogger("gui")
exchanges_bp = Blueprint('exchanges', __name__)

@exchanges_bp.route("/exchanges")
def exchanges():
    try:
        # Összes aktív tőzsde
        exchanges = q_all("""
          SELECT id, exchange_name, country_name, city, website_url, status
          FROM stock_exchanges
          WHERE status='active'
          ORDER BY exchange_name
        """)
        
        html = ""
        
        # Minden tőzsde
        for exch in exchanges:
            exch_id = exch["id"]
            exch_name = exch["exchange_name"]
            country = exch["country_name"] or "N/A"
            
            html += f"""
            <div class='card'>
              <div class='k'>💹 {exch_name} ({country})</div>
            """
            
            # Cégek ebben a tőzsdén - vannak cikkek → ABC sorrend
            # Első: vannak cikkekben említve
            products_with_articles = q_all("""
              SELECT DISTINCT sp.id, sp.company_name, sp.ticker, sp.isin, sp.sector, 0 as no_articles
              FROM stock_products sp
              WHERE sp.exchange_id=%s AND sp.status='active'
              AND (EXISTS (
                SELECT 1 FROM claims c 
                WHERE c.claim COLLATE utf8mb4_unicode_ci LIKE CONCAT('%%', sp.company_name, '%%')
              ) OR EXISTS (
                SELECT 1 FROM claims c 
                WHERE c.claim COLLATE utf8mb4_unicode_ci LIKE CONCAT('%%', sp.ticker, '%%')
              ))
              ORDER BY sp.company_name
            """, (exch_id,))
            
            # Második: nincsenek cikkekben
            products_without = q_all("""
              SELECT sp.id, sp.company_name, sp.ticker, sp.isin, sp.sector, 1 as no_articles
              FROM stock_products sp
              WHERE sp.exchange_id=%s AND sp.status='active'
              AND NOT EXISTS (
                SELECT 1 FROM claims c 
                WHERE c.claim COLLATE utf8mb4_unicode_ci LIKE CONCAT('%%', sp.company_name, '%%')
                   OR c.claim COLLATE utf8mb4_unicode_ci LIKE CONCAT('%%', sp.ticker, '%%')
              )
              ORDER BY sp.company_name
            """, (exch_id,))
            
            products = products_with_articles + products_without
            
            if products:
                html += "<table style='font-size:13px; margin-top:10px;'><tbody>"
                
                for prod in products:
                    prod_id = prod["id"]
                    company_name = prod["company_name"]
                    ticker = prod["ticker"]
                    isin = prod["isin"] or "N/A"
                    sector = prod["sector"] or ""
                    
                    # Utolsó 4 árfolyam adat
                    prices = q_all("""
                      SELECT open_price, high_price, low_price, close_price, trade_date
                      FROM stock_prices
                      WHERE product_id=%s
                      ORDER BY trade_date DESC
                      LIMIT 4
                    """, (prod_id,))
                    
                    # Trend számítás (utolsó 2 adat: nyitó vs záró)
                    trend_html = ""
                    if len(prices) >= 2:
                        latest = prices[0]
                        open_price = float(latest["open_price"])
                        close_price = float(latest["close_price"])
                        
                        if close_price > open_price:
                            trend_html = "📈 <span style='color:#4ade80;'>↑</span>"
                        elif close_price < open_price:
                            trend_html = "📉 <span style='color:#f87171;'>↓</span>"
                        else:
                            trend_html = "➡️"
                    
                    # Árfolyam adatok
                    price_info = ""
                    if prices:
                        p = prices[0]
                        price_info = f"""
                        O: {p['open_price']} | H: {p['high_price']} | 
                        L: {p['low_price']} | C: {p['close_price']}
                        """
                    
                    html += f"""
                    <tr>
                      <td style='padding:8px; border-bottom:1px solid rgba(255,255,255,.06);'>
                        <div><b>{company_name}</b></div>
                        <div style='font-size:11px; color:var(--muted);'>{ticker} | {isin}</div>
                        <div style='font-size:11px; margin-top:4px;'>{price_info}</div>
                        <div style='margin-top:6px;'>{trend_html} <a class='btn' href='{url_for('exchanges.stock_detail', product_id=prod_id)}'>Részletek →</a></div>
                      </td>
                    </tr>
                    """
                
                html += "</tbody></table>"
            else:
                html += "<p style='color:var(--muted);'>Nincsenek aktív cégek.</p>"
            
            html += "</div>"
        
        if not exchanges:
            html = "<div class='card' style='color:red;'>Nincsenek aktív tőzsdék az adatbázisban.</div>"
        
        return render_page(html, active="exchanges", title="Stock Exchanges")
    except Exception as e:
        log.exception("exchanges error")
        return f"<div class='card' style='color:red;'><b>Hiba:</b> {str(e)}</div>", 500

@exchanges_bp.route("/stock/<int:product_id>")
def stock_detail(product_id: int):
    try:
        # Cég adatok
        prod = q_one("""
          SELECT sp.*, se.exchange_name
          FROM stock_products sp
          LEFT JOIN stock_exchanges se ON se.id=sp.exchange_id
          WHERE sp.id=%s
        """, (product_id,))
        
        if not prod:
            return "Cég nem található", 404
        
        company_name = prod["company_name"]
        ticker = prod["ticker"]
        isin = prod["isin"]
        sector = prod["sector"]
        exchange_name = prod["exchange_name"]
        
        # Összes árfolyam adat időben visszafelé
        prices = q_all("""
          SELECT * FROM stock_prices
          WHERE product_id=%s
          ORDER BY trade_date DESC
          LIMIT 30
        """, (product_id,))
        
        # Cikkek, amelyekben ez a cég szerepel
        articles = q_all("""
          SELECT DISTINCT a.id, a.title, a.created_at, c.claim
          FROM articles a
          JOIN claims c ON c.article_id=a.id
          WHERE c.claim COLLATE utf8mb4_unicode_ci LIKE %s
             OR c.claim COLLATE utf8mb4_unicode_ci LIKE %s
          ORDER BY a.created_at DESC
          LIMIT 20
        """, (f"%{company_name}%", f"%{ticker}%"))
        
        html = f"""
        <div class='card'>
          <div class='k'>📈 {company_name}</div>
          <div><b>Ticker:</b> {ticker}</div>
          <div><b>ISIN:</b> {isin}</div>
          <div><b>Szektor:</b> {sector or 'N/A'}</div>
          <div><b>Tőzsde:</b> {exchange_name or 'N/A'}</div>
        </div>
        
        <div class='card'>
          <div class='k'>Árfolyam történet (utolsó 30 nap)</div>
          <table style='font-size:12px;'>
            <thead>
              <tr>
                <th>Dátum</th>
                <th>Nyitó</th>
                <th>Max</th>
                <th>Min</th>
                <th>Záró</th>
                <th>Trend</th>
              </tr>
            </thead>
            <tbody>
        """
        
        for price in prices:
            trade_date = price["trade_date"]
            open_p = price["open_price"]
            high_p = price["high_price"]
            low_p = price["low_price"]
            close_p = price["close_price"]
            
            trend = "↑ 📈" if float(close_p) > float(open_p) else ("↓ 📉" if float(close_p) < float(open_p) else "➡️")
            
            html += f"""
            <tr>
              <td>{trade_date}</td>
              <td>{open_p}</td>
              <td>{high_p}</td>
              <td>{low_p}</td>
              <td>{close_p}</td>
              <td>{trend}</td>
            </tr>
            """
        
        html += """
            </tbody>
          </table>
        </div>
        """
        
        # Cikkek
        if articles:
            html += """
            <div class='card'>
              <div class='k'>🗞️ Cikkek, amelyekben szerepel</div>
              <ul style='margin:10px 0; padding-left:20px;'>
            """
            for art in articles:
                aid = art["id"]
                title = art["title"]
                created = art["created_at"]
                html += f"<li><a href='{url_for('article_one.article_one', aid=aid)}'>{title}</a> ({created})</li>"
            html += "</ul></div>"
        
        return render_page(html, active="exchanges", title=f"{company_name}")
    except Exception as e:
        log.exception("stock_detail error")
        return f"<div class='card' style='color:red;'><b>Hiba:</b> {str(e)}</div>", 500
