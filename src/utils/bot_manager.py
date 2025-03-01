import os
import logging
import json
import time
import schedule
import threading
from typing import Dict, Any
from flask import Flask, request, jsonify

from utils.polymarket_data import PolymarketDataService
from utils.telegram_bot import TelegramBot
from utils.generic import format_market, format_price_alert

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
)

logger = logging.getLogger(__name__)

class BotManager:
    """Manager for handling multiple bots and a single PolymarketDataService"""

    def __init__(self, webhook_base_url: str, config: dict):
        """Initialize BotManager with a single PolymarketDataService"""
        self.webhook_base_url = webhook_base_url
        self.bots = {}  # Stores bot instances
        self.polymarket_service = PolymarketDataService(config)  # Shared market data service

        # Flask app setup for webhooks
        self.app = Flask(__name__)
        self.setup_routes()
    
    def register_bot(self, bot_token: str, chat_id: str):
        """Register a bot and associate it with the shared data service"""
        if bot_token in self.bots:
            logger.warning(f"Bot with token {bot_token[:8]}... is already registered.")
            return

        bot = TelegramBot(bot_token, chat_id, self.webhook_base_url)
        self.bots[bot_token] = bot
        
        logger.info(f"Registered bot with token {bot_token[:8]}...")

    def handle_bot_command(self, bot_token: str, update: dict):
        """Handle bot commands and forward requests to PolymarketDataService"""
        bot = self.bots[bot_token]
        message = update['message']
        text = message.get('text', '')

        parts = text.split()
        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if command == '/show_config':
            config_str = json.dumps(self.polymarket_service.config, indent=2)
            bot.safe_send_message(f"Current configuration:\n```\n{config_str}\n```")

        elif command == '/update_config' and len(args) >= 2:
            param, value = args[0], args[1]
            response = self.polymarket_service.update_config(param, value)
            bot.safe_send_message(response)

        elif command == '/market' and len(args) >= 1:
            condition_id = args[0]
            market = self.polymarket_service.get_market(condition_id)
            bot.safe_send_message(format_market(market) if market else f"No tracked market found for {condition_id}.")

        elif command == '/show_tracked_markets':
            market_ids = ", ".join(self.polymarket_service.markets.keys())
            bot.safe_send_message(f"Tracked markets: {market_ids}" if market_ids else "No markets are currently tracked.")

    def run_load_markets(self):
        """Periodically update market data and notify bots of new markets"""
        logger.info("Running periodic market loading...")
        new_markets, closed_markets = self.polymarket_service.load_markets()
        
        for bot in self.bots.values():
            for market in new_markets.values():
                bot.send_market_notification(market, is_new=True)
            for market in closed_markets.values():
                bot.send_market_notification(market, is_new=False)

    def run_check_markets(self):
        """Periodically check market price changes and notify bots"""
        logger.info("Checking market price changes...")
        alerts = self.polymarket_service.check_markets()
        
        for bot in self.bots.values():
            for alert in alerts:
                bot.send_price_notification(alert)
        
    def setup_routes(self):
        """Setup Flask routes for handling webhooks"""
        @self.app.route('/<bot_token>', methods=['POST'])
        def webhook(bot_token):
            if bot_token not in self.bots:
                return jsonify({"status": "error", "message": "Bot not found"}), 404

            update = request.get_json()

            # Process the update in the bot
            self.bots[bot_token].process_update(update)

            # If it's a command, handle it through BotManager
            if 'message' in update and 'text' in update['message'] and update['message']['text'].startswith('/'):
                self.handle_bot_command(bot_token, update)

            return jsonify({"status": "success"})
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            """Health check endpoint"""
            return jsonify({
                "status": "ok",
                "bots_registered": len(self.bots),
                "tracked_markets": len(self.polymarket_service.markets)
            })
    
    def run_scheduler(self):
        """Run the scheduler in a separate thread"""
        def scheduler_loop():
            schedule.every(2).minutes.do(self.run_load_markets)  # Load new markets every 2 minutes
            schedule.every(1).minutes.do(self.run_check_markets)  # Check price changes every minute
            
            logger.info("Scheduler started, running periodic tasks...")
            while True:
                schedule.run_pending()
                time.sleep(1)  # Avoid CPU overutilization
        
        self.scheduler_thread = threading.Thread(target=scheduler_loop)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
