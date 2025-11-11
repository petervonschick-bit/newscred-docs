# GUI Routes - SHARED HELPERS
import json, os
import pymysql
from flask import render_template_string

# DB config path - Linux!
DBCFG_PATH = os.environ.get("DBCFG_PATH", "/opt/newscred/db.json")

# ---- DB helpers ----
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

# ---- HTML helpers ----
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
    header{ padding:22px 18px; border-bottom:1px solid rgba(255,255,255,.06); display:flex; align-items:center; justify-content:center; gap:18px; flex-wrap:wrap; }
    .brand{ display:flex; align-items:center; justify-content:center; gap:14px; text-decoration:none; color:var(--txt);}
    .brand img{ height:96px; width:auto; object-fit:contain; }
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
    <a class="brand" href="{{ url_for('dashboard.dashboard') }}">
      <img src="{{ url_for('static_file', filename='logo.webp') }}" alt="logo">
      <h1>Tutitipp Dashboard</h1>
    </a>
    <nav>
      <a href="{{ url_for('dashboard.dashboard') }}" class="{{ 'active' if active=='dashboard' else '' }}">Dashboard</a>
      <a href="{{ url_for('articles.articles') }}" class="{{ 'active' if active=='articles' else '' }}">Articles</a>
      <a href="{{ url_for('exchanges.exchanges') }}" class="{{ 'active' if active=='exchanges' else '' }}">Stock Exchanges</a>
    </nav>
  </header>
  <main>
    {{ content|safe }}
  </main>
</body>
</html>"""

def render_page(content_html, **ctx):
    return render_template_string(LAYOUT, content=content_html, **ctx)

# ---- UI helpers ----
def status_pill(n):
    n = 0 if n is None else int(n)
    label = {0:"queued/empty", 1:"ok", 2:"failed"}.get(n, str(n))
    cls = f"s{min(n,2)}"
    return f"<span class='pill {cls}'>{label}</span>"

def articles_table(rows, show_links=True):
    from flask import url_for
    out = []
    out.append("<table><thead><tr>")
    out.append("<th>ID</th><th>URL</th><th>Status</th><th>HTTP</th><th>Fetched</th><th>Updated</th><th>Ops</th>")
    out.append("</tr></thead><tbody>")
    for r in rows:
        aid = r["id"]
        url = (r.get("url") or r.get("link") or "")[:140]
        pill = status_pill(r.get("status"))
        ops = []
        ops.append(f"<a class='btn' href='{url_for('article_one.article_one', aid=aid)}'>View</a>")
        ops.append(f"<a class='btn warn' href='#' onclick=\"post('{url_for('api.api_fetch', aid=aid)}');return false;\">Fetch</a>")
        has_en = r.get("has_en", 0)
        if not has_en:
            ops.append(f"<a class='btn' href='#' onclick=\"post('{url_for('api.api_translate', aid=aid)}');return false;\">Translate</a>")
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
