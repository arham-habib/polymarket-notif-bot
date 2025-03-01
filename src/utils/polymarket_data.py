import json
import pandas as pd
import time
import requests
import logging
from tqdm import tqdm
from datetime import datetime
import random
from typing import Tuple, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

from py_clob_client.client import ClobClient

# Polymarket API endpoints
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_GAMMA_HOST = "https://gamma-api.polymarket.com"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
)

logger = logging.getLogger(__name__)

class PolymarketDataService:
    """Service for fetching and analyzing Polymarket data"""
    
    INTERVAL_MAP = {
        '1m': 60,
        '5m': 300, 
        '30m': 1800,
        '1h': 3600,
        '6h': 21600,
        '1d': 86400
    }
    
    def __init__(self, config: dict, known_cursors: List[str] = None):
        """Initialize the Polymarket data service"""
        self.config = config
        self.cursors = known_cursors or []
        self.markets = {}
        self.init_markets()
        
    def init_markets(self):
        """Initial load of markets when service is first created"""
        markets, new_cursors = self._polymarket_crawl_live_markets()
        self.cursors += new_cursors
        self.markets = self._get_tracked_markets(markets)
        logger.info(f"Parsed {len(markets)} total markets")
        logger.info(f"Initialized {len(self.markets)} tracked markets")
        
    def load_markets(self):
        """Load all markets, making note of new markets and new cursors"""
        markets, new_cursors = self._polymarket_crawl_live_markets()
        self.cursors += new_cursors

        logger.info(f"{len(markets)} live markets parsed")

        # Identify newly added markets
        new_markets = {
            condition_id: market
            for condition_id, market in markets.items()
            if condition_id not in self.markets
        }

        # Identify markets that might have closed/changed acceptance since last time
        closed_markets = {
            condition_id: market
            for condition_id, market in markets.items()
            if condition_id in self.markets
               and market["accepting_orders"] != self.markets[condition_id]["accepting_orders"]
        }
        
        # Filter new markets by tracked config
        tracked_new_markets = self._get_tracked_markets(new_markets)
        # Update our in-memory dictionary
        self.markets.update(tracked_new_markets)

        logger.info(f"{len(tracked_new_markets)} new tracked markets, {len(closed_markets)} closed tracked markets")
        logger.info(f"Last 5 pages scanned: {self.cursors[-5:]}")
        logger.info(f"{len(self.markets)} markets in memory")
        
        return tracked_new_markets, closed_markets
    
    def _get_token_ids(self, market: Dict[str, Any]) -> Tuple[str, str]:
        """Extract Yes/No token IDs from market data"""
        token1, token2 = market["tokens"]
        yes_token = token1["token_id"] if token1["outcome"] == "Yes" else token2["token_id"]
        no_token = token1["token_id"] if token1["outcome"] == "No" else token2["token_id"]
        return yes_token, no_token
    
    def _update_market_history(self, condition_id: str, 
                               yes_history: pd.DataFrame, 
                               no_history: pd.DataFrame) -> None:
        """Update market price history if both histories are valid"""
        if yes_history is not None and not yes_history.empty and no_history is not None and not no_history.empty:
            history_data = {
                "yes_history": yes_history,
                "no_history": no_history
            }
            if "price_history" in self.markets[condition_id]:
                self.markets[condition_id]["price_history"].update(history_data)
            else:
                self.markets[condition_id]["price_history"] = history_data
    
    def _fetch_market_history(self, condition_id: str, market: Dict[str, Any]) -> None:
        """Fetch and update history for a single market"""
        try:
            yes_token, no_token = self._get_token_ids(market)
            
            # Small delay to avoid rate limiting
            time.sleep(random.uniform(0.1, 0.3))
            
            yes_history = self._get_price_history(yes_token, "1d")
            no_history = self._get_price_history(no_token, "1d")
            
            self._update_market_history(condition_id, yes_history, no_history)
        except Exception as e:
            logger.error(f"Error fetching history for market {condition_id}: {str(e)}")
    
    def _get_price_history(
        self,
        token_id: str, 
        interval: str = None, 
        start_ts: int = None, 
        end_ts: int = None, 
        fidelity: int = 5,
        retry_limit: int = 3,
        base_backoff: float = 1.0
    ) -> pd.Series:
        """
        Get price history for a market using either an interval or timestamp range.
        Retries on 429 with exponential backoff.
        """
        if not interval and not (start_ts and end_ts):
            raise ValueError("Must provide either interval or both start_ts and end_ts")
        if interval and (start_ts or end_ts):
            raise ValueError("Cannot provide both interval and timestamps")

        attempt = 0
        while attempt < retry_limit:
            try:
                params = {"market": token_id, "fidelity": fidelity}
                if interval:
                    params["interval"] = interval
                else:
                    params["startTs"] = start_ts
                    params["endTs"] = end_ts

                response = requests.get(f"{POLYMARKET_HOST}/prices-history", params=params)
                
                if response.status_code == 200:
                    data = response.json()
                    df = pd.Series(
                        [d['p'] for d in data['history']], 
                        index=[d['t'] for d in data['history']]
                    )
                    return df
                elif response.status_code == 429:
                    # Rate-limited: backoff and retry
                    logger.warning(f"Rate-limited on token {token_id}, attempt {attempt+1}")
                    time.sleep(base_backoff * 2**attempt)
                    attempt += 1
                else:
                    logger.error(f"Failed to fetch price history ({response.status_code}) for {token_id}")
                    return None

            except requests.RequestException as e:
                logger.error(f"Error fetching price history for {token_id}: {str(e)}")
                return None

        logger.error(f"Exhausted retries fetching price history for {token_id}.")
        return None
    
    def check_markets(self, use_threads=False):
        """Check price history of tracked markets"""
        if use_threads:
            # Use ThreadPoolExecutor for concurrent requests with a limited number of workers
            with ThreadPoolExecutor(max_workers=5) as executor:
                list(tqdm(
                    executor.map(
                        lambda item: self._fetch_market_history(item[0], item[1]),
                        self.markets.items()
                    ),
                    total=len(self.markets),
                    desc="Checking market histories",
                    unit="market"
                ))
        else:
            # Sequential version
            for condition_id, market in tqdm(
                self.markets.items(),
                desc="Checking market histories",
                unit="market"
            ):
                self._fetch_market_history(condition_id, market)
        
        # After we have updated data, check for changes
        return self._market_price_changes()
    
    def _market_price_changes(self):
        """Figure out if any of the markets have changed in excess of defined thresholds"""
        current_ts = int(datetime.now().timestamp())
        price_change_alerts = []
        
        for interval, threshold in self.config.items():
            if interval not in self.INTERVAL_MAP:
                continue
            interval_start = current_ts - self.INTERVAL_MAP[interval]
            
            # Get all price changes for this interval, regardless of threshold
            interval_changes = []
            for condition_id, market in tqdm(self.markets.items(),
                                           desc=f"Checking {interval} price changes",
                                           unit="market"):
                change = self._get_price_change(condition_id, market, interval, interval_start, current_ts, threshold)
                if change:
                    interval_changes.append(change)
            
            # Get top movers for this interval
            top_movers = self._get_top_movers(interval_changes)
            if top_movers:
                price_change_alerts.extend(top_movers)
            
            # Add alerts that exceeded threshold
            threshold_alerts = [change for change in interval_changes if abs(change.get("yes_price_change", 0)) >= threshold]
            for alert in threshold_alerts:
                self.markets[alert["condition_id"]]["last_notification"] = current_ts
            price_change_alerts.extend(threshold_alerts)
                
        return price_change_alerts

        
    def _get_top_movers(self, changes, top_n=3):
        """Get the top N price movers from a list of price changes"""
        if not changes:
            return []
            
        # Sort by absolute price change
        sorted_changes = sorted(
            changes,
            key=lambda x: abs(x.get("yes_price_change", 0)),
            reverse=True
        )
        
        # Add flag to indicate these are top movers
        top_alerts = []
        for change in sorted_changes[:top_n]:
            top_alert = change.copy()
            top_alert["is_top_mover"] = True
            top_alerts.append(top_alert)
            
        return top_alerts
    
    def _get_price_change(self, condition_id: str, market: dict, interval: str, 
                          interval_start: int, current_ts: int, threshold: float):
        """Check whether a market's price fluctuations have exceeded the threshold."""
        if "price_history" not in market:
            return None
        
        try:
            interval_start_market = max(interval_start, market.get("last_notification", 0))

            # If we recently notified (over a different lookback window), skip
            if (current_ts - interval_start_market) <= 10:
                logger.info(f"Already notified for market {condition_id}")
                return None
            
            yes_interval_data = market["price_history"]["yes_history"].loc[interval_start_market:current_ts]
            no_interval_data = market["price_history"]["no_history"].loc[interval_start_market:current_ts]

            if yes_interval_data.empty or no_interval_data.empty:
                logger.debug(f"Missing yes/no data for {condition_id} in interval.")
                return None

            # Ensure both tokens show enough data for the threshold check
            price_diff_yes = yes_interval_data.max() - yes_interval_data.min()
            price_diff_no = no_interval_data.max() - no_interval_data.min()

            # Take the larger of the two price differences
            price_diff = max(price_diff_yes, price_diff_no)

            if price_diff >= threshold:
                # Find max/min/time for YES
                yes_max_price = yes_interval_data.max()
                yes_min_price = yes_interval_data.min()
                yes_max_time = datetime.fromtimestamp(yes_interval_data.idxmax())
                yes_min_time = datetime.fromtimestamp(yes_interval_data.idxmin())

                # Find max/min/time for NO
                no_max_price = no_interval_data.max()
                no_min_price = no_interval_data.min()
                no_max_time = datetime.fromtimestamp(no_interval_data.idxmax())
                no_min_time = datetime.fromtimestamp(no_interval_data.idxmin())

                # Price changes from earliest to most recent
                yes_price_change = yes_interval_data.iloc[-1] - yes_interval_data.iloc[0]
                no_price_change = no_interval_data.iloc[-1] - no_interval_data.iloc[0]
                
                return {
                    "market": market,
                    "condition_id": condition_id,
                    "interval": interval,
                    "yes_data": {
                        "max_price": yes_max_price,
                        "min_price": yes_min_price,
                        "max_time": yes_max_time,
                        "min_time": yes_min_time,
                        "price_change": yes_price_change
                    },
                    "no_data": {
                        "max_price": no_max_price,
                        "min_price": no_min_price,
                        "max_time": no_max_time,
                        "min_time": no_min_time,
                        "price_change": no_price_change
                    }
                }
            
        except Exception as e: 
            logger.error(f"Error on {condition_id}: {str(e)}")
        
        return None
    
    def _get_tracked_markets(self, markets: dict):
        """Get the tracked markets based on config filters (tags, keywords, etc.)."""
        filter_tags = self.config.get("tags", [])
        filter_keywords = self.config.get("keywords", [])

        tracked_markets = {
            condition_id: market for condition_id, market in markets.items()
            if any(tag in (market.get("tags") or []) for tag in filter_tags)
               or any(keyword.lower() in market.get("question", "").lower() for keyword in filter_keywords)
        }
        return tracked_markets
    
    def _polymarket_crawl_live_markets(self) -> Tuple[Dict[str, Any], List[str]]:
        """Crawl from the cursor given to the end of the markets tab, accumulating all active markets."""
        markets = {}
        cursors_collected = []

        # Start from a default or from a point close to last known
        if not self.cursors: 
            current_cursor = "MA=="
        else: 
            current_cursor = self.cursors[-5]  # start from the 5th to last cursor to catch changes

        while True: 
            data, nxt = self._polymarket_get_markets_page(current_cursor)
            if data:
                for market in data:
                    # Filter out inactive or closed or non-accepting
                    if market["active"] and not market["closed"] and market["accepting_orders"]:
                        condition_id = market["condition_id"]
                        markets[condition_id] = market
            
            if current_cursor not in self.cursors:
                cursors_collected.append(current_cursor)
            
            if not nxt or nxt == "LTE=":
                break
            current_cursor = nxt

        return markets, cursors_collected
    
    def _polymarket_get_markets_page(self, cursor: str):
        """
        Get a given page in the Polymarket markets.
        
        Returns:
            (data, nxt): 
                data - the list of markets
                nxt - the cursor to fetch the next page
        """
        client = ClobClient(POLYMARKET_HOST)
        response = client.get_markets(next_cursor=cursor)
        data = response.get("data", [])
        nxt = response.get("next_cursor", None)
        return data, nxt
    
    def get_market(self, condition_id: str):
        """Get details for a specific market"""
        return self.markets.get(condition_id)
    
    def update_config(self, param: str, new_config: str) -> str:
        """
        Update the config dictionary.
        If param is in ("tags","keywords"), treat as string list membership.
        Otherwise, attempt to parse a float for thresholds.
        """
        if param in ("tags", "keywords"):
            if param not in self.config:
                self.config[param] = []
                
            if new_config in self.config[param]:
                self.config[param].remove(new_config)
                return f"Removed '{new_config}' from {param}."
            else:
                self.config[param].append(new_config)
                return f"Added '{new_config}' to {param}."
        else:
            try:
                val = float(new_config)
                self.config[param] = val
                return f"New {param} is {val}."
            except ValueError:
                return f"Invalid numeric value: {new_config}"