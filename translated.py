# GUI Routes - Translated Articles List with Filters
from flask import Blueprint, url_for, request
import logging
from datetime import datetime, timedelta
from .helpers import render_page, q_all

log = logging.getLogger("gui")
translated_bp = Blueprint('translated', __name__)

@translated_bp.route("/translated", methods=['GET'])
def translated():
    try:
        # Paraméterek lekérése
        date_from = request.args.get('date_from', (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))
        date_to = request.args.get('date_to', datetime.now().strftime('%Y-%m-%d'))
        company_id = request.args.get('company_id', '')
        source_id = request.args.get('source_id', '')
        
        # Fordított cikkek - ahol van english fordítás
        params = [date_from, date_to]
        
        sql = """
          SELECT DISTINCT a.id, a.title, a.link, a.status, a.created_at,
                 t.text_en, t.en_provider, t.en_updated_at,
                 s.name as source_name, sp.company_name, sp.ticker
          FROM articles a
          INNER JOIN article_texts t ON t.article_id=a.id
          LEFT JOIN sources s ON s.id=a.source_id
          LEFT JOIN claims c ON c.article_id=a.id
          LEFT JOIN stock_products sp ON sp.id=c.company_id
          WHERE a.status!=2
            AND t.text_en IS NOT NULL 
            AND t.text_en != ''
            AND DATE(a.created_at) BETWEEN %s AND %s
        """
        
        if company_id:
            sql += " AND c.company_id = %s "
            params.append(int(company_id))
        
        if source_id:
            sql += " AND a.source_id = %s "
            params.append(int(source_id))
        
        sql += """
          ORDER BY a.id DESC
          LIMIT 100
        """
        
        rows = q_all(sql, params)
        
        # Összes cég (stock_products) - előre azok, amikhez van claims
        companies = q_all("""
          SELECT DISTINCT sp.id, sp.company_name, sp.ticker,
                 CASE WHEN EXISTS (
                   SELECT 1 FROM claims WHERE company_id=sp.id
                 ) THEN 0 ELSE 1 END AS no_claims
          FROM stock_products sp
          ORDER BY no_claims, sp.company_name
        """)
        
        # Összes forrás
        sources = q_all("""
          SELECT DISTINCT s.id, s.name
          FROM sources s
          JOIN articles a ON a.source_id=s.id
          WHERE a.status!=2
          ORDER BY s.name
        """)
        
        # Szűrő panel HTML
        company_options = '<option value="">-- Összes cég --</option>'
        for c in companies:
            selected = 'selected' if str(c['id']) == company_id else ''
            company_options += f'<option value="{c["id"]}" {selected}>{c["company_name"]} ({c["ticker"]})</option>'
        
        source_options = '<option value="">-- Összes forrás --</option>'
        for s in sources:
            selected = 'selected' if str(s['id']) == source_id else ''
            source_options += f'<option value="{s["id"]}" {selected}>{s["name"]}</option>'
        
        filter_panel = f"""
        <div class='card' style='margin-top:20px;'>
          <div class='k'>🔍 Szűrés</div>
          <form method='get' style='display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:10px;'>
            <div>
              <label style='font-size:11px; color:var(--muted);'>Ettől:</label>
              <input type='date' name='date_from' value='{date_from}' style='width:100%;'>
            </div>
            <div>
              <label style='font-size:11px; color:var(--muted);'>Eddig:</label>
              <input type='date' name='date_to' value='{date_to}' style='width:100%;'>
            </div>
            <div>
              <label style='font-size:11px; color:var(--muted);'>Cég:</label>
              <select name='company_id' style='width:100%;'>
                {company_options}
              </select>
            </div>
            <div>
              <label style='font-size:11px; color:var(--muted);'>Forrás:</label>
              <select name='source_id' style='width:100%;'>
                {source_options}
              </select>
            </div>
            <button type='submit' class='btn' style='grid-column:1/5; margin-top:15px;'>🔍 Szűrés</button>
          </form>
        </div>
        """
        
        # Táblázat HTML - görgetős
        table_html = """
        <div style='max-height:500px; overflow-y:scroll; border:1px solid rgba(255,255,255,.1); border-radius:8px;'>
        <table style='width:100%; border-collapse:collapse;'>
          <thead style='position:sticky; top:0; background:var(--bg); z-index:10;'>
            <tr>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>ID</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Cím</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Link</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Forrás</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Cég</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Fordító</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Fordítva</th>
              <th style='padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);'>Művelet</th>
            </tr>
          </thead>
          <tbody>
        """
        
        for r in rows:
            aid = r["id"]
            title = (r.get("title") or "")[:80]
            link = (r.get("link") or "")[:50]
            source_name = (r.get("source_name") or "N/A")[:30]
            company_name = (r.get("company_name") or "—")
            ticker = (r.get("ticker") or "")
            company_display = f"{company_name} ({ticker})" if ticker else company_name
            en_provider = (r.get("en_provider") or "N/A")[:20]
            en_updated = r.get("en_updated_at") or ""
            
            table_html += f"""
            <tr>
              <td style='padding:10px;'>{aid}</td>
              <td style='padding:10px;'>{title}</td>
              <td class='mono' style='font-size:11px; padding:10px;'><a href='{link}' target='_blank'>🔗</a></td>
              <td style='font-size:12px; padding:10px;'>{source_name}</td>
              <td style='font-size:12px; padding:10px;'>{company_display}</td>
              <td style='font-size:11px; padding:10px;'>{en_provider}</td>
              <td style='font-size:11px; padding:10px;'>{en_updated}</td>
              <td style='padding:10px;'><a class='btn' href='{url_for('article_one.article_one', aid=aid)}'>Megtekintés →</a></td>
            </tr>
            """
        
        table_html += """
          </tbody>
        </table>
        </div>
        """
        
        html = f"""
        <div class='card'>
          <div class='k'>🌐 Fordított cikkek ({len(rows)} találat)</div>
          {table_html}
        </div>
        {filter_panel}
        """
        
        return render_page(html, active="translated", title="Translated Articles")
    except Exception as e:
        log.exception("translated error")
        return f"<div class='card' style='color:red;'><b>Hiba a fordított cikkek oldalon:</b> {str(e)}</div>", 500
