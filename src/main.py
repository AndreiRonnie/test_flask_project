import os
import json
import logging
import time
import threading
from flask import Flask, request, jsonify
import requests

# ------------------------------------------------------
# 1) Убираем все упоминания "import openai"
#    и код, связанный с openai.api_key.
#    Здесь мы имитируем ИИ-ответ, вместо ChatGPT.
# ------------------------------------------------------

# Если прокси нужен только для запросов в Talk-Me, можем оставить эти строки
proxy_host = "213.225.237.177"
proxy_port = "9239"
proxy_user = "user27099"
proxy_pass = "qf08ja"

proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"

# При желании можно закомментировать, если не нужен прокси:
os.environ['http_proxy'] = proxy_url
os.environ['https_proxy'] = proxy_url

# ------------------------------------------------------
# 2) Логирование в файл с уровнем DEBUG
# ------------------------------------------------------
BASE_DIR = os.getcwd()  # Рабочая директория
LOGFILE_PATH = os.path.join(BASE_DIR, 'bot_no_openai.log')

logging.basicConfig(
    filename=LOGFILE_PATH,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# ------------------------------------------------------
# Папка для хранения "истории" (при желании можно убрать)
# ------------------------------------------------------
CONVERSATIONS_DIR = os.path.join(BASE_DIR, 'conversations')
if not os.path.exists(CONVERSATIONS_DIR):
    try:
        os.makedirs(CONVERSATIONS_DIR)
        logging.info(f"Создана папка {CONVERSATIONS_DIR}")
    except Exception as e:
        logging.error(f"Ошибка при создании папки {CONVERSATIONS_DIR}: {e}")

# ------------------------------------------------------
# Фоновая задача (каждые 10 секунд пишет в лог)
# ------------------------------------------------------
def periodic_logger():
    while True:
        logging.info("Periodic log message: the bot (no openai) is running")
        time.sleep(10)

thread = threading.Thread(target=periodic_logger, daemon=True)
thread.start()

# ------------------------------------------------------
# Функции для "истории" (можно упростить, если не нужно)
# ------------------------------------------------------
def get_conversation_file_path(conversation_id: str) -> str:
    safe_id = conversation_id.replace('/', '_').replace('\\', '_')
    return os.path.join(CONVERSATIONS_DIR, f"conversation_{safe_id}.json")

def get_default_system_history() -> list:
    system_message = {
        "role": "system",
        "content": (
            "Ты — ассистент (заглушка), который не имеет доступа к ChatGPT. "
            "Просто храни диалог и отвечай фиксированным сообщением."
        )
    }
    return [system_message]

def load_history(conversation_id: str) -> list:
    filepath = get_conversation_file_path(conversation_id)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logging.debug(f"Загружена история из файла {filepath}: {data}")
                return data
        except Exception as e:
            logging.error(f"Ошибка при чтении истории {filepath}: {e}")
            return get_default_system_history()
    else:
        logging.debug(f"Файл истории не найден, возвращаем дефолтную историю: {filepath}")
        return get_default_system_history()

def save_history(conversation_id: str, history: list):
    filepath = get_conversation_file_path(conversation_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logging.debug(f"История сохранена в файл {filepath}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении истории {filepath}: {e}")

# ------------------------------------------------------
# "Функция" для ответа вместо ChatGPT
# ------------------------------------------------------
def get_fake_response(user_text: str, conversation_id: str) -> str:
    """
    Вместо ChatGPT возвращаем заглушку:
    1) Загружаем историю
    2) Добавляем сообщение пользователя
    3) Создаём "ответ"
    4) Добавляем "ответ" в историю
    5) Сохраняем
    """
    try:
        history = load_history(conversation_id)
        history.append({"role": "user", "content": user_text})

        # Сформируем ответ-заглушку
        assistant_answer = f"Здесь должна быть логика ИИ, но сейчас только заглушка. Вы написали: {user_text}"
        history.append({"role": "assistant", "content": assistant_answer})

        save_history(conversation_id, history)
        return assistant_answer

    except Exception as e:
        logging.error(f"Ошибка в get_fake_response: {e}")
        return "Извините, произошла внутренняя ошибка (заглушка)."

# ------------------------------------------------------
# Маршрут для приема вебхуков от Talk-Me
# ------------------------------------------------------
@app.route('/talkme_webhook', methods=['POST'])
def talkme_webhook():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        logging.error(f"Невозможно считать JSON: {e}")
        return jsonify({"error": "Bad JSON"}), 400

    search_id = data.get("client", {}).get("searchId")
    if not search_id:
        search_id = data.get("token", "unknown")

    # Текст сообщения пользователя
    incoming_text = data.get("message", {}).get("text", "")
    talkme_token = data.get("token", "")

    logging.info(f"Получен webhook: searchId={search_id}, text={incoming_text}")

    # Вместо ChatGPT — "заглушка"
    reply_text = get_fake_response(incoming_text, search_id)

    # Отправляем ответ обратно в Talk-Me
    url = "https://lcab.talk-me.ru/json/v1.0/customBot/send"
    body = {"content": {"text": reply_text}}
    headers = {
        "X-Token": talkme_token,
        "Content-Type": "application/json"
    }

    try:
        resp = requests.post(url, json=body, headers=headers)
        logging.info(f"Отправили ответ в Talk-Me: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"Ошибка при отправке ответа в Talk-Me: {e}")

    return jsonify({"status": "ok"}), 200

# ------------------------------------------------------
# Корневой маршрут GET /
# ------------------------------------------------------
@app.route('/', methods=['GET'])
def index():
    logging.debug("Вызван корневой маршрут / (no openai)")
    return "Bot without real OpenAI is running", 200

# ------------------------------------------------------
# Локальный запуск
# ------------------------------------------------------
if __name__ == '__main__':
    logging.info("Запуск Flask-приложения (no openai)")
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        logging.error(f"Ошибка при запуске приложения: {e}")

