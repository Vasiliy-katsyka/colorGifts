import os
import requests
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from tonnelmp import getGifts as getTonnelGifts
from portalsmp import search as searchPortalsGifts
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# --- CONFIGURATION ---
TONNEL_AUTH_DATA = os.environ.get('TONNEL_AUTH_DATA')
PORTALS_AUTH_DATA = os.environ.get('PORTALS_AUTH_DATA')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
WEB_APP_URL = 'https://vasiliy-katsyka.github.io/colorGifts'
SERVER_URL = os.environ.get('SERVER_URL') 

# --- SINGLE BOT INSTANCE ---
# This ensures that no matter how many workers Gunicorn starts,
# they all reference the same bot object.
bot = telebot.TeleBot(BOT_TOKEN)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- INITIALIZE FLASK APP ---
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "https://vasiliy-katsyka.github.io"}})


# --- DATA CACHING & HELPERS (These are unchanged) ---
# ... (Keep the CACHED_DATA, load_initial_data, format_tonnel_gift, and format_portals_gift functions exactly as they were) ...
CACHED_DATA = {
    "collections": [], "backdrops": [],
    "colors": [
        {"name": "white"}, {"name": "black"}, {"name": "gray"}, {"name": "red"},
        {"name": "orange"}, {"name": "yellow"}, {"name": "green"}, {"name": "cyan"},
        {"name": "blue"}, {"name": "purple"}, {"name": "pink"}, {"name": "unknown"}
    ], "color_model_map": {}
}
def load_initial_data():
    logger.info("Loading initial data...")
    try:
        collections_url = "https://cdn.changes.tg/gifts/id-to-name.json"
        collections_res = requests.get(collections_url).json()
        CACHED_DATA["collections"] = [{"id": k, "name": v} for k, v in collections_res.items()]
        logger.info(f"Loaded {len(CACHED_DATA['collections'])} collections.")
        backdrops_url = "https://cdn.changes.tg/gifts/backdrops.json"
        CACHED_DATA["backdrops"] = requests.get(backdrops_url).json()
        logger.info(f"Loaded {len(CACHED_DATA['backdrops'])} backdrops.")
        color_repo_api = "https://api.github.com/repos/Vasiliy-katsyka/colorGifts/contents/"
        files = requests.get(color_repo_api).json()
        for file in files:
            if file['name'].endswith('.json'):
                gift_name = file['name'].replace('.json', '')
                content_url = file['download_url']
                models_data = requests.get(content_url).json()
                for model_name, data in models_data.items():
                    main_color = data.get("main_color", "unknown")
                    if main_color not in CACHED_DATA["color_model_map"]:
                        CACHED_DATA["color_model_map"][main_color] = []
                    CACHED_DATA["color_model_map"][main_color].append((gift_name, model_name))
        logger.info("Finished loading color model map.")
    except Exception as e: logger.error(f"Error loading initial data: {e}", exc_info=True)
def format_tonnel_gift(gift):
    gift_name_formatted = gift.get('name', '').lower().replace(' ', '')
    return { "id": f"tonnel_{gift.get('gift_id')}", "name": gift.get('name'), "model": gift.get('model', '').split(' (')[0], "price": gift.get('price', 0) * 1.1, "imageUrl": f"https://nft.fragment.com/gift/{gift_name_formatted}-{gift.get('gift_num')}.large.jpg", "buyUrl": f"https://market.tonnel.network/gift/{gift.get('gift_id')}", "source": "tonnel" }
def format_portals_gift(gift):
    model_attr = next((attr for attr in gift.get('attributes', []) if attr['type'] == 'model'), {'value': 'N/A'})
    return { "id": f"portals_{gift.get('id')}", "name": gift.get('name'), "model": model_attr.get('value'), "price": float(gift.get('price', 0)), "imageUrl": gift.get('photo_url'), "buyUrl": f"https://t.me/portals/market?startapp=gift_{gift.get('id')}", "source": "portals" }


# --- API ENDPOINTS (Unchanged) ---
@app.route('/api/filters', methods=['GET'])
def get_filters():
    return jsonify(CACHED_DATA)

