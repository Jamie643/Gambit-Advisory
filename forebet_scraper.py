"""
Forebet prediction scraper.
Forebet's mobile endpoint returns JSON directly.
"""
import logging
import requests
from typing import Dict, List, Any
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

FOREBET_AJAX = "https://m.forebet.com/scripts/getrs.php"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def fetch_forebet_predictions(session: requests.Session, max_pages: int = 5) -> List[Dict[str, Any]]:
    predictions = []
    try:
        for i in range(max_pages):
            params = {"ln": "en", "tp": "1x2", "in": str(i + 11), "ord": "0"}
            r = session.get(FOREBET_AJAX, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or not data:
                break
            rows = data[0]
            if not isinstance(rows, list):
                break
            for row in rows:
                home = row.get("home") or row.get("homeTeam") or ""
                away = row.get("away") or row.get("awayTeam") or ""
                if not home or not away:
                    continue
                predictions.append({
                    "home": home.strip(),
                    "away": away.strip(),
                    "prob_home": row.get("pr_home") or row.get("homeProb"),
                    "prob_draw": row.get("pr_draw") or row.get("drawProb"),
                    "prob_away": row.get("pr_away") or row.get("awayProb"),
                })
            logger.info(f"Forebet page {i+1}: {len(rows)} raw rows.")
    except Exception as e:
        logger.warning(f"Forebet scrape failed: {e}")
    logger.info(f"Forebet: {len(predictions)} predictions collected.")
    return predictions


def match_prediction_to_fixture(
    home_name: str, away_name: str, predictions: List[Dict[str, Any]], threshold: float = 0.75
) -> Dict[str, Any] | None:
    best, best_score = None, 0.0
    for p in predictions:
        combined = (_similarity(home_name, p["home"]) + _similarity(away_name, p["away"])) / 2
        if combined > best_score:
            best_score, best = combined, p
    if best and best_score >= threshold:
        return best
    return None
