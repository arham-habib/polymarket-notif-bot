import json
import pandas as pd
import time
import schedule
import requests
import logging
from tqdm import tqdm, trange
from datetime import datetime
import asyncio
import aiohttp
import random  # for optional random sleeps
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple, Dict, Any

from py_clob_client.client import ClobClient
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.error import TimedOut, NetworkError

#############################################################
# Polymarket is indexed on a "condition_id"
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_GAMMA_HOST = "https://gamma-api.polymarket.com"
#############################################################

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Concurrency limit to avoid rate-limiting
MAX_CONCURRENT_REQUESTS = 5

class PolymarketNotifBot:

    INTERVAL_MAP = {
        '1m': 60,
        '5m': 300, 
        '30m': 1800,
        '1h': 3600,
        '6h': 21600,
        '1d': 86400
    }

    def __init__(
            self,
            bot_token: str,
            chat_id: str,
            config: dict,
            known_cursors: list = []
        ):
            logger.info("Initializing PolymarketNotifBot...")

            self.bot_token = bot_token
            self.chat_id = chat_id
            self.bot = Bot(token=bot_token)
            self.updater = Updater(token=bot_token)
            self.config = config
            self.dispatcher = self.updater.dispatcher
            self.cursors = known_cursors

            logger.info("Parsing existing markets...")
            self.init_markets()

            logger.info("Setting up scheduled tasks...")
            schedule.every(2).minutes.do(self.load_markets)
            schedule.every(1).minutes.do(self.check_markets)

            logger.info("Registering command handlers...")
            self.register_handlers()
            logger.info("Bot initialization complete.")


    def init_markets(self):
        """Initial load of markets when bot is first created"""
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

        self._send_market_notification(tracked_new_markets, new=True)
        self._send_market_notification(closed_markets, new=False)


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


    async def _fetch_market_history(self, session: aiohttp.ClientSession, 
                                    condition_id: str, market: Dict[str, Any]) -> None:
        """
        Fetch and update history for a single market - sequential token fetching.
        This method itself does 2 requests (one for "Yes" token, one for "No" token).
        """
        yes_token, no_token = self._get_token_ids(market)

        # Optional short random sleep to distribute requests
        await asyncio.sleep(random.uniform(0.05, 0.15))

        yes_history = await self._get_price_history_async(session, yes_token, "1d")
        no_history = await self._get_price_history_async(session, no_token, "1d")
        self._update_market_history(condition_id, yes_history, no_history)


    async def _check_markets_async(self) -> None:
        """Process multiple markets concurrently with a limited number of tasks."""
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)  # concurrency-limiting semaphore

        async with aiohttp.ClientSession() as session:
            # Create tasks for each market with concurrency-limiting
            tasks = []
            for condition_id, market in tqdm(list(self.markets.items()), 
                                             desc="Checking market histories", 
                                             unit="market"):
                tasks.append(self._fetch_market_history_with_semaphore(session, sem, condition_id, market))
            
            await asyncio.gather(*tasks)


    async def _fetch_market_history_with_semaphore(self, session, sem, condition_id, market):
        """
        A small wrapper to ensure that the concurrency-limiting semaphore is respected
        before calling `_fetch_market_history`.
        """
        async with sem:
            await self._fetch_market_history(session, condition_id, market)

    # -----------------------------------------------------------------------------
    # If you want to add an optional retry/backoff in `_get_price_history_async`
    # for rate-limit errors (HTTP 429), you could do something like this:
    # -----------------------------------------------------------------------------
    async def _get_price_history_async(
        self,
        session: aiohttp.ClientSession,
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

                async with session.get(f"{POLYMARKET_HOST}/prices-history", params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        df = pd.Series(
                            [d['p'] for d in data['history']], 
                            index=[d['t'] for d in data['history']]
                        )
                        return df
                    elif response.status == 429:
                        # Rate-limited: backoff and retry
                        logger.warning(f"Rate-limited on token {token_id}, attempt {attempt+1}")
                        await asyncio.sleep(base_backoff * 2**attempt)
                        attempt += 1
                    else:
                        logger.error(f"Failed to fetch price history ({response.status}) for {token_id}")
                        return None

            except aiohttp.ClientError as e:
                logger.error(f"Error fetching price history for {token_id}: {str(e)}")
                # Might optionally retry or just exit here
                return None

        # If we got here, we retried `retry_limit` times
        logger.error(f"Exhausted retries fetching price history for {token_id}.")
        return None

    def check_markets(self) -> None:
        """Main method to check price history of tracked markets (entry point for schedule)."""
        # We run the async method in a separate event loop or with asyncio.run
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._check_markets_async())
        loop.close()

        # After we have all updated data, let's check for changes
        self._market_price_changes()


    def _market_price_changes(self):
        """Figure out if any of the markets have changed in excess of defined thresholds"""
        current_ts = int(datetime.now().timestamp())
        for interval, threshold in self.config.items():
            if interval not in self.INTERVAL_MAP:
                continue 
            interval_start = current_ts - self.INTERVAL_MAP[interval]
            
            for condition_id, market in tqdm(self.markets.items(), 
                                             desc=f"Checking {interval} price changes", 
                                             unit="market"):
                self._get_price_change(condition_id, market, interval, interval_start, current_ts, threshold)


    def _get_price_change(self, condition_id: str, market: dict, interval: int, 
                          interval_start: int, current_ts: int, threshold: float):
        """Check whether a market's price fluctuations have exceeded the threshold."""
        if "price_history" not in market:
            return
        try:
            interval_start_market = max(interval_start, market.get("last_notification", 0))

            # If we recently notified (over a different lookback window), skip
            if (current_ts - interval_start_market) <= 10:
                logger.info(f"Already notified for market {condition_id}")
                return
            
            yes_interval_data = market["price_history"]["yes_history"].loc[interval_start_market:current_ts]
            no_interval_data = market["price_history"]["no_history"].loc[interval_start_market:current_ts]

            if yes_interval_data.empty or no_interval_data.empty:
                logger.debug(f"Missing yes/no data for {condition_id} in interval.")
                return

            # Ensure both tokens show enough data for the threshold check
            price_diff_yes = yes_interval_data.max() - yes_interval_data.min()
            price_diff_no = no_interval_data.max() - no_interval_data.min()

            # For an alert, require that BOTH tokens have sufficiently large moves?
            # Or whichever is bigger? Decide your logic.
            price_diff = max(price_diff_yes, price_diff_no)

            if price_diff >= threshold:
                self._send_price_notification(
                    market, condition_id, yes_interval_data, no_interval_data, interval_start_market, interval
                )
                self.markets[condition_id]["last_notification"] = current_ts

        except Exception as e: 
            logger.error(f"Error on {condition_id}: {str(e)}")
            return


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


    def _polymarket_crawl_live_markets(self) -> tuple[dict, list[str]]:
        """Crawl from the cursor given to the end of the markets tab, accumulating all active markets."""
        markets = {}
        cursors_collected = []

        # Start from a default or from a point close to last known
        if not self.cursors: 
            current_cursor = "MA=="
        else: 
            current_cursor = self.cursors[-5]  # start from the 5th to last cursor to catch changes

        while True: 
            data, nxt = _polymarket_get_markets_page(current_cursor)
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
    

    def _safe_send_message(self, bot, chat_id, text, retries=3, delay=5):
        """
        Send a message with retry logic for network issues.
        """
        attempt = 0
        while attempt < retries:
            try:
                bot.send_message(chat_id=chat_id, text=text)
                return
            except (TimedOut, NetworkError) as e:
                attempt += 1
                logger.warning(f"Send message attempt {attempt} failed: {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
        logger.error(f"Failed to send message after {retries} attempts.")


    def _send_market_notification(self, changed_markets: dict, new: bool):
        """Send notifications of new or closed markets."""
        for condition_id, market in changed_markets.items():
            logger.info(f"Market {'added' if new else 'closed'}: {condition_id}")
            formatted_market = polymarket_format_market(market)
            if new:
                text = f"🆕 New Market Found!\n\n{formatted_market}"
            else:
                text = f"🔒 Market Closed\n\n{formatted_market}"

            self._safe_send_message(self.bot, self.chat_id, text)


    def _send_price_notification(self, market: dict, condition_id: str, 
                                 yes_interval_data: pd.Series, no_interval_data: pd.Series, 
                                 interval_start_market: int, interval: int):
        """Send a notification about significant price change in a market."""
        logger.info(f"Price change recorded for market {condition_id} over {interval}")

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

        msg = f"⚠️ Price Change Alert ({interval} interval):\n"
        msg += f"Market: {market['question']}\n"
        msg += f"Condition ID: {market['condition_id']}\n"
        msg += f"\nYES Token:\n"
        msg += f"  Max: {yes_max_price:.3f} at {yes_max_time.strftime('%H:%M:%S')}\n"
        msg += f"  Min: {yes_min_price:.3f} at {yes_min_time.strftime('%H:%M:%S')}\n"
        msg += f"  Change: {'+' if yes_price_change > 0 else ''}{yes_price_change:.3f}\n"
        msg += f"\nNO Token:\n"
        msg += f"  Max: {no_max_price:.3f} at {no_max_time.strftime('%H:%M:%S')}\n"
        msg += f"  Min: {no_min_price:.3f} at {no_min_time.strftime('%H:%M:%S')}\n"
        msg += f"  Change: {'+' if no_price_change > 0 else ''}{no_price_change:.3f}\n"

        self._safe_send_message(self.bot, self.chat_id, msg)


    def _update_config(self, param: str, new_config: str) -> str:
        """
        Update the config dictionary from a Telegram command.
        If param is in ("tags","keywords"), treat as string list membership.
        Otherwise, attempt to parse a float for thresholds.
        """
        if param in ("tags", "keywords"):
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
            

    def register_handlers(self):
        """Register all command handlers for the Telegram Bot."""
        logger.info("Setting up command handlers...")

        # /help
        self.dispatcher.add_handler(
            CommandHandler("help", lambda update, context: self._safe_send_message(
                self.bot, self.chat_id, self.get_help()
            ))
        )

        # /show_config
        self.dispatcher.add_handler(
            CommandHandler("show_config", lambda update, context: self._safe_send_message(
                self.bot, self.chat_id, str(self.config)
            ))
        )

        # /update_config <param> <value>
        def update_config_cmd(update: Update, context: CallbackContext):
            if len(context.args) < 2:
                self._safe_send_message(self.bot, self.chat_id, "Usage: /update_config <param> <value>")
                return
            param, val = context.args[0], context.args[1]
            response = self._update_config(param, val)
            self._safe_send_message(self.bot, self.chat_id, response)

        self.dispatcher.add_handler(CommandHandler("update_config", update_config_cmd))

        # /market <condition_id>
        def show_market_cmd(update: Update, context: CallbackContext):
            if not context.args:
                self._safe_send_message(self.bot, self.chat_id, "Usage: /market <condition_id>")
                return
            cid = context.args[0]
            market = self.markets.get(cid)
            if market:
                self._safe_send_message(self.bot, self.chat_id, polymarket_format_market(market))
            else:
                self._safe_send_message(self.bot, self.chat_id, f"No tracked market found for {cid}.")

        self.dispatcher.add_handler(CommandHandler("market", show_market_cmd))

        # /show_tracked_markets
        self.dispatcher.add_handler(
            CommandHandler("show_tracked_markets", lambda update, context: self._safe_send_message(
                self.bot, self.chat_id, ", ".join(self.markets.keys())
            ))
        )


    def get_help(self) -> str:
        """Return help text listing all available commands."""
        return (
            "Available Commands:\n"
            "/help - Show this help message\n"
            "/market <id> - Show details for a specific market\n"
            "/show_config - Show current Polymarket configuration\n"
            "/update_config <param> <value> - Update a config parameter\n"
            "/show_tracked_markets - List condition IDs of tracked markets\n"
        )
    

    def start(self):
        """Start the bot and keep scheduling running."""
        logger.info("Starting the bot...")
        self.updater.start_polling()
        logger.info("Bot is now running.")

        while True:
            schedule.run_pending()
            time.sleep(1)


def _polymarket_get_markets_page(cursor: str):
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


def polymarket_format_market(market: dict) -> str:
    """Format market data into a readable message string."""
    question = market.get('question', 'N/A')
    token_data = market.get('tokens', [])
    formatted_price = ", ".join([f"{token['outcome']}: ${token.get('price', 'N/A')}" for token in token_data])
    tags = ', '.join(market.get('tags', []))
    condition_id = market["condition_id"]
    
    return (
        f"Condition ID: {condition_id}\n"
        f"Question: {question}\n"
        f"Tokens: {formatted_price}\n"
        f"Tags: {tags}"
    )


