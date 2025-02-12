import json
import os
import time
import schedule
import requests
import logging
from tqdm import tqdm, trange
from datetime import datetime

from py_clob_client.client import ClobClient
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.error import TimedOut, NetworkError

#############################################################
# Polymarket is indexed on a "condition_id"
POLYMARKET_FILEPATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "polymarket")
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_GAMMA_HOST = "https://gamma-api.polymarket.com"
#############################################################

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
            schedule.every(2).minutes.do(self.check_markets)

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
        new_markets = {}
        closed_markets = {}
        self.cursors += new_cursors
        logger.info(f"{len(markets)} live markets parsed")
        
        new_markets = {
            condition_id: market for condition_id, market in markets.items() 
            if condition_id not in self.markets
        }

        closed_markets = {
            condition_id: market for condition_id, market in markets.items()
            if condition_id in self.markets and market["closed"] != self.markets[condition_id]["closed"]
        }
        
        tracked_new_markets = self._get_tracked_markets(new_markets)
        self.markets.update(tracked_new_markets)

        logger.info(f"{len(tracked_new_markets)} new tracked markets, {len(closed_markets)} closed tracked markets")
        logger.info(f"Last 5 pages scanned: {self.cursors[-5:]}")
        logger.info(f"{len(self.markets)} markets in memory")

        self._send_market_notification(tracked_new_markets, new=True)
        self._send_market_notification(closed_markets, new=False)

        # TODO: More robust logic for tracking market closes. Currently only catches closes if on the most recent page


    def check_markets(self):
        """Check price history of tracked markets"""
        current_ts = int(datetime.now().timestamp())
        
        # keeping the for loop for now to get TQDM support (unsure if thats necessary)
        for condition_id, market in tqdm(self.markets.items(), desc="Checking market histories", unit="market"):
            token1 = market["tokens"][0]
            token2 = market["tokens"][1]

            yes_token = token1["token_id"] if token1["outcome"] == "Yes" else token2["token_id"]
            no_token = token1["token_id"] if token1["outcome"] == "No" else token2["token_id"]
            
            # Use last notification timestamp if exists, otherwise look back 1 day
            last_notification_timestamp = market.get("last_notification", current_ts - self.INTERVAL_MAP['1d'])
            last_checked_timestamp = market.get("price_history", {}).get("yes_history", {}).get("history", [{}])[-1].get("t", 0)
            start_ts = max(last_notification_timestamp, last_checked_timestamp, current_ts-self.INTERVAL_MAP["1d"])

            yes_history = self._get_price_history(yes_token, start_ts, current_ts)
            no_history = self._get_price_history(no_token, start_ts, current_ts)

            if yes_history and no_history:
                self.markets[condition_id]["price_history"] = {"yes_history": yes_history, "no_history": no_history}

            # TODO: add logic to figure out when a market is closed
            self._market_price_changes()
        

    def _market_price_changes(self, market_histories: dict):
        """Figure out if any of the markets have changed in excess of defined thresholds"""
        current_ts = int(datetime.now().timestamp())

        for interval, threshold in self.config.items():
            if interval not in self.INTERVAL_MAP:
                continue 
            interval_start = current_ts - self.INTERVAL_MAP[interval]
            
            max_price_change = 0
            max_change_market = None
            max_change_data = None
            
            for condition_id, market in self.markets.items():
                try:
                    interval_start_market = max(interval_start, market.get("last_notification", 0))
                    yes_interval_data = [
                        price['p'] for price in market["price_history"]["yes_history"]
                        if interval_start_market <= price['t'] <= current_ts
                    ]
                    no_interval_data = [
                        price['p'] for price in market["price_history"]["yes_history"]
                        if interval_start_market <= price['t'] <= current_ts
                    ]
                    if not yes_interval_data and no_interval_data:
                        continue
                        
                    price_diff = min(max(yes_interval_data) - min(yes_interval_data), max(no_interval_data) - min(no_interval_data))  # make sure the diff happened in both markets
                    if price_diff > max_price_change:
                        max_price_change = price_diff
                        max_change_market = self.markets[condition_id]
                        max_change_data = yes_interval_data
                    
                    if price_diff >= threshold:
                        self._send_price_notification(market, condition_id, yes_interval_data, no_interval_data, interval_start_market, interval)
                        self.markets[condition_id]["last_notification"] = current_ts

                except Exception as e: 
                    logger.error(e, " on ", polymarket_format_market(market))
            
            # Log the largest price change for this interval
            if max_change_data and max_change_market:
                logger.info(
                    f"Largest {interval} price change: {max_price_change:.3f} "
                    f"for market: {polymarket_format_market(max_change_market)} "
                    f"(Range: {min(max_change_data):.3f} - {max(max_change_data):.3f})"
                )
            else:
                logger.info(f"No price changes found for interval {interval}")


    def _get_tracked_markets(self, markets: dict):
        """Get the tracked markets based on the config"""
        tracked_markets = {}
        filter_tags = self.config.get("tags", [])
        filter_keywords = self.config.get("keywords", [])

        tracked_markets = {
            condition_id: market for condition_id, market in markets.items()
            if any(tag in (market.get("tags") or []) for tag in filter_tags) or
            any(keyword.lower() in market.get("question", "").lower() for keyword in filter_keywords)
        }

        return tracked_markets


    def _polymarket_crawl_live_markets(self) -> tuple[dict, list[str]]:
        """Crawl from the cursor given to the end of the markets tab"""
        markets = {}
        cursors_collected = []

        if not self.cursors: 
            current_cursor = "MA=="
        else: 
            current_cursor = self.cursors[-1]

        while True: 
            data, nxt = _polymarket_get_markets_page(current_cursor)
            if data: 
                for market in data: 
                    if market["active"] and not market["closed"] and market["accepting_orders"]:
                        condition_id = market["condition_id"]
                        markets[condition_id] = market
            if current_cursor not in self.cursors:
                cursors_collected.append(current_cursor)
            if not nxt or nxt=="LTE=":
                break
            current_cursor = nxt

        return markets, cursors_collected
    

    def _get_price_history(self, token_id: str, start_ts: int, end_ts: int, fidelity: int = 5) -> dict:
        """Get price history for a market between timestamps"""
        try:
            response = requests.get(
                f"{POLYMARKET_HOST}/prices-history",
                params={
                    "market": token_id,
                    "startTs": start_ts, 
                    "endTs": end_ts,
                    "fidelity": fidelity
                }
            )
            if response.status_code == 200:
                return response.json()
        except (requests.RequestException, ValueError) as e:
            print(f"Error fetching price history for {token_id}: {str(e)}")
        return None
    

    def _send_market_notification(self, changed_markets: dict, new: bool):
        """Send notifications of new or closed markets, new is a boolean for which notif to send"""
        for condition_id, market in changed_markets.items():
            logger.info(f"Market {'added' if new else 'closed'}: {condition_id}")
            formatted_market = polymarket_format_market(market)
            if new:
                text = f"🆕 New Market Found!\n\n{formatted_market}"
            else:
                text = f"🔒 Market Closed\n\n{formatted_market}"

            self._safe_send_message(self.bot, self.chat_id, text)

    
    def _send_price_notification(self, market: dict, condition_id: str, yes_interval_data: list[int], no_interval_data: list[int], interval_start_market: int, interval: int):
        """Sent a notification about the changed market"""
        logger.info(f"Price change recorded for market {condition_id} over {interval}")

        # Find indices of max and min prices for YES token
        yes_idx_max = yes_interval_data.index(max(yes_interval_data))
        yes_idx_min = yes_interval_data.index(min(yes_interval_data))
        no_idx_max = no_interval_data.index(max(no_interval_data))
        no_idx_min = no_interval_data.index(min(no_interval_data))

        # Convert timestamps to datetime for YES token extremes
        yes_max_time = datetime.fromtimestamp(interval_start_market + (yes_idx_max * interval))
        yes_min_time = datetime.fromtimestamp(interval_start_market + (yes_idx_min * interval))
        no_max_time = datetime.fromtimestamp(interval_start_market + (no_idx_max * interval))
        no_min_time = datetime.fromtimestamp(interval_start_market + (no_idx_min * interval))

        # Calculate price changes based on most recent value compared to earlier value
        yes_latest = yes_interval_data[-1]
        yes_earliest = yes_interval_data[0]
        yes_price_change = yes_latest - yes_earliest

        no_latest = no_interval_data[-1]
        no_earliest = no_interval_data[0]
        no_price_change = no_latest - no_earliest

        msg = f"⚠️ Price Change Alert over {interval} seconds:\n\n"
        msg += f"Market: {market['question']}\n"
        msg += f"Condition ID: {market['condition_id']}\n"
        msg += f"\nYES Token:\n"
        msg += f"Max: {max(yes_interval_data):.3f} at {yes_max_time.strftime('%H:%M:%S')}\n"
        msg += f"Min: {min(yes_interval_data):.3f} at {yes_min_time.strftime('%H:%M:%S')}\n"
        msg += f"Change: {'+' if yes_price_change > 0 else ''}{yes_price_change:.3f}\n"
        msg += f"\nNO Token:\n"
        msg += f"Max: {max(no_interval_data):.3f} at {no_max_time.strftime('%H:%M:%S')}\n" 
        msg += f"Min: {min(no_interval_data):.3f} at {no_min_time.strftime('%H:%M:%S')}\n"
        msg += f"Change: {'+' if no_price_change > 0 else ''}{no_price_change:.3f}\n"

        self._safe_send_message(self.bot, self.chat_id, msg)
        

    def _update_config(self, param: str, new_config: str) -> str:
        """Update the config dictionary from a Telegram command."""
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


    def _safe_send_message(self, bot, chat_id, text, retries=3, delay=5):
        """
        Send a message with retry logic for network issues.
        Use the official bot.send_message method (not bot._send_message).
        """
        attempt = 0
        while attempt < retries:
            try:
                bot.send_message(chat_id=chat_id, text=text)
                return  # Success, exit after sending
            except (TimedOut, NetworkError) as e:
                attempt += 1
                logger.warning(f"Send message attempt {attempt} failed: {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
        logger.error(f"Failed to send message after {retries} attempts.")


    def get_help(self) -> str:
        """Return help text listing all available commands."""
        return (
            "Available Commands:\n"
            "/help - Show this help message\n"
            "/market <id> - Show details for a specific market\n"
            "/show_config - Show current Polymarket configuration\n"
            "/update_config <param> <value> - Update a config parameter\n"
            # "/list_attributes <attr1,attr2,...> - List specified attributes for all markets\n"
            # "/show_attributes - Show all available market attributes\n"
            # "/volume <min> - List markets with volume above threshold\n"
            # "/spread <max> - List markets with bid-ask spread below threshold"
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
    Get a given page in the polymarket markets
    
    Params: 
        cursor (str): the cursor of the mkt page we are on
    Returns: 
        (data, nxt): the data associated with the market and the 
    """
    client = ClobClient(POLYMARKET_HOST)
    response = client.get_markets(next_cursor=cursor)
    data = response.get("data", [])
    nxt = response.get("next_cursor", None)
    return data, nxt 


def polymarket_format_market(market: dict) -> str:
    """Format market data into a readable message string."""
    question = market.get('question', 'N/A')
    price = market.get('tokens')
    formatted_price = ", ".join([f"{token['outcome']}: ${token['price']}" for token in price])
    tags = ', '.join(market.get('tags', []))
    condition_id = market["condition_id"]
    
    return f"Condition ID: {condition_id}\nQuestion: {question}\nTokens: {formatted_price}\nTags: {tags}"
