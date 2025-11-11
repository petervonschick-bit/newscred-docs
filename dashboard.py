# GUI Routes - Tutitipp Dashboard
from flask import Blueprint, url_for
import logging
from .helpers import render_page, q_one

log = logging.getLogger("gui")
dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route("/")
def dashboard():
    try:
        # Statisztikák
        stats = {
            'articles': q_one("SELECT COUNT(*) AS c FROM articles")['c'],
            'translated': q_one("SELECT COUNT(*) AS c FROM article_texts WHERE text_en IS NOT NULL AND text_en<>''")['c'],
            'claims': q_one("SELECT COUNT(*) AS c FROM claims")['c'],
            'entities': q_one("SELECT COUNT(*) AS c FROM entities")['c'],
            'exchanges': q_one("SELECT COUNT(*) AS c FROM stock_exchanges")['c'],
            'stocks': q_one("SELECT COUNT(*) AS c FROM stock_products")['c'],
        }

        # Csempék - 3x2 grid
        cards = f"""
        <div class='cards' style='grid-template-columns: repeat(3, 1fr);'>
          <div class='card'>
            <div class='k'>📰 Cikkek száma</div>
            <div class='v'>{stats['articles']:,}</div>
            <div style='font-size:12px; color:var(--muted); margin:8px 0;'>összes betöltött cikk az adatbázisban</div>
            <a class='btn' href='{url_for('articles.articles')}'>Megnyitás 🔍</a>
          </div>
          
          <div class='card'>
            <div class='k'>🌐 Fordított cikkek</div>
            <div class='v'>{stats['translated']:,}</div>
            <div style='font-size:12px; color:var(--muted); margin:8px 0;'>angol fordítással rendelkező cikkek</div>
	    <a class='btn' href='/translated'>Megnyitás 🔍</a>
          </div>
          
          <div class='card'>
            <div class='k'>💬 Kinyert állítások</div>
            <div class='v'>{stats['claims']:,}</div>
            <div style='font-size:12px; color:var(--muted); margin:8px 0;'>AI által azonosított tényállítások</div>
            <a class='btn' href='/claims'>Megnyitás 🔍</a>
          </div>
          
          <div class='card'>
            <div class='k'>🧩 Entitások</div>
            <div class='v'>{stats['entities']:,}</div>
            <div style='font-size:12px; color:var(--muted); margin:8px 0;'>azonosított szervezetek és személyek</div>
	    <a class='btn' href='/entities'>Megnyitás 🔍</a>
          </div>
          
          <div class='card'>
            <div class='k'>💹 Tőzsdék</div>
            <div class='v'>{stats['exchanges']:,}</div>
            <div style='font-size:12px; color:var(--muted); margin:8px 0;'>nyilvántartott tőzsdei helyek</div>
            <a class='btn' href='{url_for('exchanges.exchanges')}'>Megnyitás 🔍</a>
          </div>
          
          <div class='card'>
            <div class='k'>📈 Részvények</div>
            <div class='v'>{stats['stocks']:,}</div>
            <div style='font-size:12px; color:var(--muted); margin:8px 0;'>BÉT-en jegyzett részvények</div>
            <a class='btn' href='#'>Megnyitás 🔍</a>
          </div>
        </div>
        """
        
        return render_page(cards, active="dashboard", title="Tutitipp Dashboard")
    except Exception as e:
        log.exception("dashboard error")
        return f"<div class='card' style='color:red;'><b>Hiba:</b> {str(e)}</div>", 500
