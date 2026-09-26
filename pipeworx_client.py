"""
Pipeworx API-Football MCP client.
Endpoint: https://gateway.pipeworx.io/api-football/mcp
Auth: x-api-key header with your Pipeworx key (pwx_...)
BYO API-Football key passed via _apiKey argument.
Docs: https://pipeworx.io/docs/reference/api-football/fixtures/
"""
import os
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

PIPEWORX_MCP_URL = "https://gateway.pipeworx.io/api-football/mcp"
PIPEWORX_KEY = os.environ.get("PIPEWORX_API_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

# API-Football league IDs (39=EPL, 140=La Liga, 135=Serie A, 78=Bundesliga, 61=Ligue 1, etc.)
TARGET_LEAGUE_IDS = [39, 40, 140, 141, 135, 136, 78, 79, 61, 62, 88, 89, 94, 95, 179, 180, 203, 204]


def _headers() -> Dict[str, str]:
    if not PIPEWORX_KEY:
        raise RuntimeError("PIPEWORX_API_KEY is not set.")
    return {
        "Content-Type": "application/json",
        "x-api-key": PIPEWORX_KEY,
    }


def _call_tool(session: requests.Session, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Call a tool on the Pipeworx MCP endpoint via JSON-RPC 2.0."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        r = session.post(PIPEWORX_MCP_URL, headers=_headers(), json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            logger.error(f"Pipeworx error on {tool_name}: {data['error']}")
            return None

        result = data.get("result", {})
        # MCP wraps tool output in a "content" array of {type, text}
        content = result.get("content", [])
        if content and isinstance(content, list):
            text = content[0].get("text", "")
            if text:
                return json.loads(text)
        return result
    except requests.exceptions.Timeout:
        logger.error(f"Pipeworx timeout on {tool_name}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Pipeworx request error on {tool_name}: {e}")
        return None
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Pipeworx JSON parse error on {tool_name}: {e}")
        return None


def fetch_todays_fixtures(session: requests.Session) -> List[Dict[str, Any]]:
    """Fetch today's fixtures by iterating over target leagues."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_fixtures = []

    for league_id in TARGET_LEAGUE_IDS:
        args = {
            "date": today,
            "league": league_id,
            "_apiKey": API_FOOTBALL_KEY,
        }
        result = _call_tool(session, "fixtures", args)
        if not result:
            continue
        fixtures = result.get("fixtures", [])
        if fixtures:
            logger.info(f"League {league_id}: {len(fixtures)} fixtures today.")
            all_fixtures.extend(fixtures)

    logger.info(f"Pipeworx: {len(all_fixtures)} total target fixtures today.")
    return all_fixtures


def fetch_team_away_matches(
    session: requests.Session, team_id: int, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch a team's last N matches, filtered to away venue.
    Uses the fixtures tool with team + last + season.
    """
    current_year = datetime.now(timezone.utc).year
    args = {
        "team": team_id,
        "last": limit * 2,  # fetch extra, filter away client-side
        "season": current_year,
        "_apiKey": API_FOOTBALL_KEY,
    }
    result = _call_tool(session, "fixtures", args)
    if not result:
        return []

    fixtures = result.get("fixtures", [])

    # Filter to away matches where this team was the away side
    away = []
    for f in fixtures:
        teams = f.get("teams", {}) or {}
        away_team = teams.get("away", {}) or {}
        if away_team.get("id") == team_id:
            # Only finished matches (have scores)
            goals = f.get("goals", {}) or {}
            if goals.get("away") is not None:
                away.append(f)

    logger.debug(f"Team {team_id}: {len(away)} finished away matches.")
    return away[:limit]


def calculate_away_stats(matches: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Calculate away goal average and drought % from API-Football fixture objects."""
    if not matches:
        return None
    total, zero, valid = 0, 0, 0
    for m in matches:
        goals = m.get("goals", {}) or {}
        away_goals = goals.get("away")
        if away_goals is None:
            continue
        total += away_goals
        if away_goals == 0:
            zero += 1
        valid += 1
    if valid == 0:
        return None
    return {"avg_goals": total / valid, "drought_pct": (zero / valid) * 100}
