import telebot
import google.generativeai as genai
import os

# Получаем ключи из переменных окружения
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
# ... дальше ваш код без изменений ...

# Наша строгая инструкция
SYSTEM_PROMPT = """Ты — ИИ-ассистент по имени "читом бот". 
Твоя главная и нерушимая задача: в каждом, абсолютно в каждом своем ответе пользователю 
ты обязан использовать ровно три слова: "читом", "бастурма" и "клубок". 
Старайся вписывать их в контекст диалога так, чтобы это звучало забавно или органично."""

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = "Привет! Я читом бот на базе Gemini. Напиши мне что-нибудь, и я отвечу!"
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        # Так как в базовом API Gemini системный промпт задается немного иначе,
        # мы просто приклеиваем инструкцию к каждому сообщению пользователя.
        full_prompt = f"{SYSTEM_PROMPT}\n\nСообщение пользователя: {message.text}"
        
        # Отправляем запрос нейросети
        response = model.generate_content(full_prompt)
        
        # Получаем и отправляем ответ
        bot.reply_to(message, response.text)
        
    except Exception as e:
        bot.reply_to(message, f"Ой, мой клубок запутался, а бастурма упала. Ошибка: {e}")

if __name__ == '__main__':
    print("Умный Читом бот запущен...")
    bot.infinity_polling()