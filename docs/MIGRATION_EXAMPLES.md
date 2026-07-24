# MCP Client Migration Examples

This document shows concrete examples of migrating from direct Yahoo API calls to the MCP client.

## Example 1: Yahoo Rankings Command

### Current Code (yahoo_client)

```python
# In cli.py - line 566-572
if args.command == 'yahoo-rankings':
    keepers = build_keepers(args.keeper)
    access_token = resolve_access_token(args.access_token)
    yahoo_rankings = fetch_yahoo_rankings(access_token)
    aggregated = aggregate_rankings(yahoo_rankings, keepers)
    print(json.dumps(aggregated[:100], indent=2))
    return
```

Problems:
- Requires managing access tokens
- Token may be expired, requires refresh logic
- Makes direct API calls

### Migrated Code (mcp_client)

```python
# In cli.py - line 566-572 (updated)
if args.command == 'yahoo-rankings':
    from .mcp_client import get_sync_leagues, get_sync_draft_rankings
    
    try:
        leagues = get_sync_leagues()
        if not leagues:
            print("No leagues found. Make sure MCP server is running on localhost:8000")
            sys.exit(1)
        
        league_id = leagues[0]['id']  # Use first league
        keepers = build_keepers(args.keeper)
        yahoo_rankings = get_sync_draft_rankings(league_id)
        aggregated = aggregate_rankings(yahoo_rankings, keepers)
        print(json.dumps(aggregated[:100], indent=2))
    except Exception as e:
        print(f"Error fetching rankings: {e}", file=sys.stderr)
        print("Make sure MCP server is running: python3 fastmcp_server.py", file=sys.stderr)
        sys.exit(1)
    return
```

Benefits:
- No token management needed
- Automatic token refresh handled by MCP server
- Clear error messages if server isn't running
- Simpler code

## Example 2: Refresh Yahoo Rankings Command

### Current Code

```python
# In cli.py - line 574-579
if args.command == 'refresh-yahoo-rankings':
    access_token = resolve_access_token(None)
    rankings = fetch_yahoo_rankings(access_token, count=args.count)
    save_yahoo_rankings(rankings)
    print(f'Saved {len(rankings)} Yahoo rankings to data/raw/rankings/yahoo_rankings.json')
    return
```

### Migrated Code

```python
# In cli.py - line 574-579 (updated)
if args.command == 'refresh-yahoo-rankings':
    from .mcp_client import get_sync_leagues, get_sync_draft_rankings
    
    try:
        leagues = get_sync_leagues()
        if not leagues:
            print("No leagues found. Make sure MCP server is running on localhost:8000")
            sys.exit(1)
        
        league_id = leagues[0]['id']
        rankings = get_sync_draft_rankings(league_id)
        save_yahoo_rankings(rankings)
        print(f'Saved {len(rankings)} Yahoo rankings to data/raw/rankings/yahoo_rankings.json')
    except Exception as e:
        print(f"Error refreshing rankings: {e}", file=sys.stderr)
        print("Make sure MCP server is running: python3 fastmcp_server.py", file=sys.stderr)
        sys.exit(1)
    return
```

## Example 3: Roster Parsing (Web Handler)

### Current Code (web.py)

```python
from .yahoo_client import fetch_yahoo_roster_players, YahooRosterPlayer

@app.route('/api/roster', methods=['GET'])
def get_current_roster():
    access_token = get_access_token_from_session()
    roster = fetch_yahoo_roster_players(access_token)
    return jsonify([asdict(p) for p in roster])
```

### Migrated Code

```python
from .mcp_client import get_roster, YahooRosterPlayer

@app.route('/api/roster', methods=['GET'])
async def get_current_roster():
    try:
        roster = await get_roster(league_id='123.l.456')
        return jsonify([asdict(p) for p in roster])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

Note: Requires Flask to support async views (Flask 2.0+)

## Example 4: Keeper Board Export

### Current Code

```python
# In cli.py
if args.command == 'keepers-board-export':
    access_token = resolve_access_token(args.access_token)
    roster = fetch_yahoo_roster_players(access_token)
    standings = fetch_standings(access_token, league_key)
    # ... process keeper selections ...
```

### Migrated Code

```python
from .mcp_client import get_sync_roster, get_sync_standings

if args.command == 'keepers-board-export':
    try:
        roster = get_sync_roster(league_id='123.l.456')
        standings = get_sync_standings(league_id='123.l.456')
        # ... process keeper selections (no changes needed) ...
    except Exception as e:
        print(f"Error exporting keeper board: {e}", file=sys.stderr)
        sys.exit(1)
```

## Pattern: Try/Except for Graceful Degradation

For all CLI migrations, wrap with error handling:

```python
from .mcp_client import get_sync_leagues

if args.command == 'my-command':
    try:
        leagues = get_sync_leagues()
        # ... rest of command ...
    except ConnectionError as e:
        print("MCP server not running. Start it with:", file=sys.stderr)
        print("  cd fantasy-football-mcp-public && python3 fastmcp_server.py", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
```

## Data Structure Compatibility

The MCP client returns the same structures as yahoo_client:

```python
from dataclasses import asdict

# YahooRosterPlayer works the same way
roster = get_sync_roster(league_id)
for player in roster:
    print(f"{player.playerName} ({player.position}) - {player.team}")
    
# Convert to dict for JSON serialization
roster_json = [asdict(p) for p in roster]
```

## Gradual Migration Strategy

1. **Phase 1**: Add mcp_client.py (done ✓)
2. **Phase 2**: Migrate low-risk CLI commands
   - Start with read-only commands (rankings, standings)
   - Test with MCP server running
   - Verify output matches old behavior
3. **Phase 3**: Migrate web handlers
   - Update Flask routes to use async MCP functions
   - Test in browser
4. **Phase 4**: Remove old yahoo_client.py
   - After confirming all commands work
   - Keep auth_helper.py for future re-auth if needed

## Testing Checklist

After migrating each command:

- [ ] MCP server is running on localhost:8000
- [ ] Command runs without errors
- [ ] Output matches expected format
- [ ] Error messages are clear if server isn't running
- [ ] No token management code remains
- [ ] Data structures (YahooRosterPlayer, etc.) work as before

## Troubleshooting Migrations

### "Connection refused"
```bash
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
python3 fastmcp_server.py
```

### "No leagues found"
- Verify your Yahoo account has active fantasy leagues
- Check that MCP server has valid .env credentials

### "League ID format incorrect"
MCP expects league IDs from `get_sync_leagues()`. Use the returned `id` field directly.

### Tests failing
Update test fixtures to mock the MCP client instead of yahoo_client:
```python
from unittest.mock import patch, MagicMock

@patch('app.mcp_client.get_sync_roster')
def test_roster_parsing(mock_roster):
    mock_roster.return_value = [YahooRosterPlayer(...)]
    # ... test code ...
```
