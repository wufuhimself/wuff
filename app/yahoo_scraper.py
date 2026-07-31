#!/usr/bin/env python3
"""
Yahoo Fantasy Sports web scraper using Selenium.
Logs in and pulls roster data directly from web pages.
"""

import time
import re
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options as FirefoxOptions
import geckodriver_autoinstaller
from bs4 import BeautifulSoup

from .config import config
from .yahoo_client import YahooRosterPlayer


class YahooScraper:
    """Scrape Yahoo Fantasy Sports data using Selenium with Firefox."""

    BASE_URL = 'https://fantasy.yahoo.com'
    LOGIN_URL = 'https://login.yahoo.com'

    def __init__(self, email: str, password: str, headless: bool = True):
        self.email = email
        self.password = password
        self._last_team_metadata: Dict[str, str] = {}

        # Setup Firefox options
        firefox_options = FirefoxOptions()
        if headless:
            firefox_options.add_argument('--headless')
        firefox_options.add_argument('--disable-blink-features=AutomationControlled')
        firefox_options.set_preference('useAutomationExtension', False)
        firefox_options.set_preference('user-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')

        # Auto-install geckodriver
        geckodriver_autoinstaller.install()

        # Initialize driver
        try:
            self.driver = webdriver.Firefox(options=firefox_options)  # pylint: disable=not-callable
        except Exception as e:
            print(f"Error starting Firefox: {e}")
            self.driver = None

        self._authenticated = False

    def login(self) -> bool:
        """Authenticate with Yahoo using Selenium."""
        try:
            if not self.driver:
                print("✗ Browser driver not initialized")
                return False

            print("Opening Yahoo login page...")
            self.driver.get(self.LOGIN_URL)

            # Wait for page JavaScript to render
            time.sleep(3)

            wait = WebDriverWait(self.driver, 10)

            # Find username field by ID
            print("Looking for username field...")
            username_field = wait.until(EC.visibility_of_element_located((By.ID, 'username')))
            print("✓ Found username field")

            username_field.clear()
            username_field.send_keys(self.email)
            print(f"✓ Entered email: {self.email}")

            # Press Enter to go to password
            from selenium.webdriver.common.keys import Keys
            username_field.send_keys(Keys.RETURN)
            print("✓ Pressed Enter")

            time.sleep(2)

            # Wait for password field
            print("Looking for password field...")
            password_field = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input[type="password"]')))
            print("✓ Found password field")

            password_field.clear()
            password_field.send_keys(self.password)
            print("✓ Entered password")

            # Press Enter to sign in
            password_field.send_keys(Keys.RETURN)
            print("✓ Pressed Enter")

            # Wait briefly for redirect away from login page
            time.sleep(2)
            print(f"Current URL: {self.driver.current_url}")

            # Check for passkey setup prompt and skip it
            try:
                print("Checking for passkey prompt...")
                skip_buttons = self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Skip') or contains(text(), 'skip')]")
                if skip_buttons:
                    print("✓ Found Skip button, clicking...")
                    skip_buttons[0].click()
                    time.sleep(5)  # Wait for redirect after skip
            except Exception:
                pass

            print(f"Current URL after skip: {self.driver.current_url}")

            # Check if we're away from the challenge/login page
            current_url = self.driver.current_url.lower()
            if 'challenge' not in current_url and 'login' not in current_url:
                print("✓ Authenticated! Away from login/challenge pages")
                self._authenticated = True
                return True

            # If still on challenge page, wait a bit more
            if 'challenge' in current_url:
                print("⚠ Still on challenge page, waiting...")
                time.sleep(3)
                if 'challenge' not in self.driver.current_url.lower() and 'login' not in self.driver.current_url.lower():
                    print("✓ Redirected away from challenge page")
                    self._authenticated = True
                    return True

            print("✗ Still on login/challenge page")
            return False

        except Exception as e:
            print(f"✗ Login failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_roster(self, team_id: Optional[str] = None) -> Optional[List[YahooRosterPlayer]]:
        """Fetch roster for the configured team or a specific team id."""
        if not self._authenticated:
            if not self.login():
                return None

        try:
            league_id = config.yahoo_league_id
            team_key = config.yahoo_team_key

            # Extract team ID from team_key (format: league.team)
            resolved_team_id = team_id or (team_key.split('.')[-1] if '.' in team_key else team_key)

            # Navigate to roster page using correct URL structure
            roster_url = f'https://football.fantasysports.yahoo.com/f1/{league_id}/{resolved_team_id}'

            print(f"Loading roster from: {roster_url}")
            self.driver.get(roster_url)

            # Wait for roster table to load
            wait = WebDriverWait(self.driver, 10)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, 'table')))

            time.sleep(2)  # Let JS render fully

            # Parse page with BeautifulSoup
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            self._last_team_metadata = self._extract_team_metadata(soup)

            # Parse roster table
            players = self._parse_roster_table(soup)

            if players:
                print(f"✓ Found {len(players)} players")
                return players

            print("✗ No players found in roster")
            return None

        except Exception as e:
            print(f"✗ Error fetching roster: {e}")
            return None

    def _extract_team_metadata(self, soup: BeautifulSoup) -> Dict[str, str]:
        """Extract team/owner labels from a roster page."""
        metadata: Dict[str, str] = {}

        title = soup.title.get_text(' ', strip=True) if soup.title else ''
        if title:
            metadata['pageTitle'] = title
            team_name = title.split('|')[0].strip()
            if team_name:
                metadata['teamName'] = team_name

        # Yahoo usually renders manager text inline near team header.
        page_text = soup.get_text('\n', strip=True)
        owner_match = re.search(r'\bManagers?\s*:\s*([^\n|]+)', page_text, re.IGNORECASE)
        if owner_match:
            owner_name = re.sub(r'\s+', ' ', owner_match.group(1)).strip()
            if owner_name:
                metadata['ownerName'] = owner_name

        return metadata

    def get_league_rosters(self, team_ids: List[int]) -> List[Dict[str, Any]]:
        """Fetch rosters for each team id in the league."""
        rosters: List[Dict[str, Any]] = []
        if not self._authenticated:
            if not self.login():
                return rosters

        for team_id in team_ids:
            print(f"\n=== Team {team_id} ===")
            players = self.get_roster(str(team_id)) or []
            team_metadata = dict(self._last_team_metadata)
            rosters.append(
                {
                    'teamId': team_id,
                    'ownerName': team_metadata.get('ownerName', ''),
                    'teamName': team_metadata.get('teamName', ''),
                    'playerCount': len(players),
                    'players': [player.__dict__ for player in players],
                }
            )

        return rosters

    def get_standings(self, season: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Fetch league standings for a given season (or current if not specified)."""
        if not self._authenticated:
            if not self.login():
                return None

        try:
            league_id = config.yahoo_league_id
            standings_url = f'https://football.fantasysports.yahoo.com/f1/{league_id}/standings'
            if season:
                standings_url += f'?season={season}'

            print(f"Loading standings from: {standings_url}")
            self.driver.get(standings_url)

            # Wait for standings table to load
            wait = WebDriverWait(self.driver, 10)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, 'table')))

            time.sleep(2)  # Let JS render fully

            # Parse page with BeautifulSoup
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')

            # Parse standings table
            standings = self._parse_standings_table(soup)

            if standings:
                print(f"✓ Found {len(standings)} teams")
                return {
                    'year': season,
                    'standings': standings,
                }

            print("✗ No standings found")
            return None

        except Exception as e:
            print(f"✗ Error fetching standings: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _parse_standings_table(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract standings data from standings page HTML."""
        standings = []

        tables = soup.find_all('table')
        if not tables:
            print("✗ No tables found on standings page")
            return standings

        # Find the standings table (usually the first or second table)
        standings_table = None
        for table in tables:
            rows = table.find_all('tr')
            if rows and len(rows) > 1:
                # Check if header contains "Rank" or "Team" or "W-L"
                header_text = rows[0].get_text().lower()
                if 'rank' in header_text or 'team' in header_text or 'w-l' in header_text:
                    standings_table = table
                    break

        if not standings_table:
            print("✗ Could not find standings table")
            return standings

        rows = standings_table.find_all('tr')
        print(f"Parsing standings table ({len(rows)} rows)...")

        # Skip header row(s)
        for row in rows[1:]:
            try:
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue

                # Extract data from cells
                # Typical layout: Rank | Team | W-L | PF | PA | ...
                rank_text = cells[0].get_text(strip=True)
                team_text = cells[1].get_text(strip=True)
                wl_text = cells[2].get_text(strip=True)

                # Parse rank
                rank = None
                for char in rank_text:
                    if char.isdigit():
                        rank = int(''.join(c for c in rank_text if c.isdigit()))
                        break

                if rank is None:
                    continue

                # Parse W-L-T
                wl_match = re.search(r'(\d+)[- ](\d+)(?:[- ](\d+))?', wl_text)
                if not wl_match:
                    continue

                wins = int(wl_match.group(1))
                losses = int(wl_match.group(2))
                ties = int(wl_match.group(3)) if wl_match.group(3) else 0

                # Points for/against are usually in later cells
                points_for = 0.0
                points_against = 0.0
                if len(cells) > 3:
                    pf_text = cells[3].get_text(strip=True)
                    try:
                        points_for = float(pf_text)
                    except ValueError:
                        pass

                if len(cells) > 4:
                    pa_text = cells[4].get_text(strip=True)
                    try:
                        points_against = float(pa_text)
                    except ValueError:
                        pass

                standings.append({
                    'rank': rank,
                    'team': team_text,
                    'wins': wins,
                    'losses': losses,
                    'ties': ties,
                    'pointsFor': points_for,
                    'pointsAgainst': points_against,
                })
                print(f"  ✓ Rank {rank}: {team_text} ({wins}-{losses}-{ties}) PF {points_for:.0f} PA {points_against:.0f}")

            except Exception as e:
                print(f"  ✗ Error parsing row: {e}")
                continue

        return sorted(standings, key=lambda x: x['rank'])

    def _parse_roster_row(self, row) -> Optional[YahooRosterPlayer]:
        cells = row.find_all('td')
        if len(cells) < 4:
            return None

        # Cell 0: Player name (contains extra noise)
        player_text = cells[0].get_text(' ', strip=True)
        cleaned_text = re.sub(r'\b(?:New Player Note|Player Note|No new|Video|Forecast|NA)\b', ' ', player_text)
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

        player_name = cleaned_text
        team = 'UNK'
        position = 'UNK'

        match = re.search(r'(.+?)\s+([A-Za-z]{2,3})\s*-\s*([A-Z/]{2,3})', cleaned_text)
        if match:
            player_name = match.group(1).strip()
            team = match.group(2).upper()
            position = match.group(3).upper()

        if not player_name or len(player_name) < 2:
            return None

        # Cell 3 sometimes carries the position marker in parentheses.
        pos_text = cells[3].get_text(' ', strip=True)
        if position == 'UNK' and '(' in pos_text and ')' in pos_text:
            position = pos_text[pos_text.index('(') + 1:pos_text.index(')')].strip().upper()

        if team == 'UNK':
            tail_match = re.search(r'([A-Za-z]{2,3})\s*-\s*([A-Z/]{2,3})', cleaned_text)
            if tail_match:
                team = tail_match.group(1).upper()
                if position == 'UNK':
                    position = tail_match.group(2).upper()

        return YahooRosterPlayer(
            playerId=player_name,
            playerName=player_name,
            position=position,
            team=team,
        )

    def _parse_roster_table(self, soup: BeautifulSoup) -> List[YahooRosterPlayer]:
        """Extract player data from roster page HTML."""
        players = []

        tables = soup.find_all('table')
        if len(tables) < 2:
            print(f"✗ Expected at least 2 tables, found {len(tables)}")
            return players

        # Table 1 = Offense, Table 2 = Defense/ST
        for table_idx, table in enumerate(tables[1:3]):  # Tables 1 and 2
            rows = table.find_all('tr')
            table_name = "Offense" if table_idx == 0 else "Defense/ST"
            print(f"Parsing {table_name} table ({len(rows)} rows)...")

            # Skip header rows (first 2)
            for row in rows[2:]:
                try:
                    player = self._parse_roster_row(row)
                except Exception:
                    continue
                if player is None:
                    continue
                players.append(player)
                print(f"  ✓ {player.playerName:25} | {player.position:5} | {player.team}")

        return players

    def _extract_player_id(self, url: str) -> Optional[str]:
        """Extract player ID from Yahoo URL."""
        try:
            if '/player/' in url:
                parts = url.split('/player/')
                if len(parts) > 1:
                    player_id = parts[1].split('/')[0]
                    return player_id
            elif '/players/' in url:
                parts = url.split('/players/')
                if len(parts) > 1:
                    player_id = parts[1].split('/')[0]
                    return player_id
        except Exception:
            pass
        return None

    def close(self):
        """Close browser."""
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def scrape_roster(email: str, password: str, headless: bool = True) -> Optional[List[YahooRosterPlayer]]:
    """Convenience function to scrape roster."""
    scraper = YahooScraper(email, password, headless=headless)
    try:
        if scraper.login():
            time.sleep(1)
            return scraper.get_roster()
    finally:
        scraper.close()
    return None


def scrape_league_rosters(email: str, password: str, team_count: int = 12, headless: bool = True) -> List[Dict[str, Any]]:
    """Convenience function to scrape every team's roster in the league."""
    scraper = YahooScraper(email, password, headless=headless)
    try:
        if not scraper.login():
            return []
        team_ids = list(range(1, team_count + 1))
        return scraper.get_league_rosters(team_ids)
    finally:
        scraper.close()


def scrape_standings(email: str, password: str, season: Optional[int] = None, headless: bool = True) -> Optional[Dict[str, Any]]:
    """Convenience function to scrape league standings."""
    scraper = YahooScraper(email, password, headless=headless)
    try:
        if scraper.login():
            time.sleep(1)
            return scraper.get_standings(season)
    finally:
        scraper.close()
    return None
