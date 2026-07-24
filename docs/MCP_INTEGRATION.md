# Yahoo Fantasy Football MCP Integration

This document explains how to use the MCP (Model Context Protocol) client to access Yahoo Fantasy Football data instead of making direct API calls.

## Overview

The `app/mcp_client.py` module provides a clean wrapper around the Yahoo Fantasy Football MCP server. Instead of:
1. Managing OAuth tokens yourself
2. Making raw HTTP requests to Yahoo's API
3. Parsing complex XML/JSON responses

You now get:
- High-level functions like `get_leagues()`, `get_roster()`, `build_lineup()`
- Automatic token refresh
- Built-in error handling and logging

## Starting the MCP Server

The MCP server must be running before you can use the MCP client:

```bash
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
source venv/bin/activate
python3 fastmcp_server.py
```

The server will listen on `http://localhost:8000/mcp`

## Using the MCP Client

### Async Functions (Recommended)

For async contexts (like web handlers), use the async functions directly:

```python
from app.mcp_client import get_leagues, get_roster

# Get all leagues
leagues = await get_leagues()

# Get roster for a specific league
roster = await get_roster(league_id='123.l.456')
```

### Sync Functions (CLI/Scripts)

For CLI commands and scripts, use the sync wrappers:

```python
from app.mcp_client import get_sync_leagues, get_sync_roster

# Get all leagues
leagues = get_sync_leagues()

# Get roster for a specific league
roster = get_sync_roster(league_id='123.l.456')
```

## Available Functions

### League Management
- `get_leagues()` - Get all leagues for your account
- `get_league_info(league_id)` - Get league details and settings
- `get_standings(league_id)` - Get current standings

### Roster Management
- `get_roster(league_id, team_key=None)` - Get team roster
- `get_players(league_id, position=None, count=50)` - Get free agents
- `get_waiver_wire(league_id, count=10)` - Get waiver wire targets

### Draft & Rankings
- `get_draft_rankings(league_id)` - Get pre-draft rankings
- `get_matchup(league_id, week=None)` - Get weekly matchup details
- `build_lineup(league_id, strategy='balanced')` - Optimize lineup

### Token Management
- `refresh_token()` - Manually refresh OAuth token

## Migration Examples

### Old (Direct Yahoo API)

```python
from app.yahoo_client import fetch_yahoo_roster_players

access_token = get_access_token()
roster = fetch_yahoo_roster_players(access_token)
```

### New (MCP Client)

```python
from app.mcp_client import get_sync_roster

roster = get_sync_roster(league_id='123.l.456')
```

Notice: No token management needed! The MCP server handles it internally.

## Updating CLI Commands

### Example: Yahoo Rankings Command

**Before:**
```python
if args.command == 'yahoo-rankings':
    access_token = resolve_access_token(args.access_token)
    yahoo_rankings = fetch_yahoo_rankings(access_token)
    aggregated = aggregate_rankings(yahoo_rankings, keepers)
    print(json.dumps(aggregated[:100], indent=2))
```

**After:**
```python
if args.command == 'yahoo-rankings':
    from .mcp_client import get_sync_draft_rankings
    
    leagues = get_sync_leagues()
    if not leagues:
        print("No leagues found. Make sure MCP server is running on localhost:8000")
        sys.exit(1)
    
    league_id = leagues[0]['id']  # Use first league
    yahoo_rankings = get_sync_draft_rankings(league_id)
    aggregated = aggregate_rankings(yahoo_rankings, keepers)
    print(json.dumps(aggregated[:100], indent=2))
```

## Configuration

The MCP client connects to `http://localhost:8000/mcp` by default. To change this:

```python
# In mcp_client.py
MCP_SERVER_URL = "http://your-custom-host:port/mcp"
```

Or set an environment variable:
```python
import os
MCP_SERVER_URL = os.getenv('MCP_SERVER_URL', 'http://localhost:8000/mcp')
```

## Error Handling

The MCP client includes logging for all tool calls. Enable debug logging to see details:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

If the MCP server is not running, you'll get an `httpx.ConnectError`. Handle it gracefully:

```python
from app.mcp_client import get_sync_leagues

try:
    leagues = get_sync_leagues()
except Exception as e:
    print(f"MCP server error: {e}")
    print("Make sure FastMCP server is running on localhost:8000")
    sys.exit(1)
```

## Data Structures

The MCP client returns the same data structures as the old yahoo_client:
- `YahooRosterPlayer` - Roster player with id, name, position, team
- `League` dict - League information
- `Matchup` dict - Matchup details with projections

This makes migration straightforward - existing code that processes these structures will continue to work.

## Troubleshooting

### "Connection refused" error
The MCP server isn't running. Start it with:
```bash
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
python3 fastmcp_server.py
```

### "Token expired" errors
The MCP server handles token refresh automatically. If you get token errors:
1. Check that your `.env` file has valid Yahoo credentials
2. Restart the MCP server

### "League not found" errors
Make sure your Yahoo account has active leagues for the current season. Verify with `get_leagues()` that leagues exist.

## Next Steps

1. Start the MCP server on localhost:8000
2. Install httpx: `pip install httpx`
3. Begin replacing `yahoo_client` calls with `mcp_client` equivalents
4. Test CLI commands that use the new MCP functions
5. Migrate web handlers to use async MCP functions

For more details on the MCP server capabilities, see:
`/Users/mattwufsus/Repos/fantasy-football-mcp-public/README.md`
