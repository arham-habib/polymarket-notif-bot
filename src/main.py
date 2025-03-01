import os
import sys
import argparse
import dotenv

from utils.bot_manager import BotManager

def main():
    parser = argparse.ArgumentParser(description='Polymarket Notification Bot (Refactored)')
    parser.add_argument('--1m', type=float, help='Alert threshold for 1 minute interval')
    parser.add_argument('--5m', type=float, help='Alert threshold for 5 minute interval')
    parser.add_argument('--30m', type=float, help='Alert threshold for 30 minute interval')
    parser.add_argument('--1h', type=float, help='Alert threshold for 1 hour interval')
    parser.add_argument('--6h', type=float, help='Alert threshold for 6 hour interval')
    parser.add_argument('--1d', type=float, help='Alert threshold for 1 day interval')
    
    parser.add_argument('--tags', nargs='+', help='List of tags to filter markets by')
    parser.add_argument('--keywords', nargs='+', help='List of keywords to filter markets by')

    args = parser.parse_args()

    # Build the config dictionary
    config = {}
    if args.__dict__.get('1m') is not None:
        config['1m'] = args.__dict__['1m']
    if args.__dict__.get('5m') is not None:
        config['5m'] = args.__dict__['5m']
    if args.__dict__.get('30m') is not None:
        config['30m'] = args.__dict__['30m']
    if args.__dict__.get('1h') is not None:
        config['1h'] = args.__dict__['1h']
    if args.__dict__.get('6h') is not None:
        config['6h'] = args.__dict__['6h']
    if args.__dict__.get('1d') is not None:
        config['1d'] = args.__dict__['1d']

    # Include tags and keywords filters
    config['tags'] = args.tags if args.tags else []
    config['keywords'] = args.keywords if args.keywords else []

    print("Starting Polymarket Notification Bot with the following config:")
    print(config)

    dotenv.load_dotenv()
    
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://localhost:8443")
    
    # Check if polling mode should be used
    USE_POLLING = os.getenv("TELEGRAM_USE_POLLING", "true").lower() == "true"

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        sys.exit(1)

    # Initialize BotManager with shared PolymarketDataService
    manager = BotManager(webhook_base_url=WEBHOOK_BASE_URL, config=config)

    # Register Telegram bot
    manager.register_bot(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

    # Start scheduler for periodic market updates
    manager.run_scheduler()

    if USE_POLLING:
        print("Running in polling mode...")
        while True:
            pass  # Polling mode will be handled inside the bot
    else:
        print(f"Running in webhook mode on {WEBHOOK_BASE_URL}...")
        manager.app.run(host="0.0.0.0", port=8443)

if __name__ == "__main__":
    main()
