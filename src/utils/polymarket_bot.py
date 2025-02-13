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
        
        # Go through the newly parsed markets. Check if not in object state, or if the status has now closed
        for condition_id, market in markets.items():
            if condition_id not in self.markets:
                new_markets[condition_id] = market
            if condition_id in self.markets and (market["closed"] != self.markets[condition_id]["closed"]):
                closed_markets[condition_id] = market
                
        # Check if we are tracking any new markets
        tracked_new_markets = self._get_tracked_markets(new_markets)
        for condition_id, market in tracked_new_markets.items():
            self.markets[condition_id] = market

        logger.info(f"{len(tracked_new_markets)} new tracked markets, {len(closed_markets)} closed tracked markets")
        logger.info(f"Last 5 pages scanned: {self.cursors[-5:]}")
        logger.info(f"{len(self.markets)} markets in memory")
        self._send_market_notification(tracked_new_markets, new=True)
        self._send_market_notification(closed_markets, new=False)

        # TODO: More robust logic for tracking market closes. Currently only catches closes if on the most recent page


    def check_markets(self):
        """Check price history of tracked markets"""
        current_ts = int(datetime.now().timestamp())
        interval_map = {
            '1m': 60,
            '5m': 300, 
            '30m': 1800,
            '1h': 3600,
            '6h': 21600,
            '1d': 86400
        }
        market_histories = {}
        
        for condition_id, market in tqdm(self.markets.items(), desc="Checking market histories", unit="market"):
            token1 = market["tokens"][0]["token_id"]
            token2 = market["tokens"][0]["token_id"]
            
            # Use last notification timestamp if exists, otherwise look back 1 day
            start_ts = market.get("last_notification", current_ts - interval_map['1d'])
            history = self._get_price_history(token1, start_ts, current_ts)
            if history:
                market_histories[condition_id] = history

        # Check each configured interval using the cached history
        for interval, threshold in self.config.items():
            if interval not in interval_map:
                continue 
            interval_start = current_ts - interval_map[interval]
            
            # Track largest price change for this interval
            max_price_change = 0
            max_change_market = None
            max_change_data = None
            
            for condition_id, history in market_histories.items():
                interval_data = [
                    price['p'] for price in history['history']
                    if interval_start <= price['t'] <= current_ts
                ]
                if not interval_data:
                    continue
                    
                # Check if price difference exceeds threshold
                price_diff = max(interval_data) - min(interval_data)
                
                # Track largest price change
                if price_diff > max_price_change:
                    max_price_change = price_diff
                    max_change_market = self.markets[condition_id]
                    max_change_data = interval_data
                
                if price_diff >= threshold:
                    market = self.markets[condition_id]
                    logger.info(f"Price change recorded for market {condition_id} over {interval}")
                    msg = f"⚠️ Price Change Alert!\n\n"
                    msg += f"Market: {market['question']}\n"
                    msg += f"Price changed by {price_diff:.3f} in last {interval}\n"
                    msg += f"Range: {min(interval_data):.3f} - {max(interval_data):.3f}"
                    self._safe_send_message(self.bot, self.chat_id, msg)
                    
                    # Update last notification timestamp
                    self.markets[condition_id]["last_notification"] = current_ts
            
            # Log the largest price change for this interval
            if max_change_market:
                logger.info(
                    f"Largest {interval} price change: {max_price_change:.3f} "
                    f"for market: {max_change_market['question']} "
                    f"(Range: {min(max_change_data):.3f} - {max(max_change_data):.3f})"
                )


    def _get_tracked_markets(self, markets: dict):
        """Get the tracked markets based on the config"""
        tracked_markets = {}
        filter_tags = self.config.get("tags", [])
        filter_keywords = self.config.get("keywords", [])

        for condition_id, market in markets.items():
            market_tags = market.get("tags") or []
            market_question = market.get("question", "").lower()

            if any(tag in market_tags for tag in filter_tags):
                tracked_markets[condition_id] = market
                continue

            if any(keyword.lower() in market_question for keyword in filter_keywords):
                tracked_markets[condition_id] = market
                continue

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
    

    def _get_price_history(self, token_id: str, start_ts: int, end_ts: int) -> dict:
        """Get price history for a market between timestamps"""
        try:
            response = requests.get(
                f"{POLYMARKET_GAMMA_HOST}/prices-history",
                params={
                    "market": token_id,
                    "startTs": start_ts, 
                    "endTs": end_ts,
                    "fidelity": 1
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
    
    return f"Condition ID: {condition_id}\nQuestion: {question}\Tokens: ${formatted_price}\nTags: {tags}"
