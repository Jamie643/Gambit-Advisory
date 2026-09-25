import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests
from dateutil import parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = os.environ.get("FOOTBALL_API_KEY")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

MIN_AWAY_GOAL_AVERAGE = 0.60
MIN_DROUGHT_PERCENTAGE = 60.0
AWAY_MATCHES_TO_ANALYZE = 10

TARGET_LEAGUE_IDS = {
    39, 40, 41, 42,
    140, 141,
    135, 136,
    78, 79,
    61, 62,
    88, 89,
    94, 95,
    179, 180,
    203, 204,
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP Session
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# ---------------------------------------------------------------------------
# API-Football
# ---------------------------------------------------------------------------

def fetch_fixtures_by_date(session: requests.Session, date_str: str) -> List[Dict[str, Any]]:
    url = f"{API_FOOTBALL_BASE_URL}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"date": date_str, "status": "NS"}
    try:
        logger.info(f"Fetching fixtures for date: {date_str}")
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        errors = data.get("errors")
        if errors:
            logger.error(f"API returned errors: {errors}")
            return []
        fixtures = data.get("response", [])
        logger.info(f"Retrieved {len(fixtures)} fixtures for {date_str}.")
        return fixtures
    except requests.exceptions.Timeout:
        logger.error("Timeout while fetching fixtures from API-Football.")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error while fetching fixtures: {e}")
        return []
    except ValueError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        return []

def fetch_team_away_matches(session: requests.Session, team_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    url = f"{API_FOOTBALL_BASE_URL}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {
        "team": team_id,
        "last": limit * 2,
        "status": "FT",
        "venue": "away",
    }
    try:
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        errors = data.get("errors")
        if errors:
            logger.warning(f"API errors for team {team_id} away matches: {errors}")
            return []
        matches = data.get("response", [])
        away_matches = [
            m for m in matches
            if m.get("teams", {}).get("away", {}).get("id") == team_id
        ]
        logger.debug(f"Team {team_id}: found {len(away_matches)} away matches.")
        return away_matches[:limit]
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout fetching away matches for team {team_id}.")
        return []
    except requests.exceptions.RequestException as e:
        logger.warning(f"Request error fetching away matches for team {team_id}: {e}")
        return []
    except ValueError as e:
        logger.warning(f"JSON parse error for team {team_id}: {e}")
        return []

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def calculate_away_stats(away_matches: List[Dict[str, Any]], team_id: int) -> Optional[Dict[str, float]]:
    if not away_matches:
        return None
    total_goals = 0
    zero_goal_matches = 0
    for match in away_matches:
        goals = match.get("goals", {})
        away_goals = goals.get("away")
        if away_goals is None:
            away_goals = 0
        total_goals += away_goals
        if away_goals == 0:
            zero_goal_matches += 1
    count = len(away_matches)
    avg_goals = total_goals / count
    drought_pct = (zero_goal_matches / count) * 100
    return {"avg_goals": avg_goals, "drought_pct": drought_pct}

def is_underdog_candidate(stats: Dict[str, float]) -> bool:
    return (
        stats["avg_goals"] < MIN_AWAY_GOAL_AVERAGE
        and stats["drought_pct"] >= MIN_DROUGHT_PERCENTAGE
    )

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_alert(session, league_name, kickoff_utc, home_team, away_team, drought_pct, avg_goals) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials are not set. Cannot send alert.")
        return False
    message = (
        f"🚨 *Low-Scoring Away Underdog Alert* 🚨\n\n"
        f"🏆 *League:* {league_name}\n"
        f"⏰ *Kickoff (UTC):* {kickoff_utc}\n"
        f"⚽ *Match:* {home_team} vs {away_team}\n\n"
        f"📊 *Away Team Stats (Last {AWAY_MATCHES_TO_ANALYZE} Away Matches):*\n"
        f"   • Scoring Drought: `{drought_pct:.1f}%`\n"
        f"   • Away Goal Average: `{avg_goals:.2f}`\n\n"
        f"💡 *Betting Guidance:*\n"
        f"   `Away Team Total: Under 1.5 Goals`"
    )
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        response = session.post(TELEGRAM_API_URL, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Telegram alert sent for {home_team} vs {away_team}.")
        return True
    except requests.exceptions.Timeout:
        logger.error("Timeout while sending Telegram alert.")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Starting daily football scan for low-scoring away underdogs.")
    if not API_FOOTBALL_KEY:
        logger.critical("FOOTBALL_API_KEY environment variable is not set.")
        sys.exit(1)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.critical("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.")
        sys.exit(1)

    session = create_session()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    fixtures = fetch_fixtures_by_date(session, today_str)
    if not fixtures:
        logger.info("No fixtures found for today or API error. Exiting.")
        return

    target_fixtures = []
    for fixture in fixtures:
        league_id = fixture.get("league", {}).get("id")
        if league_id in TARGET_LEAGUE_IDS:
            target_fixtures.append(fixture)

    logger.info(f"Filtered to {len(target_fixtures)} fixtures in target leagues.")
    if not target_fixtures:
        logger.info("No fixtures in target leagues today. Exiting.")
        return

    alerts_sent = 0
    for fixture in target_fixtures:
        try:
            fixture_info = fixture.get("fixture", {})
            teams = fixture.get("teams", {})
            league = fixture.get("league", {})
            home_team = teams.get("home", {})
            away_team = teams.get("away", {})
            home_name = home_team.get("name", "Unknown Home")
            away_name = away_team.get("name", "Unknown Away")
            away_id = away_team.get("id")
            league_name = league.get("name", "Unknown League")
            kickoff_iso = fixture_info.get("date")
            if not away_id or not kickoff_iso:
                continue
            try:
                kickoff_dt = parser.isoparse(kickoff_iso)
                kickoff_utc = kickoff_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                kickoff_utc = kickoff_iso

            logger.info(f"Analyzing: {home_name} vs {away_name} ({league_name})")
            away_matches = fetch_team_away_matches(session, away_id, AWAY_MATCHES_TO_ANALYZE)
            if not away_matches:
                continue
            stats = calculate_away_stats(away_matches, away_id)
            if not stats:
                continue
            logger.info(
                f"  -> {away_name} Away Stats: Avg Goals = {stats['avg_goals']:.2f}, "
                f"Drought = {stats['drought_pct']:.1f}%"
            )
            if is_underdog_candidate(stats):
                logger.info("  -> MATCH MEETS CRITERIA! Sending alert.")
                success = send_telegram_alert(
                    session, league_name, kickoff_utc, home_name, away_name,
                    stats["drought_pct"], stats["avg_goals"],
                )
                if success:
                    alerts_sent += 1
                time.sleep(1)
        except Exception as e:
            logger.error(f"Unexpected error processing fixture: {e}", exc_info=True)
            continue

    logger.info(f"Scan complete. Alerts sent: {alerts_sent}")

if __name__ == "__main__":
    main()
