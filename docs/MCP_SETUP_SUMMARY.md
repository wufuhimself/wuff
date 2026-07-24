# Wuff ↔️ Fantasy Football MCP Integration - Setup Summary

## ✅ What's Been Done

### 1. **Created MCP Client Wrapper** (`app/mcp_client.py`)
   - Wraps wuff's existing `yahoo_client` with credentials from the MCP server
   - Automatically loads OAuth tokens from fantasy-football-mcp-public/.env
   - Provides clean interface functions:
     - `get_sync_leagues()`
     - `get_sync_roster(league_id)`
     - `get_sync_draft_rankings(league_id)`
     - `get_sync_standings(league_id)`
     - `get_sync_keepers(league_id)`

### 2. **Migrated CLI Commands** (`app/cli.py`)
   The following commands now use the MCP client:
   - ✅ `yahoo-rankings` - Fetch draft rankings
   - ✅ `refresh-yahoo-rankings` - Save rankings to file
   - ✅ `keeper-insight` - Analyze keeper options
   - ✅ `best-keepers` - Select best keepers per team
   - ✅ `forecast-keepers` - Predict opponent keepers
   - ✅ `yahoo-roster` - Display your roster
   - ✅ `save-roster` - Persist roster locally
   - ✅ `yahoo-keepers` - Show keeper-eligible players

### 3. **Updated Configuration**
   - `requirements.txt` - Added `httpx` (for MCP client infrastructure)
   - `wuff/.env` - Synced with MCP server credentials
   - `wuff/data/auth/yahoo_token.json` - Updated with fresh OAuth tokens

### 4. **Created Documentation**
   - `docs/MCP_INTEGRATION.md` - Integration guide
   - `docs/MIGRATION_EXAMPLES.md` - Before/after code examples
   - `docs/MCP_MIGRATION_COMPLETE.md` - Status and architecture
   - `docs/MCP_SETUP_SUMMARY.md` - This file

## 🚀 How to Use

### Prerequisites
1. MCP Server running on localhost:8000:
   ```bash
   cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
   python3 fastmcp_server.py
   ```

2. Fresh OAuth tokens (see "Token Setup" below)

### Run CLI Commands
```bash
cd /Users/mattwufsus/Repos/wuff
source .venv/bin/activate

# Examples
python -m app yahoo-rankings
python -m app refresh-yahoo-rankings
python -m app save-roster
python -m app best-keepers
```

## 🔑 Token Setup

### Current Status
The OAuth tokens need to be valid. If you're getting 401 errors:

#### Option 1: Re-authenticate via MCP Server
```bash
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
source venv/bin/activate
python3 get_yahoo_token.py  # Interactive OAuth flow
```
This will:
- Open your browser to Yahoo OAuth
- Authorize the app
- Automatically update `.env` with fresh tokens

Then copy the tokens to wuff:
```bash
# The mcp_client.py automatically loads from
# fantasy-football-mcp-public/.env, so no manual copy needed!
```

#### Option 2: Manual Token Refresh
```bash
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
source venv/bin/activate
python3 utils/refresh_yahoo_token.py
```

### Token Flow
```
wuff CLI
   ↓
app/mcp_client.py (loads tokens from environment)
   ↓
fantasy-football-mcp-public/.env (source of truth)
   ↓
wuff/data/auth/yahoo_token.json (local token file, optional)
   ↓
Yahoo Fantasy API
```

## 📋 Architecture

**Before Migration:**
```
wuff CLI
  ↓
yahoo_client.py (direct API calls)
  ↓
Manage tokens locally in wuff/.env
  ↓
Yahoo Fantasy API
```

**After Migration:**
```
wuff CLI
  ↓
mcp_client.py (wraps yahoo_client with MCP creds)
  ↓
fantasy-football-mcp-public/.env (centralized OAuth)
  ↓
Yahoo Fantasy API
```

## 🎯 Benefits

✅ **Single source of truth** - Tokens managed in one place (MCP server)
✅ **No token duplication** - MCP server keeps tokens fresh  
✅ **Cleaner code** - No access_token parameters in wuff
✅ **Easier maintenance** - Fix token issues in one place
✅ **Modular** - MCP server can scale independently

## ⚙️ Troubleshooting

### "401 Unauthorized" Errors
**Problem**: Tokens are expired or invalid
**Solution**:
```bash
# Re-authenticate
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
python3 get_yahoo_token.py
```

### "No leagues found"
**Problem**: User might not have active leagues for current season
**Solution**:
- Verify Yahoo account has fantasy leagues
- Check YAHOO_GUID in .env is correct
- Try previous season year

### MCP Server not found
**Problem**: Fantasy Football MCP server isn't running
**Solution**:
```bash
cd /Users/mattwufsus/Repos/fantasy-football-mcp-public
python3 fastmcp_server.py
```

## 📚 Related Documentation

- **MCP Integration Guide**: `docs/MCP_INTEGRATION.md`
- **Code Examples**: `docs/MIGRATION_EXAMPLES.md`
- **MCP Server**: `../fantasy-football-mcp-public/README.md`
- **MCP Server Installation**: `../fantasy-football-mcp-public/INSTALLATION.md`

## 🔄 Migration Status

| Component | Status | Notes |
|-----------|--------|-------|
| MCP Client Wrapper | ✅ Complete | Syncs with MCP server .env |
| CLI Commands | ✅ Complete | 8 commands migrated |
| Token Management | ✅ Integrated | Loads from MCP server |
| Documentation | ✅ Complete | 4 guide docs created |
| Testing | ⏳ Pending | Needs valid OAuth tokens |
| Remaining Commands | ⏳ Future | `roster-raw`, `set-lineup` (lower priority) |

## 🚦 Next Steps

1. **Verify OAuth tokens are valid** (resolve any 401 errors)
2. **Test each CLI command** with live data
3. **Monitor token refresh** via MCP server logs
4. **Optional**: Migrate remaining commands (`roster-raw`, `set-lineup`)
5. **Optional**: Update web handlers to use async MCP client

## 📝 Notes

- MCP client automatically uses current year for season (2026)
- Tokens are loaded from environment at runtime (no hardcoding)
- All existing data structures (YahooRosterPlayer, etc.) preserved for compatibility
- No breaking changes to CLI interface

## ✨ Status: Ready for Testing

The migration is complete. Once you have valid OAuth tokens, all migrated commands should work seamlessly.
