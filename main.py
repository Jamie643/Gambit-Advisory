import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from sportsdb import fetch_todays_fixtures, fetch_team_last_matches, filter_away_matches
from forebet_scraper import fetch_forebet_predictions, match_prediction_to_fixture

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

MIN_AWAY_GOAL_AVERAGE = 0.60
MIN_DROUGHT_PERCENTAGE = 60.0
AWAY_MATCHES_TO_ANALYZE = 10
FOREBET_SIMILARITY_THRESHOLD = 0.75

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def create_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=2,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s


def calculate_away_stats(matches: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Calculate away goal average and drought % from TheSportsDB event objects."""
    if not matches:
        return None
    total, zero = 0, 0
    valid = 0
    for m in matches:
        # TheSportsDB scores are strings like "2" or null
        away_score = m.get("intAwayScore")
        if away_score is None:
            continue
        try:
            away_goals = int(away_score)
        except (ValueError, TypeError):
            continue
        total += away_goals
        if away_goals == 0:
            zero += 1
        valid += 1
    if valid == 0:
        return None
    return {"avg_goals": total / valid, "drought_pct": (zero / valid) * 100}


def is_candidate(stats: Dict[str, float]) -> bool:
    return stats["avg_goals"] < MIN_AWAY_GOAL_AVERAGE and stats["drought_pct"] >= MIN_DROUGHT_PERCENTAGE


def send_telegram(session, league, kickoff, home, away, drought, avg) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials missing.")
        return False
    msg = (
        f"🚨 *Low-Scoring Away Underdog* 🚨\n\n"
        f"🏆 *League:* {league}\n"
        f"⏰ *Kickoff (UTC):* {kickoff}\n"
        f"⚽ *Match:* {home} vs {away}\n\n"
        f"📊 *Away Stats (last {AWAY_MATCHES_TO_ANALYZE} away):*\n"
        f"   • Scoring Drought: `{drought:.1f}%`\n"
        f"   • Away Goal Average: `{avg:.2f}`\n\n"
        f"💡 *Guidance:* `Away Team Total: Under 1.5 Goals`"
    )
    try:
        r = session.post(TELEGRAM_API_URL, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": msg,
            "parse_mode": "Markdown", "disable_web_page_preview": True,
        }, timeout=10)
        r.raise_for_status()
        logger.info(f"Telegram sent: {home} vs {away}")
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def main() -> None:
    logger.info("=== Daily scan starting ===")
    session = create_session()

    # 1. Fixtures from TheSportsDB
    fixtures = fetch_todays_fixtures(session)
    if not fixtures:
        logger.info("No fixtures today. Exiting.")
        return

    # 2. Forebet predictions (one scrape, reused)
    predictions = fetch_forebet_predictions(session, max_pages=5)

    alerts = 0
    for fx in fixtures:
        try:
            league_name = fx.get("strLeague", "Unknown")
            home_name = fx.get("strHomeTeam", "?")
            away_name = fx.get("strAwayTeam", "?")
            away_id = fx.get("idAwayTeam")
            kickoff_iso = fx.get("strTimestamp") or fx.get("dateEvent")

            if not away_id or not kickoff_iso:
                continue

            # Format kickoff
            kickoff = kickoff_iso.replace("T", " ").replace("Z", "")[:16]

            # 3. Forebet consensus gate
            fb = match_prediction_to_fixture(home_name, away_name, predictions, FOREBET_SIMILARITY_THRESHOLD)
            if not fb:
                logger.info(f"  -> {home_name} vs {away_name}: no Forebet match, skip.")
                continue
            logger.info(f"Analyzing: {home_name} vs {away_name} ({league_name})")

            # 4. Away form from TheSportsDB
            events = fetch_team_last_matches(session, away_id, limit=15)
            away_matches = filter_away_matches(events, away_id)[:AWAY_MATCHES_TO_ANALYZE]
            stats = calculate_away_stats(away_matches)
            if not stats:
                logger.info(f"  -> no away match history, skip.")
                continue
            logger.info(f"  -> Avg={stats['avg_goals']:.2f} Drought={stats['drought_pct']:.1f}%")

            # 5. Decision
            if is_candidate(stats):
                logger.info("  -> ✅ MEETS CRITERIA")
                if send_telegram(session, league_name, kickoff, home_name, away_name,
                                 stats["drought_pct"], stats["avg_goals"]):
                    alerts += 1
                time.sleep(1)
        except Exception as e:
            logger.error(f"Fixture error: {e}", exc_info=True)

    logger.info(f"=== Scan complete. Alerts: {alerts} ===")


if __name__ == "__main__":
    main()
