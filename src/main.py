import json
import os
import argparse
import sys
import dotenv

from utils.polymarket_bot import PolymarketNotifBot

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Polymarket Notification Bot')
    parser.add_argument('--1m', type=float, help='1 minute interval')
    parser.add_argument('--5m', type=float, help='5 minute interval') 
    parser.add_argument('--30m', type=float, help='30 minute interval')
    parser.add_argument('--1h', type=float, help='1 hour interval')
    parser.add_argument('--6h', type=float, help='6 hour interval')
    parser.add_argument('--1d', type=float, help='1 day interval')
    parser.add_argument('--1w', type=float, help='1 week interval')
    parser.add_argument('--tags', nargs='+', help='List of tags to filter by')
    parser.add_argument('--keywords', nargs='+', help='List of keywords to filter by')

    args = parser.parse_args()

    print("Loading environment variables...")
    dotenv.load_dotenv()
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: Missing Telegram bot token or chat ID in environment variables.", file=sys.stderr)
        sys.exit(1)

    print("Creating bot instance...")
    
    # Convert args to dict and pass to bot
    config = {
        **{k: v for k, v in vars(args).items() if k in ['1m','5m','30m','1h','6h','1d','1w'] and v is not None},
        'tags': args.tags if args.tags else [],
        'keywords': args.keywords if args.keywords else []
    }
    
    bot = PolymarketNotifBot(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, config)
    bot.start()