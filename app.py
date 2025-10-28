# C:\data\gui\app.py
import os, json, logging
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template_string, url_for, redirect
import pymysql

APP_PORT = int(os.environ.get("GUI_PORT", "5080"))
DBCFG_PATH = os.environ.get("DBCFG_PATH", r"C:\data\config\db.json")
STATIC_DIR = os.environ.get("GUI_STATIC", r"C:\data\gui\static")
LOG_PATH = os.environ.get("GUI_LOG", r"C:\data\logs\gui.log")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()]
)
log = logging.getLogger("gui")

# ---- DB helpers -------------------------------------------------------------
def dbcfg():
    with open(DBCFG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def db():
    cfg = dbcfg()
    return pymysql.connect(
        host=cfg.get("host","127.0.0.1"),
        port=int(cfg.get("port",3306)),
        user=cfg.get("user","root"),
        password=cfg.get("password"),
        database=cfg.get("database","newscred"),
        charset=cfg.get("charset","utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def q_all(sql, params=None):
    with db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()

def q_one(sql, params=None):
    with db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()

def q_exec(sql, params=None):
    with db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.rowcount

# ---- HTML layout (base)  ----------------------------------------------------
LAYOUT = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title or "News Dashboard" }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root{ --bg:#0b1220; --card:#121a2b; --muted:#8da2c0; --txt:#e6eefc; --accent:#3b82f6; }
    *{ box-sizing:border-box; font-family: system-ui, -apple-system, Segoe UI, Roboto, Inter, Arial; }
    body{ margin:0; background:linear-gradient(180deg,#0b1220,#0e1730); color:var(--txt); }
    header{ padding:22px 18px; border-bottom:1px solid rgba(255,255,255,.06); display:flex; align-items:center; gap:18px; flex-wrap:wrap; }
    .brand{ display:flex; align-items:center; gap:14px; text-decoration:none; color:var(--txt);}
    .brand img{ height:32px; width:auto; object-fit:contain; } /* fele akkora */
    .brand h1{ font-size:20px; margin:0; font-weight:600; letter-spacing:.3px; }
    nav a{ margin-right:10px; text-decoration:none; color:var(--muted); padding:8px 12px; border-radius:8px; }
    nav a.active, nav a:hover{ color:var(--txt); background:rgba(255,255,255,.06); }
    main{ padding:18px; max-width:1200px; margin:0 auto; }
    .cards{ display:grid; grid-template-columns: repeat(auto-fill,minmax(220px,1fr)); gap:14px; }
    .card{ background:var(--card); border:1px solid rgba(255,255,255,.08); border-radius:14px; padding:14px; }
    .k{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.6px; }
    .v{ font-size:22px; font-weight:700; }
    table{ width:100%; border-collapse:collapse; font-size:14px; }
    th, td{ padding:10px 8px; text-align:left; border-bottom:1px solid rgba(255,255,255,.06); vertical-align:top; }
    th{ color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.5px;}
    .btn{ display:inline-block; padding:6px 10px; border-radius:8px; background:rgba(59,130,246,.12); color:#dbe7ff; text-decoration:none; font-weight:600; }
    .btn:hover{ background:rgba(59,130,246,.22); }
    .btn.warn{ background:rgba(245,158,11,.14); } .btn.warn:hover{ background:rgba(245,158,11,.24); }
    .pill{ padding:2px 8px; border-radius:999px; font-size:12px; }
    .pill.s0{ background:#334155; color:#e2e8f0; }
    .pill.s1{ background:#064e3b; color:#d1fae5; }
    .pill.s2{ background:#7f1d1d; color:#fee2e2; }
    .muted{ color:var(--muted); }
    .mono{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .toolbar{ margin:12px 0; display:flex; gap:10px; align-items:center; flex-wrap:wrap;}
    select, input[type=text]{ background:#0f172a; color:#e6eefc; border:1px solid #233; padding:6px 8px; border-radius:8px; }
  </style>
  <script>
    async function post(url){
      try{
        const res = await fetch(url,{method:'POST'});
        if(!res.ok){ const t = await res.text(); alert('Request failed: '+res.status+'\\n'+t); return; }
        location.reload();
      }catch(e){ alert('Network error'); }
    }
  </script>
</head>
<body>
  <header>
    <a class="brand" href="{{ url_for('dashboard') }}">
      <img src="{{ url_for('static_file', filename='logo.webp') }}" alt="logo">
      <h1>News Dashboard</h1>
    </a>
    <nav>
      <a href="{{ url_for('dashboard') }}" class="{{ 'active' if active=='dashboard' else '' }}">Dashboard</a>
      <a href="{{ url_for('articles') }}" class="{{ 'active' if active=='articles' else '' }}">Articles</a>
      <a href="{{ url_for('exchanges') }}" class="{{ 'active' if active=='exchanges' else '' }}">Stock Exchanges</a>
      <a href="{{ url_for('health') }}" class="{{ 'active' if active=='health' else '' }}">Health</a>
    </nav>
  </header>
  <main>
    {{ content|safe }}
  </main>
</body>
</html>"""

def render_page(content_html, **ctx):
    return render_template_string(LAYOUT, content=content_html, **ctx)

# ---- Pages (content) --------------------------------------------------------
def status_pill(n):
    n = 0 if n is None else int(n)
    label = {0:"queued/empty", 1:"ok", 2:"failed"}.get(n, str(n))
    cls = f"s{min(n,2)}"
    return f"<span class='pill {cls}'>{label}</span>"

def articles_table(rows, show_links=True):
    out = []
    out.append("<table><thead><tr>")
    out.append("<th>ID</th><th>URL</th><th>Status</th><th>HTTP</th><th>Fetched</th><th>Updated</th><th>Ops</th>")
    out.append("</tr></thead><tbody>")
    for r in rows:
        aid = r["id"]
        url = (r.get("url") or r.get("link") or "")[:140]
        pill = status_pill(r.get("status"))
        ops = []
        ops.append(f"<a class='btn' href='{url_for('article_one', aid=aid)}'>View</a>")
        ops.append(f"<a class='btn warn' href='#' onclick=\"post('{url_for('api_fetch', aid=aid)}');return false;\">Fetch</a>")
        # Has English?
        has_en = r.get("has_en", 0)
        if not has_en:
            ops.append(f"<a class='btn' href='#' onclick=\"post('{url_for('api_translate', aid=aid)}');return false;\">Translate</a>")
        out.append(
            "<tr>"
            f"<td>{aid}</td>"
            f"<td class='mono'>{url}</td>"
            f"<td>{pill}</td>"
            f"<td>{r.get('http_status') or ''}</td>"
            f"<td>{r.get('fetched_at') or ''}</td>"
            f"<td>{r.get('updated_at') or ''}</td>"
            f"<td style='white-space:nowrap; display:flex; gap:6px;'>{' '.join(ops)}</td>"
            "</tr>"
        )
    out.append("</tbody></table>")
    return "\n".join(out)

# ---- Flask app --------------------------------------------------------------
app = Flask(__name__)

@app.route("/static/<path:filename>")
def static_file(filename):
    return send_from_directory(STATIC_DIR, filename)

@app.route("/health")
def health():
    try:
        a = q_one("SELECT COUNT(*) AS c FROM articles")
        t = q_one("SELECT COUNT(*) AS c FROM article_texts")
        return jsonify(ok=True, articles=a["c"], texts=t["c"], error=None)
    except Exception as e:
        log.exception("health error")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/")
def dashboard():
    try:
        stats = {}
        rows = q_all("""
            SELECT status, COUNT(*) AS cnt
            FROM articles
            GROUP BY status
            ORDER BY status
        """)
        for r in rows:
            stats[int(r["status"])] = r["cnt"]
        texts = q_one("SELECT COUNT(*) AS c FROM article_texts")
        stats_texts = texts["c"]

        recent = q_all("""
          SELECT a.id, a.url, a.status, a.http_status, a.fetched_at, a.updated_at,
                 CASE WHEN t.text_en IS NOT NULL AND t.text_en<>'' THEN 1 ELSE 0 END AS has_en
          FROM articles a
          LEFT JOIN article_texts t ON t.article_id=a.id
          ORDER BY GREATEST(COALESCE(a.fetched_at,'1970-01-01'), a.updated_at) DESC
          LIMIT 20
        """)

        cards = f"""
        <div class='cards'>
          <div class='card'><div class='k'>Status 0 (queued/empty)</div><div class='v'>{stats.get(0,0)}</div></div>
          <div class='card'><div class='k'>Status 1 (ok)</div><div class='v'>{stats.get(1,0)}</div></div>
          <div class='card'><div class='k'>Status 2 (failed)</div><div class='v'>{stats.get(2,0)}</div></div>
          <div class='card'><div class='k'>Article texts</div><div class='v'>{stats_texts}</div></div>
        </div>
        <div class='card' style='margin-top:14px'>
          <div class='k'>Recent Articles</div>
          {articles_table(recent)}
        </div>
        """
        return render_page(cards, active="dashboard", title="Dashboard")
    except Exception as e:
        log.exception("dashboard error")
        return "Internal Server Error", 500

@app.route("/articles")
def articles():
    try:
        lang = request.args.get("lang", "all")
        langs = q_all("SELECT DISTINCT lang FROM article_texts ORDER BY lang IS NULL, lang")
        lang_opts = ["<option value='all' {sel}>All</option>".format(sel="selected" if lang=="all" else "")]
        for row in langs:
            l = row["lang"] or "(unknown)"
            sel = "selected" if lang==l else ""
            lang_opts.append(f"<option value='{l}' {sel}>{l}</option>")

        # base query
        sql = """
          SELECT a.id, a.url, a.status, a.http_status, a.fetched_at, a.updated_at,
                 CASE WHEN t.text_en IS NOT NULL AND t.text_en<>'' THEN 1 ELSE 0 END AS has_en
          FROM articles a
          LEFT JOIN article_texts t ON t.article_id=a.id
        """
        params = []
        if lang != "all":
            if lang == "(unknown)":
                sql += " WHERE t.lang IS NULL "
            else:
                sql += " WHERE t.lang=%s "
                params.append(lang)
        sql += " ORDER BY a.id DESC LIMIT 100"
        rows = q_all(sql, params)

        toolbar = f"""
        <div class='toolbar'>
          <form method='get' action='{url_for('articles')}'>
            <label>Language:
              <select name='lang' onchange='this.form.submit()'>
                {''.join(lang_opts)}
              </select>
            </label>
          </form>
        </div>
        """
        html = toolbar + "<div class='card'>" + articles_table(rows) + "</div>"
        return render_page(html, active="articles", title="Articles")
    except Exception as e:
        log.exception("articles error")
        return "Internal Server Error", 500

@app.route("/article/<int:aid>")
def article_one(aid:int):
    try:
        art = q_one("SELECT * FROM articles WHERE id=%s", (aid,))
        if not art:
            return "Not found", 404
        txt = q_one("SELECT * FROM article_texts WHERE article_id=%s", (aid,))
        def box(title, body):
            return f"<div class='card'><div class='k'>{title}</div><div style='white-space:pre-wrap'>{body}</div></div>"
        ops = (
            f"<a class='btn warn' href='#' onclick=\"post('{url_for('api_fetch', aid=aid)}');return false;\">Fetch</a> "
            f"<a class='btn' href='#' onclick=\"post('{url_for('api_translate', aid=aid)}');return false;\">Translate</a>"
        )
        meta = f"""
        <div class='card'>
          <div class='k'>Article #{aid}</div>
          <div><b>URL:</b> <span class='mono'>{art.get('url') or art.get('link') or ''}</span></div>
          <div><b>Status:</b> {status_pill(art.get('status'))}</div>
          <div><b>HTTP:</b> {art.get('http_status') or ''}</div>
          <div><b>Updated:</b> {art.get('updated_at') or ''}</div>
          <div style='margin-top:10px'>{ops}</div>
        </div>
        """
        body = meta
        if txt:
            body += box("Original (lang="+str(txt.get("lang") or "(unknown)")+ ")", (txt.get("text") or "")[:20000])
            body += box("English", (txt.get("text_en") or "")[:20000])
        else:
            body += "<div class='card'>No text yet.</div>"
        return render_page(body, active="articles", title=f"Article #{aid}")
    except Exception as e:
        log.exception("article_one error")
        return "Internal Server Error", 500

@app.route("/exchanges")
def exchanges():
    try:
        rows = q_all("SELECT id,country_name,exchange_name,url FROM stock_exchanges ORDER BY country_name,exchange_name")
        tbl = ["<table><thead><tr><th>ID</th><th>Country</th><th>Exchange</th><th>URL</th></tr></thead><tbody>"]
        for r in rows:
            tbl.append(
                "<tr>"
                f"<td>{r['id']}</td>"
                f"<td>{r['country_name']}</td>"
                f"<td>{r['exchange_name']}</td>"
                f"<td><a class='btn' href='{r['url']}' target='_blank'>Open</a></td>"
                "</tr>"
            )
        tbl.append("</tbody></table>")
        html = "<div class='card'>" + "\n".join(tbl) + "</div>"
        return render_page(html, active="exchanges", title="Stock Exchanges")
    except Exception:
        log.exception("exchanges error")
        return "Internal Server Error", 500

# ---- APIs: fetch / translate ------------------------------------------------
@app.route("/api/fetch/<int:aid>", methods=["POST"])
def api_fetch(aid:int):
    try:
        # reset to be (re)fetched by fetcher
        q_exec("""UPDATE articles
                  SET status=0, http_status=NULL, fetched_at=NULL
                  WHERE id=%s""", (aid,))
        return jsonify(ok=True, error=None)
    except Exception as e:
        log.exception("api_fetch error")
        return jsonify(ok=False, error=str(e)), 500

@app.route("/api/translate/<int:aid>", methods=["POST"])
def api_translate(aid:int):
    try:
        # clear English to force translation
        q_exec("""UPDATE article_texts
                  SET text_en=NULL, text_en_md5=NULL, en_provider=NULL, en_updated_at=NULL
                  WHERE article_id=%s""", (aid,))
        return jsonify(ok=True, error=None)
    except Exception as e:
        log.exception("api_translate error")
        return jsonify(ok=False, error=str(e)), 500

# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # tegyük ki a logót, ha még nincs
    logo_src = os.path.join(os.path.dirname(__file__), "logo.webp")
    logo_dst = os.path.join(STATIC_DIR, "logo.webp")
    try:
        if os.path.exists(logo_src) and not os.path.exists(logo_dst):
            with open(logo_src, "rb") as s, open(logo_dst, "wb") as d:
                d.write(s.read())
    except Exception as e:
        log.warning("logo copy failed: %s", e)

    app.run(host="127.0.0.1", port=5080)
