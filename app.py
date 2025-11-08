import os
import requests
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from urllib.parse import quote

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'Your_Telegram_Bot_Token_Here')
WEB_APP_URL = 'https://vasiliy-katsyka.github.io/colorGifts'
SERVER_URL = os.environ.get('SERVER_URL')

# --- INITIALIZE ---
bot = telebot.TeleBot(BOT_TOKEN)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "https://vasiliy-katsyka.github.io"}})

# --- DATA CACHING ---
CACHED_DATA = {
    "collections": [],
    "backdrops": [], # Added backdrops
    "colors": [
        {"name": "white"}, {"name": "black"}, {"name": "gray"}, {"name": "red"},
        {"name": "orange"}, {"name": "yellow"}, {"name": "green"}, {"name": "cyan"},
        {"name": "blue"}, {"name": "purple"}, {"name": "pink"}, {"name": "unknown"}
    ],
    "color_model_map": {}
}

def load_initial_data():
    logger.info("Loading initial gift model data...")
    try:
        # 1. Load Collections
        collections_url = "https://cdn.changes.tg/gifts/id-to-name.json"
        collections_res = requests.get(collections_url).json()
        CACHED_DATA["collections"] = [{"id": k, "name": v} for k, v in collections_res.items()]
        logger.info(f"Loaded {len(CACHED_DATA['collections'])} collections.")

        # 2. Load Backdrops
        backdrops_url = "https://cdn.changes.tg/gifts/backdrops.json"
        CACHED_DATA["backdrops"] = requests.get(backdrops_url).json()
        logger.info(f"Loaded {len(CACHED_DATA['backdrops'])} backdrops.")

        # 3. Load Color Data for models
        color_repo_api = "https://api.github.com/repos/Vasiliy-katsyka/colorGifts/contents/"
        files = requests.get(color_repo_api).json()
        
        for file_info in files:
            if isinstance(file_info, dict) and file_info.get('name', '').endswith('.json'):
                gift_name = file_info['name'].replace('.json', '')
                content_url = file_info['download_url']
                
                try:
                    models_data = requests.get(content_url).json()
                    for model_name, model_data in models_data.items():
                        main_color = "unknown"
                        if isinstance(model_data, dict):
                            main_color = model_data.get("main_color", "unknown")
                        elif isinstance(model_data, str):
                            main_color = model_data
                        else:
                            continue
                        
                        if main_color not in CACHED_DATA["color_model_map"]:
                            CACHED_DATA["color_model_map"][main_color] = []
                        CACHED_DATA["color_model_map"][main_color].append((gift_name, model_name))
                except Exception:
                    # Supress errors for individual file processing to avoid crashing startup
                    pass
        
        logger.info("Finished loading color model map.")

    except Exception as e:
        logger.error(f"An unexpected error occurred during initial data load: {e}", exc_info=True)


# --- API ENDPOINTS ---

@app.route('/api/filters', methods=['GET'])
def get_filters():
    # Now returns collections, colors, AND backdrops
    return jsonify({
        "collections": CACHED_DATA["collections"],
        "colors": CACHED_DATA["colors"],
        "backdrops": CACHED_DATA["backdrops"]
    })

@app.route('/api/models', methods=['GET'])
def get_models():
    args = request.args
    color = args.get('color')
    collections = args.get('collections', '').split(',') if args.get('collections') else []

    if not color:
        return jsonify({"error": "Color parameter is required"}), 400

    models_for_color = CACHED_DATA["color_model_map"].get(color, [])
    
    if collections:
        filtered_models = [m for m in models_for_color if m[0] in collections]
    else:
        filtered_models = models_for_color
    
    response_data = []
    for collection_name, model_name in filtered_models:
        encoded_collection = quote(collection_name)
        encoded_model = quote(model_name)
        image_url = f"https://cdn.changes.tg/gifts/models/{encoded_collection}/png/{encoded_model}.png"
        
        response_data.append({
            "collection": collection_name,
            "model": model_name,
            "imageUrl": image_url
        })
    
    return jsonify(response_data)

# --- TELEGRAM BOT & WEBHOOK LOGIC (Unchanged) ---
@app.route('/api/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return '', 200
    return 'Unsupported Media Type', 415

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    web_app_button = InlineKeyboardButton(text="🎨 Open Gift Gallery", web_app=WebAppInfo(url=WEB_APP_URL))
    markup.add(web_app_button)
    bot.send_message(message.chat.id, "Welcome! Click the button below to browse a gallery of gift models by color, collection, and backdrop.", reply_markup=markup)

# --- STARTUP LOGIC ---
if __name__ != '__main__':
    load_initial_data()
    if SERVER_URL and BOT_TOKEN:
        webhook_url = f"{SERVER_URL}/api/{BOT_TOKEN}"
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url, timeout=20)
        logger.info(f"Webhook set to {webhook_url}")
