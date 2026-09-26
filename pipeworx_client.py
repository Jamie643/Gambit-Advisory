"""
Hybrid data client for the football underdog scanner.

Two jobs:
  1. Today's fixtures     -> TheSportsDB (free, no key beyond public '3')
  2. Away form + team IDs -> Pipeworx -> API-Football (season 2024, free tier OK)

TheSportsDB team IDs are NOT the same as API-Football team IDs, so we resolve
by name via Pipeworx's team_search tool before pulling away form.
"""
import os
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TheSportsDB (free fixtures)
# ---------------------------------------------------------------------------
SDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"
SDB_LEAGUE_IDS = {
    "4328": "English Premier League",
    "4329": "English Championship",
    "4335": "Spanish La Liga",
    "4332": "Italian Serie A",
    "4331": "German Bundesliga",
    "4334": "French Ligue 1",
    "4337": "Dutch Eredivisie",
    "4344": "Portuguese Primeira Liga",
    "4340": "Scottish Premiership",
}

# ---------------------------------------------------------------------------
# Pipeworx (away form + team id resolution)
# ---------------------------------------------------------------------------
PIPEWORX_MCP_URL = "https://gateway.pipeworx.io/api-football/mcp"
PIPEWORX_KEY = os.environ.get("PIPEWORX_API_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

# API-Football free tier: only seasons 2022-2024 are queryable.
HISTORICAL_SEASON = 2024

# Cache team_id lookups within a run so repeated names don't burn calls.
_TEAM_ID_CACHE: Dict[str, Optional[int]] = {}


# ---------------------------------------------------------------------------
# Pipeworx MCP helpers
# ---------------------------------------------------------------------------

def _pw_headers() -> Dict[str, str]:
    if not PIPEWORX_KEY:
        raise RuntimeError("PIPEWORX_API_KEY is not set.")
    return {
        "Content-Type": "application/json",
        "x-api-key": PIPEWORX_KEY,
    }


def _pw_call(session: requests.Session, tool_name: str, arguments: Dict[str, Any]) -> Optional[Any]:
    """
    Call a Pipeworx MCP tool. Handles both the standard MCP content envelope
    and the structuredContent field. Returns None on error, unknown tool,
    or a found:false payload.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        r = session.post(PIPEWORX_MCP_URL, headers=_pw_headers(), json=payload, timeout=25)
        if r.status_code != 200:
            logger.error(f"[Pipeworx] {tool_name} HTTP {r.status_code}: {r.text[:300]}")
            return None

        data = r.json()
        if "error" in data:
            logger.error(f"[Pipeworx] {tool_name} JSON-RPC error: {data['error']}")
            return None

        result = data.get("result", {})
        if not isinstance(result, dict):
            return result

        # Preferred path: structuredContent
        sc = result.get("structuredContent")
        if sc:
            if isinstance(sc, dict) and sc.get("found") is False:
                logger.warning(
                    f"[Pipeworx] {tool_name} found:false — {sc.get('reason')}: {sc.get('hint', '')}"
                )
                return None
            return sc

        # Fallback: content[0].text is a JSON string
        content = result.get("content", [])
        if content and isinstance(content, list):
            first = content[0]
            text = first.get("text") if isinstance(first, dict) else None
            if text:
                try:
                    parsed = json.loads(text)
                except (ValueError, TypeError):
                    return {"raw": text}
                if isinstance(parsed, dict) and parsed.get("found") is False:
                    logger.warning(
                        f"[Pipeworx] {tool_name} found:false — {parsed.get('reason')}"
                    )
                    return None
                return parsed

        return None

    except requests.exceptions.Timeout:
        logger.error(f"[Pipeworx] {tool_name} timeout")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[Pipeworx] {tool_name} request error: {e}")
        return None
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"[Pipeworx] {tool_name} JSON parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# Fixtures: TheSportsDB
# ---------------------------------------------------------------------------

def fetch_todays_fixtures(session: requests.Session) -> List[Dict[str, Any]]:
    """
    Fetch each target league's upcoming fixtures from TheSportsDB and keep
    only those matching today's UTC date. Returns raw TheSportsDB event dicts.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fixtures: List[Dict[str, Any]] = []

    for league_id, league_name in SDB_LEAGUE_IDS.items():
        try:
            r = session.get(
                f"{SDB_BASE}/eventsnextleague.php",
                params={"id": league_id},
                timeout=15,
            )
            r.raise_for_status()
            events = (r.json() or {}).get("events") or []
            today_count = 0
            for e in events:
                if e.get("dateEvent") == today:
                    fixtures.append(e)
                    today_count += 1
            logger.info(f"TheSportsDB {league_name}: {len(events)} upcoming, {today_count} today.")
        except Exception as e:
            logger.warning(f"TheSportsDB {league_name} error: {e}")

    logger.info(f"Fixtures collected: {len(fixtures)} today across target leagues.")
    return fixtures


# ---------------------------------------------------------------------------
# Team ID resolution + away form: Pipeworx -> API-Football
# ---------------------------------------------------------------------------

def resolve_api_football_team_id(
    session: requests.Session, team_name: str
) -> Optional[int]:
    """
    Map a team name (from TheSportsDB) to an API-Football team id.
    Cached per-process to avoid duplicate calls.
    """
    key = team_name.strip().lower()
    if key in _TEAM_ID_CACHE:
        return _TEAM_ID_CACHE[key]

    result = _pw_call(
        session,
        "team_search",
        {"name": team_name, "_apiKey": API_FOOTBALL_KEY},
    )

    team_id: Optional[int] = None
    if isinstance(result, dict):
        teams = result.get("teams") or []
        if teams:
            team_id = teams[0].get("id")
            logger.info(f"Resolved '{team_name}' -> API-Football team id {team_id}")
        else:
            logger.warning(f"Could not resolve team id for '{team_name}'")
    _TEAM_ID_CACHE[key] = team_id
    return team_id


def fetch_team_away_matches(
    session: requests.Session, team_id: int, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch a team's last N finished away matches in HISTORICAL_SEASON.
    Response shape (flat, from Pipeworx fixtures tool):
        {count, fixtures: [{fixture_id, date, status, home, away,
                            home_team_id, away_team_id, score_home, score_away}]}
    """
    args = {
        "team": team_id,
        "season": HISTORICAL_SEASON,
        "last": 50,  # fetch plenty, filter to away + played
        "_apiKey": API_FOOTBALL_KEY,
    }
    result = _pw_call(session, "fixtures", args)
    if not isinstance(result, dict):
        return []

    fixtures = result.get("fixtures") or []

    away: List[Dict[str, Any]] = []
    for f in fixtures:
        if f.get("away_team_id") == team_id and f.get("score_away") is not None:
            away.append(f)

    away.sort(key=lambda x: x.get("date") or "", reverse=True)
    logger.debug(
        f"Team {team_id}: {len(away)} finished away matches in season {HISTORICAL_SEASON}."
    )
    return away[:limit]


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def calculate_away_stats(
    matches: List[Dict[str, Any]],
) -> Optional[Dict[str, float]]:
    """
    Compute away goal average and scoring-drought percentage.
    Only counts matches with a valid score_away.
    """
    if not matches:
        return None

    total, zero, valid = 0, 0, 0
    for m in matches:
        s = m.get("score_away")
        if s is None:
            continue
        try:
            g = int(s)
        except (ValueError, TypeError):
            continue
        total += g
        if g == 0:
            zero += 1
        valid += 1

    if valid == 0:
        return None

    return {
        "avg_goals": total / valid,
        "drought_pct": (zero / valid) * 100,
    }
