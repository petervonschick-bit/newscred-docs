# C:\data\gui\routes\health.py
from flask import Blueprint, jsonify
import logging
from .helpers import q_one

log = logging.getLogger("gui")
health_bp = Blueprint('health', __name__)

@health_bp.route("/health")
def health():
    try:
        a = q_one("SELECT COUNT(*) AS c FROM articles")
        t = q_one("SELECT COUNT(*) AS c FROM article_texts")
        return jsonify(ok=True, articles=a["c"], texts=t["c"], error=None)
    except Exception as e:
        log.exception("health error")
        return jsonify(ok=False, error=str(e)), 500
