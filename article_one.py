# GUI Routes - Article Detail Page
from flask import Blueprint, url_for
import logging
from .helpers import render_page, q_one, q_all, status_pill

log = logging.getLogger("gui")
article_one_bp = Blueprint('article_one', __name__)

@article_one_bp.route("/article/<int:aid>")
def article_one(aid: int):
    try:
        # Cikk adatok
        art = q_one("SELECT * FROM articles WHERE id=%s", (aid,))
        if not art:
            return "Cikk nem található", 404
        
        # Cikk szövege
        txt = q_one("SELECT * FROM article_texts WHERE article_id=%s", (aid,))
        
        # Claimek (állítások) + céginformáció
        claims = q_all("""
          SELECT c.id, c.claim, c.company_id, sp.company_name, sp.ticker, sp.isin
          FROM claims c
          LEFT JOIN stock_products sp ON sp.id = c.company_id
          WHERE c.article_id=%s
          ORDER BY c.created_at DESC
        """, (aid,))
        
        # Entitások
        entities = q_all("SELECT * FROM entities WHERE claim_id IN (SELECT id FROM claims WHERE article_id=%s)", (aid,))
        
        # HTML generálás
        html = f"""
        <div class='card'>
          <div class='k'>Cikk #{aid}</div>
          <div><b>Cím:</b> {art.get('title') or '(nincs cím)'}</div>
          <div><b>Link:</b> <span class='mono'>{art.get('link') or ''}</span></div>
          <div><b>Státusz:</b> {status_pill(art.get('status'))}</div>
          <div><b>Létrehozva:</b> {art.get('created_at') or ''}</div>
        </div>
        """
        
        # Eredeti szöveg
        if txt and txt.get('text'):
            html += f"""
            <div class='card'>
              <div class='k'>Eredeti szöveg</div>
              <div style='white-space:pre-wrap; font-size:12px; max-height:300px; overflow-y:auto;'>
                {txt.get('text')[:2000]}
              </div>
            </div>
            """
        
        # Angol fordítás
        if txt and txt.get('text_en'):
            html += f"""
            <div class='card'>
              <div class='k'>Angol fordítás</div>
              <div style='white-space:pre-wrap; font-size:12px; max-height:300px; overflow-y:auto;'>
                {txt.get('text_en')[:2000]}
              </div>
            </div>
            """
        
        # Claimek (állítások) + céginformáció
        if claims:
            html += """
            <div class='card'>
              <div class='k'>Kinyert állítások (Claims)</div>
            """
            for claim in claims:
                company_link = ""
                if claim.get("company_id") and claim.get("company_name"):
                    company_link = f"""
                    <div style='margin-top:6px; padding:8px; background:rgba(59,130,246,.1); border-radius:8px;'>
                      <b>📈 Cég:</b> {claim.get('company_name')} 
                      ({claim.get('ticker')} | {claim.get('isin')})
                      <a class='btn' href='{url_for('exchanges.stock_detail', product_id=claim.get("company_id"))}' style='margin-left:10px;'>Részletek →</a>
                    </div>
                    """
                
                html += f"""
                <div style='margin:10px 0; padding:10px; background:rgba(255,255,255,.05); border-left:3px solid #3b82f6; border-radius:4px;'>
                  <div>{claim.get('claim')}</div>
                  {company_link}
                </div>
                """
            html += """
            </div>
            """
        
        # Entitások
        if entities:
            html += """
            <div class='card'>
              <div class='k'>Azonosított entitások</div>
              <table style='font-size:12px;'>
                <thead>
                  <tr>
                    <th>Típus</th>
                    <th>Szöveg</th>
                    <th>Megbízhatóság</th>
                  </tr>
                </thead>
                <tbody>
            """
            for entity in entities:
                html += f"""
                <tr>
                  <td>{entity.get('entity_type')}</td>
                  <td>{entity.get('entity_text')}</td>
                  <td>{entity.get('confidence') or 'N/A'}</td>
                </tr>
                """
            html += """
                </tbody>
              </table>
            </div>
            """
        
        return render_page(html, active="articles", title=f"Cikk #{aid}")
    except Exception as e:
        log.exception("article_one error")
        return f"<div class='card' style='color:red;'><b>Hiba:</b> {str(e)}</div>", 500
