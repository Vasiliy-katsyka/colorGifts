import os
import requests
import threading
from flask import Flask, jsonify, request
from flask_cors import CORS
from tonnelmp import getGifts as getTonnelGifts
from portalsmp import search as searchPortalsGifts
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# --- CONFIGURATION ---
# It's highly recommended to set these as environment variables for security
TONNEL_AUTH_DATA = os.environ.get('TONNEL_AUTH_DATA', 'Your_Tonnel_Auth_Data_Here')
PORTALS_AUTH_DATA = os.environ.get('PORTALS_AUTH_DATA', 'Your_Portals_Auth_Data_Here')
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'Your_Telegram_Bot_Token_Here')
WEB_APP_URL = 'https://vasiliy-katsyka.github.io/colorGifts' # Your GitHub Pages URL

# --- INITIALIZE APP & BOT ---
app = Flask(__name__)
# Configure CORS to allow requests only from your GitHub Pages domain
CORS(app, resources={r"/api/*": {"origins": "https://vasiliy-katsyka.github.io"}})
bot = telebot.TeleBot(BOT_TOKEN)

# --- DATA CACHING ---
# This dictionary will store data fetched at startup to avoid re-fetching on every request
CACHED_DATA = {
    "collections": [],
    "backdrops": [],
    "colors": [
        {"name": "white"}, {"name": "black"}, {"name": "gray"}, {"name": "red"},
        {"name": "orange"}, {"name": "yellow"}, {"name": "green"}, {"name": "cyan"},
        {"name": "blue"}, {"name": "purple"}, {"name": "pink"}, {"name": "unknown"}
    ],
    "color_model_map": {}
}

def load_initial_data():
    print("Loading initial data...")
    try:
        # 1. Load Collections
        collections_url = "https://cdn.changes.tg/gifts/id-to-name.json"
        collections_res = requests.get(collections_url).json()
        CACHED_DATA["collections"] = [{"id": k, "name": v} for k, v in collections_res.items()]
        print(f"Loaded {len(CACHED_DATA['collections'])} collections.")

        # 2. Load Backdrops
        backdrops_url = "https://cdn.changes.tg/gifts/backdrops.json"
        CACHED_DATA["backdrops"] = requests.get(backdrops_url).json()
        print(f"Loaded {len(CACHED_DATA['backdrops'])} backdrops.")

        # 3. Load Color Data for models
        color_repo_api = "https://api.github.com/repos/Vasiliy-katsyka/colorGifts/contents/"
        files = requests.get(color_repo_api).json()
        color_map = {}
        for file in files:
            if file['name'].endswith('.json'):
                gift_name = file['name'].replace('.json', '')
                content_url = file['download_url']
                models_data = requests.get(content_url).json()
                color_map[gift_name] = {}
                for model_name, data in models_data.items():
                    main_color = data.get("main_color", "unknown")
                    # Group by color
                    if main_color not in CACHED_DATA["color_model_map"]:
                        CACHED_DATA["color_model_map"][main_color] = []
                    # Store as a tuple (collection_name, model_name)
                    CACHED_DATA["color_model_map"][main_color].append((gift_name, model_name))
        print("Finished loading color model map.")

    except Exception as e:
        print(f"Error loading initial data: {e}")

# --- HELPER FUNCTIONS ---
def format_tonnel_gift(gift):
    """Converts a Tonnel gift dict to a standardized format."""
    gift_name_formatted = gift.get('name', '').lower().replace(' ', '')
    return {
        "id": f"tonnel_{gift.get('gift_id')}",
        "name": gift.get('name'),
        "model": gift.get('model', '').split(' (')[0], # Remove rarity part
        "price": gift.get('price', 0) * 1.1, # Add 10% fee back
        "imageUrl": f"https://nft.fragment.com/gift/{gift_name_formatted}-{gift.get('gift_num')}.large.jpg",
        "buyUrl": f"https://market.tonnel.network/gift/{gift.get('gift_id')}",
        "source": "tonnel"
    }

