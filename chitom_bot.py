import telebot
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from groq import Groq

# Получаем ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY') # Новый ключ!

bot = telebot.TeleBot(TELEGRAM_TOKEN)
# Подключаемся к Groq
client = Groq(api_key=GROQ_API_KEY)

# Замените на реальный юзернейм вашего бота (с собачкой в начале)
BOT_USERNAME = '@chaitom_bot' 

SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твоя главная и нерушимая задача: в каждом, абсолютно в каждом своем ответе пользователю 
ты обязан использовать ровно три слова: "читом", "бастурма" и "клубок". 
Старайся вписывать их в контекст диалога так, чтобы это звучало забавно или органично.
ВАЖНО: Ни в коем случае не используй звездочки (*) и форматирование текста! Пиши обычным текстом."""

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я читом бот на базе сверхбыстрого Groq. Напиши мне что-нибудь, и я отвечу!")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Логика для работы в группах
    if message.chat.type in ['group', 'supergroup']:
        is_mentioned = message.text and BOT_USERNAME in message.text
        is_reply = False
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
            is_reply = True
        if not (is_mentioned or is_reply):
            return

    try:
        # Отправляем запрос нейросети LLaMA 3 через Groq
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.text}
            ],
            model="llama3-8b-8192", # Это легкая, быстрая и бесплатная модель
        )
        
        # Получаем ответ
        response_text = chat_completion.choices[0].message.content
        
        # Вырезаем звездочки
        clean_reply = response_text.replace('*', '')
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
        self.wfile.write(b'Bot is running on Groq!')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
# ==========================================

if __name__ == '__main__':
    print("Умный Читом бот (Groq) запущен...")
    bot.infinity_polling()
