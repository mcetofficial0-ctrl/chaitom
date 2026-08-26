import telebot
import google.generativeai as genai
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Получаем ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
# Замените на реальный юзернейм вашего бота (с собачкой в начале)
BOT_USERNAME = '@chaitom_bot'

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-3.6-flash')

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твоя главная и нерушимая задача: в каждом, абсолютно в каждом своем ответе пользователю 
ты обязан использовать ровно три слова: "читом", "бастурма" и "клубок". 
Старайся вписывать их в контекст диалога так, чтобы это звучало забавно или органично.
ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши обычным текстом."""

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я читом бот на базе Gemini. Напиши мне что-нибудь, и я отвечу!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Если сообщение пришло из группы или супергруппы
    if message.chat.type in ['group', 'supergroup']:
        # 1. Проверяем, упомянули ли бота в тексте
        is_mentioned = message.text and BOT_USERNAME in message.text
        
        # 2. Проверяем, ответили ли на сообщение самого бота (реплай)
        is_reply = False
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
            is_reply = True
            
        # Если бота не упоминали и ему не отвечали — просто игнорируем сообщение
        if not (is_mentioned or is_reply):
            return

    # Если мы дошли сюда, значит нужно отвечать!
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\nСообщение пользователя: {message.text}"
        response = model.generate_content(full_prompt)
        
        # Вырезаем звездочки
        clean_reply = response.text.replace('*', '')
        bot.reply_to(message, clean_reply)
        
    except Exception as e:
        bot.reply_to(message, f"Ой, мой клубок запутался, а бастурма упала. Ошибка: {e}")

# ==========================================
# ЧИТОМ ДЛЯ RENDER: Фейковый веб-сервер
# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')

def run_dummy_server():
    # Render сам задает переменную PORT, мы берем ее или 10000 по умолчанию
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# Запускаем фейковый сервер в отдельном потоке, чтобы он не мешал боту
threading.Thread(target=run_dummy_server, daemon=True).start()
# ==========================================

if __name__ == '__main__':
    print("Умный Читом бот запущен...")
    bot.infinity_polling()
