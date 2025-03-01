
def format_market(market: dict) -> str:
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

def format_price_alert(alert: dict) -> str:
    """Format price alert into a readable message string."""
    market = alert["market"]
    condition_id = alert["condition_id"]
    interval = alert["interval"]
    yes_data = alert["yes_data"]
    no_data = alert["no_data"]
    
    msg = f"⚠️ Price Change Alert ({interval} interval):\n"
    msg += f"Market: {market['question']}\n"
    msg += f"Condition ID: {condition_id}\n"
    msg += f"\nYES Token:\n"
    msg += f"  Max: {yes_data['max_price']:.3f} at {yes_data['max_time'].strftime('%H:%M:%S')}\n"
    msg += f"  Min: {yes_data['min_price']:.3f} at {yes_data['min_time'].strftime('%H:%M:%S')}\n"
    msg += f"  Change: {'+' if yes_data['price_change'] > 0 else ''}{yes_data['price_change']:.3f}\n"
    msg += f"\nNO Token:\n"
    msg += f"  Max: {no_data['max_price']:.3f} at {no_data['max_time'].strftime('%H:%M:%S')}\n"
    msg += f"  Min: {no_data['min_price']:.3f} at {no_data['min_time'].strftime('%H:%M:%S')}\n"
    msg += f"  Change: {'+' if no_data['price_change'] > 0 else ''}{no_data['price_change']:.3f}\n"
    
    return msg