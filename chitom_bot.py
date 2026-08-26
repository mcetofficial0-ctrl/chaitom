import telebot
import os
import threading
import random
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont, ImageFile

# Позволяет Pillow работать с неполными JPG файлами
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Ключи и настройки
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Подключаем платный Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Настройки файлов для мемов
TEMPLATE_NAME = 'template.jpg'
FONT_NAME = 'arial.ttf'       
RESULT_NAME = 'meme_result.jpg'

# Обязательно замените на юзернейм вашего бота
BOT_USERNAME = '@chaitom_bot' 

# Хранилище истории чата
chat_history = []
HISTORY_LIMIT = 200

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твоя главная и нерушимая задача: в каждом, абсолютно в каждом своем ответе пользователю 
ты обязан использовать ровно три слова: "читом", "бастурма" и "клубок". 
Старайся вписывать их в контекст диалога так, чтобы это звучало забавно или органично.
ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши обычным текстом."""

# Инициализируем новейшую модель и передаем ей системные инструкции
model = genai.GenerativeModel('gemini-3.6-flash', system_instruction=SYSTEM_PROMPT)

def text_wrap(text, font, max_width):
    lines = []
    if font.getlength(text) <= max_width:
        lines.append(text)
    else:
        words = text.split(' ')
        i = 0
        while i < len(words):
            line = ''
            while i < len(words) and font.getlength(line + words[i]) <= max_width:
                line = line + words[i] + ' '
                i += 1
            if not line:
                line = words[i]
                i += 1
            lines.append(line.strip())
    return lines

def draw_text_with_outline(draw, text, position, font, text_color="white", outline_color="black"):
    x, y = position
    for adj in range(-2, 3):
        draw.text((x+adj, y), text, font=font, fill=outline_color)
        draw.text((x, y+adj), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=text_color)

def generate_meme_image(top_text, bottom_text):
    try:
        # Проверка наличия файлов
        if not os.path.exists(TEMPLATE_NAME):
            return "ОШИБКА: Не найден файл картинки. Убедитесь, что на GitHub он называется ровно 'template.jpg'"
        if not os.path.exists(FONT_NAME):
            return "ОШИБКА: Не найден файл шрифта. Убедитесь, что на GitHub он называется ровно 'arial.ttf'"

        img = Image.open(TEMPLATE_NAME)
        draw = ImageDraw.Draw(img)
        
        font_top = ImageFont.truetype(FONT_NAME, 40)
        font_bottom = ImageFont.truetype(FONT_NAME, 50)
        
        width, height = img.size
        max_txt_width = width * 0.9 

        lines_top = text_wrap(top_text, font_top, max_txt_width)
        current_h = 20
        for line in lines_top:
            w = font_top.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_top)
            current_h += font_top.getbbox(line)[3] + 5

        lines_bottom = text_wrap(bottom_text, font_bottom, max_txt_width)
        current_h = height * 0.7 
        for line in lines_bottom:
            w = font_bottom.getlength(line)
            draw_text_with_outline(draw, line, ((width - w) / 2, current_h), font_bottom)
            current_h += font_bottom.getbbox(line)[3] + 5

        img.save(RESULT_NAME)
        return RESULT_NAME
    except Exception as e:
        return f"ОШИБКА PILLOW: {e}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я читом бот (на базе платного Gemini). Пиши мне про бастурму или клубок. А командой /make_meme я сделаю мем из ваших фраз!")

@bot.message_handler(commands=['make_meme'])
def make_meme_command(message):
    if message.chat.type not in ['group', 'supergroup']:
        bot.reply_to(message, "Эту команду можно использовать только в групповом чате!")
        return

    if len(chat_history) < 2:
        bot.reply_to(message, "В истории пока слишком мало сообщений. Пообщайтесь ещё немного!")
        return

    status_msg = bot.reply_to(message, "Разматываю клубок истории, сейчас будет мем...")

    try:
        phrases = random.sample(chat_history, 2)
        top_phrase = phrases[0]
        bottom_phrase = phrases[1]

        meme_result = generate_meme_image(top_phrase, bottom_phrase)

        if meme_result and meme_result.startswith("ОШИБКА"):
            bot.edit_message_text(f"Ой, моя бастурма упала. Причина:\n{meme_result}", message.chat.id, status_msg.message_id)
        elif meme_result:
            with open(meme_result, 'rb') as photo:
                bot.send_photo(message.chat.id, photo, reply_to_message_id=message.message_id)
            os.remove(meme_result)
            bot.delete_message(message.chat.id, status_msg.message_id)
        else:
             bot.edit_message_text("Неизвестная ошибка создания мема.", message.chat.id, status_msg.message_id)
            
    except Exception as e:
        bot.edit_message_text(f"Системная ошибка: {e}", message.chat.id, status_msg.message_id)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Сбор истории
    if message.chat.type in ['group', 'supergroup'] and message.text and not message.text.startswith('/'):
        clean_text = message.text.strip()
        if clean_text and clean_text not in chat_history:
            chat_history.append(clean_text)
            if len(chat_history) > HISTORY_LIMIT:
                chat_history.pop(0)

    # Проверка, нужно ли отвечать
    if message.chat.type in ['group', 'supergroup']:
        is_mentioned = message.text and BOT_USERNAME in message.text
        is_reply = False
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
            is_reply = True
        if not (is_mentioned or is_reply):
            return

    # Запрос к нейросети Gemini
    try:
        response = model.generate_content(message.text)
        clean_reply = response.text.replace('*', '')
        bot.reply_to(message, clean_reply)
    except Exception as e:
        print(f"Ошибка Gemini: {e}")

# ==========================================
# Фейковый веб-сервер для Render
# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot with Meme maker (Gemini Edition) is running!')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

if __name__ == '__main__':
    print("Читом бот с мемоделом (Gemini) запущен...")
    bot.infinity_polling()
