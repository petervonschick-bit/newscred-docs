# C:\data\gui\routes\api.py
from flask import Blueprint, jsonify
import logging
from .helpers import q_exec

log = logging.getLogger("gui")
api_bp = Blueprint('api', __name__)

@api_bp.route("/api/fetch/<int:aid>", methods=["POST"])
def api_fetch(aid: int):
    try:
        q_exec("""UPDATE articles
                  SET status=0, http_status=NULL, fetched_at=NULL
                  WHERE id=%s""", (aid,))
        return jsonify(ok=True, error=None)
    except Exception as e:
        log.exception("api_fetch error")
        return jsonify(ok=False, error=str(e)), 500

@api_bp.route("/api/translate/<int:aid>", methods=["POST"])
def api_translate(aid: int):
    try:
        q_exec("""UPDATE article_texts
                  SET text_en=NULL, text_en_md5=NULL, en_provider=NULL, en_updated_at=NULL
                  WHERE article_id=%s""", (aid,))
        return jsonify(ok=True, error=None)
    except Exception as e:
        log.exception("api_translate error")
        return jsonify(ok=False, error=str(e)), 500
