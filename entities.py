# GUI Routes - Entities List with Filters
from flask import Blueprint, url_for, request
import logging
from datetime import datetime, timedelta
from .helpers import render_page, q_all

log = logging.getLogger("gui")
entities_bp = Blueprint("entities", __name__)

@entities_bp.route("/entities", methods=["GET"])
def entities():
    try:
        date_from = request.args.get("date_from", (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
        date_to = request.args.get("date_to", datetime.now().strftime("%Y-%m-%d"))
        entity_type = request.args.get("entity_type", "")
        
        params = [date_from, date_to]
        sql = "SELECT e.id, e.entity_type, e.entity_text, e.confidence, e.created_at, e.claim_id, c.claim, c.article_id, a.title as article_title, sp.id as company_id, sp.company_name, sp.ticker FROM entities e JOIN claims c ON c.id=e.claim_id JOIN articles a ON a.id=c.article_id LEFT JOIN stock_products sp ON sp.company_name COLLATE utf8mb4_unicode_ci LIKE CONCAT(CHAR(37), e.entity_text COLLATE utf8mb4_unicode_ci, CHAR(37)) WHERE DATE(e.created_at) BETWEEN %s AND %s"
        
        if entity_type:
            sql += " AND e.entity_type = %s"
            params.append(entity_type)
        
        sql += " ORDER BY e.id DESC LIMIT 100"
        rows = q_all(sql, params)
        
        entity_types = ["PERSON", "ORG", "GPE", "PRODUCT", "MONEY"]
        opts = "<option value=\"\">-- Összes típus --</option>"
        for et in entity_types:
            sel = "selected" if et == entity_type else ""
            opts += "<option value=\"" + et + "\" " + sel + ">" + et + "</option>"
        
        filter_html = "<div class=\"card\" style=\"margin-top:20px;\"><div class=\"k\">Szűrés</div><form method=\"get\" style=\"display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:10px;\"><div><label style=\"font-size:11px; color:var(--muted);\">Ettől:</label><input type=\"date\" name=\"date_from\" value=\"" + date_from + "\" style=\"width:100%;\"></div><div><label style=\"font-size:11px; color:var(--muted);\">Eddig:</label><input type=\"date\" name=\"date_to\" value=\"" + date_to + "\" style=\"width:100%;\"></div><div><label style=\"font-size:11px; color:var(--muted);\">Típus:</label><select name=\"entity_type\" style=\"width:100%;\">" + opts + "</select></div><div></div><button type=\"submit\" class=\"btn\" style=\"grid-column:1/5; margin-top:15px;\">Szűrés</button></form></div>"
        
        tbody = ""
        for r in rows:
            eid = str(r.get("id", ""))
            etype = r.get("entity_type", "—")
            etext = (r.get("entity_text", ""))[:50]
            conf = str(r.get("confidence", "—"))
            aid = str(r.get("article_id", ""))
            art_title = (r.get("article_title", ""))[:50]
            comp_id = r.get("company_id")
            comp_name = r.get("company_name", "")
            
            art_link = "<a class=\"btn\" href=\"" + url_for("article_one.article_one", aid=int(aid)) + "\" target=\"_blank\">Cikk</a>"
            comp_link = ""
            if etype == "ORG" and comp_id and comp_name:
                comp_link = "<a class=\"btn\" href=\"" + url_for("exchanges.stock", id=comp_id) + "\" target=\"_blank\">" + comp_name + "</a>"
            elif etype == "ORG":
                comp_link = "<span style=\"font-size:11px;color:gray;\">N/A</span>"
            
            links = art_link + " " + comp_link
            tbody += "<tr><td style=\"padding:10px;\">" + eid + "</td><td style=\"padding:10px;\"><span class=\"pill s0\">" + etype + "</span></td><td style=\"padding:10px;\">" + etext + "</td><td style=\"padding:10px;\">" + conf + "</td><td style=\"padding:10px;\">" + art_title + "</td><td style=\"padding:10px;\">" + links + "</td></tr>"
        
        table = "<div style=\"max-height:500px; overflow-y:scroll; border:1px solid rgba(255,255,255,.1); border-radius:8px;\"><table style=\"width:100%; border-collapse:collapse;\"><thead style=\"position:sticky; top:0; background:var(--bg); z-index:10;\"><tr><th style=\"padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);\">ID</th><th style=\"padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);\">Típus</th><th style=\"padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);\">Szöveg</th><th style=\"padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);\">Megbízhatóság</th><th style=\"padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);\">Cikk</th><th style=\"padding:10px; text-align:left; border-bottom:2px solid rgba(255,255,255,.2);\">Linkek</th></tr></thead><tbody>" + tbody + "</tbody></table></div>"
        
        html = "<div class=\"card\"><div class=\"k\">Entitások (" + str(len(rows)) + ")</div>" + table + "</div>" + filter_html
        return render_page(html, active="entities", title="Entities")
    except Exception as e:
        log.exception("entities error")
        return "<div class=\"card\" style=\"color:red;\">Hiba: " + str(e) + "</div>", 500