def format_portals_gift(gift):
    """Converts a Portals gift dict to a standardized format."""
    model_attr = next((attr for attr in gift.get('attributes', []) if attr['type'] == 'model'), {'value': 'N/A'})
    return {
        "id": f"portals_{gift.get('id')}",
        "name": gift.get('name'),
        "model": model_attr.get('value'),
        "price": float(gift.get('price', 0)),
        "imageUrl": gift.get('photo_url'),
        "buyUrl": f"https://t.me/portals/market?startapp=gift_{gift.get('id')}",
        "source": "portals"
    }

# --- API ENDPOINTS ---
@app.route('/api/filters', methods=['GET'])
def get_filters():
    return jsonify(CACHED_DATA)

@app.route('/api/search', methods=['GET'])
def search_gifts():
    args = request.args
    color = args.get('color')
    collections = args.get('collections', '').split(',') if args.get('collections') else []
    backdrops = args.get('backdrops', '').split(',') if args.get('backdrops') else []
    min_price = args.get('min_price', type=float)
    max_price = args.get('max_price', type=float)
    sort = args.get('sort', 'price_asc')

    if not color:
        return jsonify({"error": "Color parameter is required"}), 400

    models_for_color = CACHED_DATA["color_model_map"].get(color, [])
    if not models_for_color:
        return jsonify([])

    # Filter models by selected collections if any
    if collections:
        models_for_color = [m for m in models_for_color if m[0] in collections]

    all_gifts = []

    # Fetch from Portals
    try:
        # Portals API is flexible with list of models and gift_names
        gift_names_to_search = list(set([m[0] for m in models_for_color]))
        model_names_to_search = list(set([m[1] for m in models_for_color]))

        portals_results = searchPortalsGifts(
            authData=PORTALS_AUTH_DATA,
            gift_name=gift_names_to_search,
            model=model_names_to_search,
            backdrop=backdrops if backdrops else "",
            limit=50 # Fetch more to filter locally
        )
        for gift in portals_results:
            # Final check to ensure the model's color is the one we want
            model_attr = next((attr for attr in gift.get('attributes', []) if attr['type'] == 'model'), None)
            if model_attr and (gift['name'], model_attr['value']) in models_for_color:
                 all_gifts.append(format_portals_gift(gift))
        print(f"Found {len(portals_results)} gifts from Portals.")

    except Exception as e:
        print(f"Error fetching from Portals: {e}")

    # Fetch from Tonnel (API is less flexible, so we fetch by collection and filter)
    try:
        collections_to_search = collections if collections else list(set([m[0] for m in models_for_color]))
        for collection_name in collections_to_search:
            models_in_collection = [m[1] for m in models_for_color if m[0] == collection_name]
            if not models_in_collection:
                continue
            
            # Tonnel API might not support list of models, so we fetch and filter
            tonnel_results = getTonnelGifts(
                authData=TONNEL_AUTH_DATA,
                gift_name=collection_name,
                backdrop=backdrops[0] if len(backdrops) == 1 else '', # Tonnel supports single backdrop
                limit=30
            )
            for gift in tonnel_results:
                model_name = gift.get('model', '').split(' (')[0]
                if model_name in models_in_collection:
                    all_gifts.append(format_tonnel_gift(gift))
        print(f"Found gifts from Tonnel, total now {len(all_gifts)}.")

    except Exception as e:
        print(f"Error fetching from Tonnel: {e}")

    # --- FINAL FILTERING AND SORTING ---
    # Price filtering
    if min_price is not None:
        all_gifts = [g for g in all_gifts if g['price'] >= min_price]
    if max_price is not None:
        all_gifts = [g for g in all_gifts if g['price'] <= max_price]

    # Sorting
    if sort == 'price_asc':
        all_gifts.sort(key=lambda x: x['price'])
    elif sort == 'price_desc':
        all_gifts.sort(key=lambda x: x['price'], reverse=True)
    # Rarity and latest sorting would require more complex logic and data not always available.
    # We will stick to price sorting for now as requested.

    return jsonify(all_gifts)


# --- TELEGRAM BOT LOGIC ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
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

def run_bot():
    print("Starting Telegram Bot Polling...")
    bot.polling(non_stop=True)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    load_initial_data()
    # Run bot in a separate thread
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()
    # Run Flask app
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