@app.route('/api/search', methods=['GET'])
def search_gifts():
    logger.info(f"Received search request with args: {request.args}")
    args = request.args
    color = args.get('color')
    collections = args.get('collections', '').split(',') if args.get('collections') else []
    backdrops = args.get('backdrops', '').split(',') if args.get('backdrops') else []
    min_price = args.get('min_price', type=float)
    max_price = args.get('max_price', type=float)
    sort = args.get('sort', 'price_asc')
    
    if not color:
        logger.warning("Search request failed: Color parameter is required.")
        return jsonify({"error": "Color parameter is required"}), 400

    models_for_color = CACHED_DATA["color_model_map"].get(color, [])
    if not models_for_color:
        logger.info(f"No models found in cache for color '{color}'.")
        return jsonify([])

    logger.info(f"Found {len(models_for_color)} potential models for color '{color}'.")

    # Filter models by selected collections if any
    if collections:
        initial_model_count = len(models_for_color)
        models_for_color = [m for m in models_for_color if m[0] in collections]
        logger.info(f"Filtered models by collection. Count changed from {initial_model_count} to {len(models_for_color)}.")

    if not models_for_color:
        logger.info("No models left after filtering by collection. Returning empty list.")
        return jsonify([])

    all_gifts = []

    # --- Fetch from Portals ---
    try:
        gift_names_to_search = list(set([m[0] for m in models_for_color]))
        model_names_to_search = list(set([m[1] for m in models_for_color]))
        
        logger.info(f"Searching Portals with {len(gift_names_to_search)} collections and {len(model_names_to_search)} models.")
        
        portals_results = searchPortalsGifts(
            authData=PORTALS_AUTH_DATA,
            gift_name=gift_names_to_search,
            model=model_names_to_search,
            backdrop=backdrops if backdrops else "",
            limit=50
        )
        
        logger.info(f"Portals API returned {len(portals_results)} raw results.")
        
        # This part remains the same, formatting the valid results
        for gift in portals_results:
            model_attr = next((attr for attr in gift.get('attributes', []) if attr['type'] == 'model'), None)
            if model_attr and (gift['name'], model_attr['value']) in models_for_color:
                all_gifts.append(format_portals_gift(gift))

    except Exception as e:
        logger.error(f"Error fetching from Portals: {e}", exc_info=True)

    # --- Fetch from Tonnel ---
    try:
        collections_to_search = collections if collections else list(set([m[0] for m in models_for_color]))
        logger.info(f"Searching Tonnel with {len(collections_to_search)} collections.")
        
        for collection_name in collections_to_search:
            models_in_collection = [m[1] for m in models_for_color if m[0] == collection_name]
            if not models_in_collection: continue
            
            # Tonnel API might not support list of models, so we fetch and filter
            tonnel_results = getTonnelGifts(
                authData=TONNEL_AUTH_DATA,
                gift_name=collection_name,
                backdrop=backdrops[0] if len(backdrops) == 1 else '',
                limit=30
            )
            logger.info(f"Tonnel API returned {len(tonnel_results)} raw results for collection '{collection_name}'.")
            
            for gift in tonnel_results:
                model_name = gift.get('model', '').split(' (')[0]
                if model_name in models_in_collection:
                    all_gifts.append(format_tonnel_gift(gift))
    except Exception as e:
        logger.error(f"Error fetching from Tonnel: {e}", exc_info=True)

    logger.info(f"Total gifts from both marketplaces before final filtering: {len(all_gifts)}")

    # --- FINAL FILTERING AND SORTING (Unchanged) ---
    if min_price is not None: all_gifts = [g for g in all_gifts if g['price'] >= min_price]
    if max_price is not None: all_gifts = [g for g in all_gifts if g['price'] <= max_price]
    if sort == 'price_asc': all_gifts.sort(key=lambda x: x['price'])
    elif sort == 'price_desc': all_gifts.sort(key=lambda x: x['price'], reverse=True)
    
    logger.info(f"Returning {len(all_gifts)} gifts to the frontend.")
    return jsonify(all_gifts)


# --- TELEGRAM BOT WEBHOOK LOGIC (IMPROVED) ---
@app.route('/api/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        logger.info(f"Webhook received: {json_string}") # Add logging to see the data
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        logger.warning("Webhook received with incorrect content-type.")
        return 'Unsupported Media Type', 415


# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    logger.info(f"Processing /start command for chat_id: {message.chat.id}")
    try:
        markup = InlineKeyboardMarkup()
        web_app_button = InlineKeyboardButton(
            text="🎨 Open Gift Finder",
            web_app=WebAppInfo(url=WEB_APP_URL)
        )
        markup.add(web_app_button)
        bot.send_message(
            message.chat.id,
            "Welcome! Click the button below to find gifts by color, collection, and more!",
            reply_markup=markup
        )
        logger.info(f"Successfully sent /start reply to {message.chat.id}")
    except Exception as e:
        logger.error(f"Failed to send /start reply to {message.chat.id}: {e}", exc_info=True)


# --- STARTUP LOGIC ---
# This part runs only ONCE when Gunicorn starts the application.
if __name__ != '__main__':
    # Load data once
    load_initial_data()
    # Set webhook
    if SERVER_URL and BOT_TOKEN:
        webhook_url = f"{SERVER_URL}/api/{BOT_TOKEN}"
        logger.info("Removing old webhook...")
        bot.remove_webhook()
        logger.info(f"Setting new webhook to: {webhook_url}")
        # The timeout is important for Render's free tier, which can be slow to start
        success = bot.set_webhook(url=webhook_url, timeout=20) 
        if success:
            logger.info("Webhook set successfully!")
        else:
            logger.error("Webhook set failed.")
    else:
        logger.error("SERVER_URL or BOT_TOKEN environment variable not set. Webhook not set.")

# This part is for running locally for testing
if __name__ == '__main__':
    logger.info("Starting Flask dev server for local testing...")
    # For local testing, you might want to switch back to polling
    bot.remove_webhook()
    bot.polling(non_stop=True)
