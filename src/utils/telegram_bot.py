import time
import logging
import json
import os
from flask import Flask, request, jsonify
from typing import Dict, Any
from telegram import Bot, Update
from telegram.ext import (
    Dispatcher, CommandHandler, CallbackContext, 
    MessageHandler, Filters, Updater
)
from telegram.error import TimedOut, NetworkError
from utils.generic import *

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Toggle between polling mode and webhook mode
USE_POLLING = os.getenv("TELEGRAM_USE_POLLING", "true").lower() == "true"

class TelegramBot:
    """Telegram bot for Polymarket notifications using webhooks or polling"""

    def __init__(self, bot_token: str, chat_id: str, webhook_url: str = None):
        """Initialize the Telegram bot"""
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot = Bot(token=bot_token)
        self.webhook_url = webhook_url
        self.updater = None  # Ensure updater is defined

        # Register command handlers
        if USE_POLLING:
            self.start_polling()
        else:
            self.set_webhook()

    def start_polling(self):
        """Start polling mode for Telegram updates"""
        logger.info("Starting bot in polling mode...")
        
        # Create an Updater instance (which handles the Dispatcher)
        self.updater = Updater(token=self.bot_token, use_context=True)
        self.dispatcher = self.updater.dispatcher

        # Register handlers
        self.register_handlers()

        # Start polling
        self.updater.start_polling()

    def set_webhook(self):
        """Set webhook mode for Telegram updates"""
        if not self.webhook_url:
            logger.error("Webhook URL not provided. Falling back to polling mode.")
            self.start_polling()
            return

        try:
            self.bot.delete_webhook()
            self.bot.set_webhook(url=f"{self.webhook_url}/{self.bot_token}")
            logger.info(f"Webhook set to {self.webhook_url}/{self.bot_token}")

            # Create a Dispatcher manually (needed for webhooks)
            self.dispatcher = Dispatcher(self.bot, update_queue=None, workers=4, use_context=True)
            self.register_handlers()
        except Exception as e:
            logger.error(f"Failed to set webhook: {str(e)}. Falling back to polling mode.")
            self.start_polling()

    def register_handlers(self):
        """Register all command handlers for the Telegram Bot."""
        logger.info("Setting up command handlers...")

        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("show_config", self.show_config_command))
        self.dispatcher.add_handler(CommandHandler("update_config", self.update_config_command))
        self.dispatcher.add_handler(CommandHandler("market", self.market_command))
        self.dispatcher.add_handler(CommandHandler("show_tracked_markets", self.show_tracked_markets_command))
        self.dispatcher.add_handler(MessageHandler(Filters.command, self.unknown_command))

    def process_update(self, update_json):
        """Process an update from Telegram webhook"""
        update = Update.de_json(update_json, self.bot)
        self.dispatcher.process_update(update)

    def safe_send_message(self, text, retries=3, delay=5):
        """Send a message with retry logic for network issues."""
        attempt = 0
        while attempt < retries:
            try:
                self.bot.send_message(chat_id=self.chat_id, text=text)
                return
            except (TimedOut, NetworkError) as e:
                attempt += 1
                logger.warning(f"Send message attempt {attempt} failed: {e}. Retrying in {delay} seconds...")
                time.sleep(delay)
        logger.error(f"Failed to send message after {retries} attempts.")

    # Command Handlers
    def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        help_text = (
            "Available Commands:\n"
            "/help - Show this help message\n"
            "/market <id> - Show details for a specific market\n"
            "/show_config - Show current Polymarket configuration\n"
            "/update_config <param> <value> - Update a config parameter\n"
            "/show_tracked_markets - List condition IDs of tracked markets\n"
        )
        self.safe_send_message(help_text)

    def show_config_command(self, update: Update, context: CallbackContext):
        """Handle /show_config command"""
        pass  # Handled by bot manager

    def update_config_command(self, update: Update, context: CallbackContext):
        """Handle /update_config command"""
        if not context.args or len(context.args) < 2:
            self.safe_send_message("Usage: /update_config <param> <value>")
            return
        pass  # Handled by bot manager

    def market_command(self, update: Update, context: CallbackContext):
        """Handle /market command"""
        if not context.args:
            self.safe_send_message("Usage: /market <condition_id>")
            return
        pass  # Handled by bot manager

    def show_tracked_markets_command(self, update: Update, context: CallbackContext):
        """Handle /show_tracked_markets command"""
        pass  # Handled by bot manager

    def unknown_command(self, update: Update, context: CallbackContext):
        """Handle unknown commands"""
        self.safe_send_message("Unknown command. Use /help to see available commands.")

    # Notification Methods
    def send_market_notification(self, market: dict, is_new: bool):
        """Send a notification about a new or closed market"""
        formatted_market = format_market(market)
        text = f"🆕 New Market Found!\n\n{formatted_market}" if is_new else f"🔒 Market Closed\n\n{formatted_market}"
        self.safe_send_message(text)

    def send_price_notification(self, alert: dict):
        """Send a notification about a significant price change"""
        text = format_price_alert(alert)
        self.safe_send_message(text)
