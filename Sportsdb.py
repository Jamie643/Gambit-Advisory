"""
TheSportsDB API client.
Free tier uses key '3'. No registration required for testing.
Docs: https://www.thesportsdb.com/free_sports_api
"""
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

BASE_URL = "https://www.thesportsdb.com/api/v1/json/3"

# TheSportsDB league IDs for target competitions
# Find IDs: https://www.thesportsdb.com/api/v1/json/3/all_leagues.php
TARGET_LEAGUE_IDS = {
    "4328",  # English Premier League
    "4329",  # English League Championship
    "4335",  # Spanish La Liga
    "4332",  # Italian Serie A
    "4331",  # German Bundesliga
    "4334",  # French Ligue 1
    "4337",  # Dutch Eredivisie
    "4344",  # Portuguese Primeira Liga
    "4340",  # Scottish Premiership
}


def fetch_team_last_matches(
    session: requests.Session, team_id: str, limit: int = 15
) -> List[Dict[str, Any]]:
    """
    Fetch a team's last N events (all venues).
    We'll filter to away matches client-side.
    Endpoint: /eventslast.php?id={team_id}
    """
    url = f"{BASE_URL}/eventslast.php"
    params = {"id": team_id}
    try:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        events = data.get("results") or data.get("events") or []
        logger.debug(f"TheSportsDB team {team_id}: {len(events)} events.")
        return events[:limit]
    except Exception as e:
        logger.warning(f"TheSportsDB team {team_id} error: {e}")
        return []


def filter_away_matches(events: List[Dict[str, Any]], team_id: str) -> List[Dict[str, Any]]:
    """
    Filter events to only those where the given team was the away side.
    TheSportsDB event fields: idHomeTeam, idAwayTeam, intHomeScore, intAwayScore
    """
    away = []
    for e in events:
        home_id = str(e.get("idHomeTeam") or "")
        away_id = str(e.get("idAwayTeam") or "")
        if away_id == str(team_id):
            away.append(e)
    return away


def fetch_todays_fixtures(session: requests.Session) -> List[Dict[str, Any]]:
    """
    Fetch all of today's fixtures across target leagues.
    Endpoint: /eventsday.php?d={YYYY-MM-DD}&s=Soccer
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{BASE_URL}/eventsday.php"
    params = {"d": today, "s": "Soccer"}
    try:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        events = data.get("events") or []
        # Filter to target leagues
        filtered = [e for e in events if str(e.get("idLeague") or "") in TARGET_LEAGUE_IDS]
        logger.info(f"TheSportsDB: {len(filtered)} target fixtures today (out of {len(events)}).")
        return filtered
    except Exception as e:
        logger.error(f"TheSportsDB fixtures error: {e}")
        return []


def search_team(session: requests.Session, name: str) -> Optional[str]:
    """Lookup a team ID by name. Used as fallback if fixture lacks team ID."""
    url = f"{BASE_URL}/searchteams.php"
    params = {"t": name}
    try:
        r = session.get(url, params=params, timeout=15)
        r.raise_for_status()
        teams = r.json().get("teams") or []
        if teams:
            return str(teams[0].get("idTeam"))
    except Exception as e:
        logger.warning(f"TheSportsDB search '{name}' error: {e}")
    return None
