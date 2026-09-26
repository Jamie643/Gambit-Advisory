"""
Pipeworx API-Football client.
Endpoint: https://gateway.pipeworx.io/api-football/mcp
Auth: x-api-key header with your Pipeworx key
BYO API-Football key passed via _apiKey argument.

Tool schemas (verified from tools/list):
  fixtures(league, team, date, season, last, next, _apiKey)
  team_search(name, country, _apiKey)
  league_search(name, country, _apiKey)
  h2h(team1, team2, last, _apiKey)

Response shape is FLAT (not the nested API-Football shape):
  {count: N, fixtures: [{fixture_id, date, status, venue, league, round,
                         home, away, home_team_id, away_team_id,
                         score_home, score_away}, ...]}
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

# API-Football league IDs — trimmed for the free 50-call/day Pipeworx tier
TARGET_LEAGUE_IDS = [
    39,   # English Premier League
    40,   # English Championship
    140,  # La Liga
    135,  # Serie A
    78,   # Bundesliga
    61,   # Ligue 1
    88,   # Eredivisie
    94,   # Primeira Liga
]


def _headers() -> Dict[str, str]:
    if not PIPEWORX_KEY:
        raise RuntimeError("PIPEWORX_API_KEY is not set.")
    return {
        "Content-Type": "application/json",
        "x-api-key": PIPEWORX_KEY,
    }


def _call_tool(session: requests.Session, tool_name: str, arguments: Dict[str, Any]) -> Optional[Any]:
    """
    Call a Pipeworx MCP tool via JSON-RPC 2.0.
    Returns the parsed tool result (the structured object), or None on failure.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        r = session.post(PIPEWORX_MCP_URL, headers=_headers(), json=payload, timeout=25)
        logger.info(f"[Pipeworx] {tool_name} -> HTTP {r.status_code}")
        if r.status_code != 200:
            logger.error(f"[Pipeworx] {tool_name} non-200 body: {r.text[:400]}")
            return None
        data = r.json()
        if "error" in data:
            logger.error(f"[Pipeworx] {tool_name} JSON-RPC error: {data['error']}")
            return None

        result = data.get("result", {})

        # Try structured content first (preferred by newer MCP servers)
        if isinstance(result, dict):
            if "structuredContent" in result and result["structuredContent"]:
                return result["structuredContent"]

            # Fall back to the MCP standard content array
            content = result.get("content", [])
            if content and isinstance(content, list) and content:
                first = content[0]
                # content[0] may be {"type": "text", "text": "<json string>"}
                text = first.get("text") if isinstance(first, dict) else None
                if text:
                    try:
                        return json.loads(text)
                    except (ValueError, TypeError):
                        return {"raw": text}

        # Some servers return the tool result directly under `result`
        return result if isinstance(result, (dict, list)) else None

    except requests.exceptions.Timeout:
        logger.error(f"[Pipeworx] Timeout on {tool_name}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[Pipeworx] Request error on {tool_name}: {e}")
        return None
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"[Pipeworx] JSON parse error on {tool_name}: {e}")
        return None


def fetch_todays_fixtures(session: requests.Session) -> List[Dict[str, Any]]:
    """Fetch today's fixtures for each target league (one call per league)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_fixtures: List[Dict[str, Any]] = []

    for league_id in TARGET_LEAGUE_IDS:
        args = {
            "league": league_id,
            "date": today,
            "season": datetime.now(timezone.utc).year,
            "_apiKey": API_FOOTBALL_KEY,
        }
        result = _call_tool(session, "fixtures", args)
        if league_id == TARGET_LEAGUE_IDS[0]:  # only log the first league
            logger.info(f"[DEBUG] league {league_id} raw result: {json.dumps(result)[:600]}")
        if not isinstance(result, dict):
            continue
        fixtures = result.get("fixtures") or []
        if fixtures:
            logger.info(f"League {league_id}: {len(fixtures)} fixtures today.")
        all_fixtures.extend(fixtures)

    logger.info(f"Pipeworx: {len(all_fixtures)} total target fixtures today.")
    return all_fixtures


def fetch_team_away_matches(
    session: requests.Session, team_id: int, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch a team's last N played matches, then filter to away matches client-side.
    Response shape is flat: {fixture_id, date, home, away, home_team_id,
    away_team_id, score_home, score_away, ...}
    """
    args = {
        "team": team_id,
        "last": limit * 3,  # fetch extra since ~half will be home matches
        "_apiKey": API_FOOTBALL_KEY,
    }
    result = _call_tool(session, "fixtures", args)
    if not isinstance(result, dict):
        return []

    fixtures = result.get("fixtures") or []

    away_matches = []
    for f in fixtures:
        # Team is away if away_team_id == our team_id
        if f.get("away_team_id") == team_id:
            # Only consider played matches (score present)
            if f.get("score_away") is not None:
                away_matches.append(f)

    logger.debug(f"Team {team_id}: {len(away_matches)} finished away matches.")
    return away_matches[:limit]


def calculate_away_stats(matches: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """
    Calculate away goal average and drought % from flat fixture objects.
    Uses score_away (not nested goals.away).
    """
    if not matches:
        return None
    total, zero, valid = 0, 0, 0
    for m in matches:
        score = m.get("score_away")
        if score is None:
            continue
        try:
            goals = int(score)
        except (ValueError, TypeError):
            continue
        total += goals
        if goals == 0:
            zero += 1
        valid += 1
    if valid == 0:
        return None
    return {"avg_goals": total / valid, "drought_pct": (zero / valid) * 100}
