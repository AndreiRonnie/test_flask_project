import os
import json
import logging
import time
import threading
from flask import Flask, request, jsonify
import requests

# Попробуем импортировать openai в блоке try/except, чтобы отловить возможные проблемы
try:
    import openai
except ImportError as e:
    print(f"Не удалось импортировать openai: {e}")
    openai = None  # Если не импортирован, работаем в режиме заглушки

# ------------------------------------------------------
# Настройка прокси (раскомментируйте/уберите при необходимости)
# ------------------------------------------------------
proxy_host = "213.225.237.177"
proxy_port = "9239"
proxy_user = "user27099"
proxy_pass = "qf08ja"

proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
os.environ['http_proxy'] = proxy_url
os.environ['https_proxy'] = proxy_url

# ------------------------------------------------------
# Логирование в файл (DEBUG)
# ------------------------------------------------------
BASE_DIR = os.getcwd()  # Рабочая директория (обычно уWSGI: /home/uXXXX/test.studentshelper.ru/www)
LOGFILE_PATH = os.path.join(BASE_DIR, 'bot2.log')

logging.basicConfig(
    filename=LOGFILE_PATH,
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# ------------------------------------------------------
# Считываем OpenAI API Key из файла (если есть)
# ------------------------------------------------------
KEY_FILE_PATH = os.path.join(BASE_DIR, 'openai_key.txt')
if os.path.exists(KEY_FILE_PATH):
    try:
        with open(KEY_FILE_PATH, 'r', encoding='utf-8') as f:
            OPENAI_API_KEY = f.read().strip()
        logging.info(f"OpenAI ключ считан из файла {KEY_FILE_PATH}")
    except Exception as e:
        logging.error(f"Ошибка при чтении ключа из {KEY_FILE_PATH}: {e}")
        OPENAI_API_KEY = ""
else:
    logging.warning(f"Файл ключа {KEY_FILE_PATH} не найден. Будет использоваться заглушка.")
    OPENAI_API_KEY = ""

if openai:
    openai.api_key = OPENAI_API_KEY if OPENAI_API_KEY else None
else:
    print("Внимание! Модуль openai не импортирован. ChatGPT-запросы работать не будут.")

# ------------------------------------------------------
# Создаём Flask-приложение
# ------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# ------------------------------------------------------
# Папка для хранения файлов-диалогов
# ------------------------------------------------------
CONVERSATIONS_DIR = os.path.join(BASE_DIR, 'conversations')
if not os.path.exists(CONVERSATIONS_DIR):
    try:
        os.makedirs(CONVERSATIONS_DIR)
        logging.info(f"Создана папка {CONVERSATIONS_DIR}")
    except Exception as e:
        logging.error(f"Ошибка при создании папки {CONVERSATIONS_DIR}: {e}")

# ------------------------------------------------------
# Глобальный словарь для накопления входящих сообщений по conversation_id
# ------------------------------------------------------
pending_updates = {}  
# Структура: { conversation_id: { "messages": [ { "type": "text"/"file", "content": <str> }, ... ],
#                                   "timer": <Timer object>, "has_file": bool } }
TEXT_DELAY = 30    # 30 секунд для чисто текстовых сообщений
FILE_DELAY = 120   # 120 секунд (2 мин) если хотя бы один файл

def schedule_flush(conv_id, delay):
    """Отменяет предыдущий таймер (если есть) и ставит новый на delay секунд."""
    global pending_updates
    data = pending_updates.get(conv_id)
    if not data:
        return
    old_timer = data.get("timer")
    if old_timer:
        old_timer.cancel()
    new_timer = threading.Timer(delay, flush_pending, args=[conv_id])
    data["timer"] = new_timer
    new_timer.start()

def flush_pending(conv_id):
    """Когда таймер срабатывает, объединяем накопленные сообщения и отправляем ответ."""
    global pending_updates
    data = pending_updates.get(conv_id)
    if not data:
        return
    messages = data["messages"]
    # Объединяем все текстовые сообщения в один блок
    text_parts = []
    file_count = 0
    for msg in messages:
        if msg["type"] == "text":
            text_parts.append(msg["content"])
        elif msg["type"] == "file":
            file_count += 1
    combined_text = ""
    if file_count > 0:
        combined_text += f"Пользователь прислал {file_count} файл(а). Предположительно, это задания или методические материалы.\n"
    if text_parts:
        combined_text += "\n".join(text_parts)
    if not combined_text.strip():
        combined_text = "(Пустое сообщение)"

    # Вызываем ChatGPT (или заглушку)
    reply_text = get_chatgpt_response(combined_text, conv_id)
    logging.info(f"[flush_pending] Объединённый запрос для ChatGPT: {combined_text}")
    logging.info(f"[flush_pending] Получен итоговый ответ: {reply_text}")

    # Отправляем ответ в Talk-Me (используем talkme_token, если он хранится)
    # Здесь можно реализовать вызов talkme_send_reply(), например:
    pending_info = data.get("talkme_token", "")
    talkme_send_reply(conv_id, reply_text, pending_info)

    # Очищаем буфер для conv_id
    del pending_updates[conv_id]

def talkme_send_reply(conv_id, reply_text, talkme_token):
    """Отправляет ответ обратно в Talk-Me через их API."""
    url = "https://lcab.talk-me.ru/json/v1.0/customBot/send"
    body = {"content": {"text": reply_text}}
    headers = {
        "X-Token": talkme_token,
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(url, json=body, headers=headers)
        logging.info(f"[talkme_send_reply] Ответ Talk-Me: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"[talkme_send_reply] Ошибка при отправке ответа: {e}")

# ------------------------------------------------------
# Функции для хранения/загрузки диалога (без изменений)
# ------------------------------------------------------
def get_conversation_file_path(conversation_id) -> str:
    conversation_str = str(conversation_id)
    safe_id = conversation_str.replace('/', '_').replace('\\', '_')
    return os.path.join(CONVERSATIONS_DIR, f"conversation_{safe_id}.json")

def get_default_system_history() -> list:
    system_message = {
        "role": "system",
        "content": (
            "Ты — дружелюбный платный ассистент, который помогает студентам. Ты должен сначала "
            "узнать важные детали их заказа:\n"
            "- Тип работы (курсовая, диплом, реферат, отчет по практике и т.д.)\n"
            "- Срок выполнения (deadline)\n"
            "- Предмет или специальность\n"
			"- Есть ли дополнительная информация или требования\n"
			"Далее, если это реферат, курсовая, дипломная ВКР или магистерская, ты должен выяснить:\n"
            "- Тема работы\n"
            "- Нужна ли проверка на антиплагиат и какой нужен процент оригинальности\n"
            "- Наличие методических материалов\n\n"			
            "Если это контрольная, задачи ты должен:\n"
			"- Попросить отправить файл с задачами, заданием на контрольную \n"
			"- Узнать, есть ли дополнительная информация или требования\n"
			"Если это отчет по практике ты должен узнать:\n"
			"- Практика должна быть по конкретной организации или нет\n"
			"- Нужна ли проверка на антиплагиат и какой нужен процент оригинальности\n"
			"Если реферат, курсовая, отчет по практике, дипломная, ВКР или магистерская требует проверки на антиплагиат, нужно выяснить требуемый процент уникальности и узнать систему проверки: подойдет ли бесплатная проверка на txt или будет проверка по АнтиплагиатВУЗ\n"
			
            "Другие детали уточнять не нужно. Будь вежливым, дружелюбным, отвечай кратко "
            "и по делу. Если студент не отвечает на какие-то вопросы, повтори один-два раза, "
            "но не зацикливайся.\n\n"
            
            "Если нужно решить что-то онлайн или срок меньше дня — отказаться, так как "
            "помочь не сможем.\n\n"
            
            "После того как узнаешь (или попытался узнать) эти детали, нужно оценить "
            "стоимость помощи фразой: «За X рублей отличный автор готов помочь выполнить "
            "вашу работу в указанные сроки и по указанным требованиям. Что скажете?»\n\n"
            
            "Где X — примерная стоимость, основывающаяся на таких ориентирах:\n"
            "- Реферат (гуманитарные) ~ 1500₽\n"
            "- Курсовая (гуманитарные) ~ 3000₽\n"
            "- Контрольная (гуманитарные) ~ 1500₽\n"
            "- Задача (гуманитарные) ~ 1000₽\n"
            "- Отчёт по практике (гуманитарные) ~ 3000₽\n"
            "- Дипломная/ВКР/магистерская (гуманитарные) ~ 14000₽\n\n"
            
            "По экономическим дисциплинам цена в 1.5 раза выше, а по техническим (точным) "
            "— в 2 раза выше.\n\n"
			
			"Реальную стоимость определяй сам, исходя из сложности темы, сроков и антиплагиата, "
            "но она не должна отличаться более чем в 2 раза от рекомендованной минимальной. "
            "Нужно обязательно называть «психологически приятную» сумму (например 990₽ вместо 1000₽).\n"
            
            "Если студент хочет срок меньше дня или онлайн-решение, сообщи, что помочь не можешь.\n\n"
        )
    }
    return [system_message]

def load_history(conversation_id) -> list:
    filepath = get_conversation_file_path(conversation_id)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
                logging.debug(f"Загружена история из файла {filepath}: {history_data}")
                return history_data
        except Exception as e:
            logging.error(f"Ошибка при чтении истории {filepath}: {e}")
            return get_default_system_history()
    else:
        logging.debug(f"Файл истории не найден, возвращаем default system history: {filepath}")
        return get_default_system_history()

def save_history(conversation_id, history: list):
    filepath = get_conversation_file_path(conversation_id)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logging.debug(f"История сохранена в файл {filepath}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении истории {filepath}: {e}")

# ------------------------------------------------------
# Функция вызова ChatGPT или "заглушки"
# ------------------------------------------------------
def get_chatgpt_response(user_text: str, conversation_id) -> str:
    """
    1) Загружаем историю
    2) Добавляем сообщение 'user'
    3) Если openai не импортирован или ключ пуст -> заглушка
    4) Иначе реальный запрос к ChatGPT
    5) Сохраняем ответ
    """
    if not openai or not OPENAI_API_KEY:
        logging.warning("OpenAI недоступен (нет ключа?), возвращаем заглушку.")
        return f"(Заглушка) Вы написали: {user_text}"

    try:
        history = load_history(conversation_id)
        history.append({"role": "user", "content": user_text})
        logging.info(f"[get_chatgpt_response] Добавлено сообщение пользователя: {user_text}")

        # Здесь указываем модель (например, "gpt-4o")
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=history,
            temperature=0.5,
        )
        assistant_answer = response["choices"][0]["message"]["content"]
        logging.debug(f"[get_chatgpt_response] Ответ ChatGPT: {assistant_answer}")

        # Добавляем ответ ассистента в историю
        history.append({"role": "assistant", "content": assistant_answer})
        save_history(conversation_id, history)

        return assistant_answer

    except Exception as e:
        logging.error(f"[get_chatgpt_response] Ошибка при запросе к ChatGPT: {e}")
        return "Извините, произошла ошибка при запросе к ИИ."

# ------------------------------------------------------
# Маршрут для приёма вебхуков от Talk-Me (POST)
# ------------------------------------------------------
@app.route('/talkme_webhook', methods=['POST'])
def talkme_webhook():
    try:
        data = request.get_json(force=True)
        logging.info(f"[talkme_webhook] Получен JSON: {json.dumps(data, ensure_ascii=False)}")
    except Exception as e:
        logging.error(f"Невозможно считать JSON: {e}")
        return jsonify({"error": "Bad JSON"}), 400

    # Определяем conversation_id из searchId или token
    conv_id = data.get("client", {}).get("searchId")
    if not conv_id:
        conv_id = data.get("token", "unknown")
    else:
        conv_id = str(conv_id)

    # Определяем, если пришёл файл — например, проверяем наличие ключа "file"
    is_file = True if data.get("file") else False

    # Извлекаем текст (если есть)
    incoming_text = data.get("message", {}).get("text", "")
    talkme_token = data.get("token", "")

    logging.info(f"[talkme_webhook] conv_id={conv_id}, text={incoming_text}, is_file={is_file}")

    # Если для этого conv_id ещё нет буфера, создаём его
    if conv_id not in pending_updates:
        pending_updates[conv_id] = {"messages": [], "timer": None, "has_file": False, "talkme_token": talkme_token}

    # Добавляем входящее сообщение в буфер
    if is_file:
        pending_updates[conv_id]["messages"].append({"type": "file", "content": "(Файл получен)"})
        pending_updates[conv_id]["has_file"] = True
    if incoming_text.strip():
        pending_updates[conv_id]["messages"].append({"type": "text", "content": incoming_text})

    # Определяем задержку: если хотя бы один файл – 120 сек, иначе 30 сек
    delay = FILE_DELAY if pending_updates[conv_id]["has_file"] else TEXT_DELAY
    schedule_flush(conv_id, delay)

    logging.info(f"[talkme_webhook] Сообщения накоплены для conv_id {conv_id}. Таймер установлен на {delay} сек.")
    return jsonify({"status": "ok"}), 200

# ------------------------------------------------------
# Функция для сброса накопленных сообщений и отправки ответа
# ------------------------------------------------------
def flush_pending(conv_id):
    global pending_updates
    data = pending_updates.get(conv_id)
    if not data:
        return

    messages = data["messages"]
    # Объединяем все текстовые сообщения в один блок
    text_parts = []
    file_count = 0
    for msg in messages:
        if msg["type"] == "text":
            text_parts.append(msg["content"])
        elif msg["type"] == "file":
            file_count += 1

    combined_text = ""
    if file_count > 0:
        combined_text += f"Пользователь прислал {file_count} файл(а). Вероятно, это задания или методические материалы.\n"
    if text_parts:
        combined_text += "\n".join(text_parts)
    if not combined_text.strip():
        combined_text = "(Пустое сообщение)"

    logging.info(f"[flush_pending] Итоговый объединённый текст для conv_id {conv_id}: {combined_text}")

    # Получаем итоговый ответ от ChatGPT (или заглушки)
    reply_text = get_chatgpt_response(combined_text, conv_id)
    logging.info(f"[flush_pending] Итоговый ответ для conv_id {conv_id}: {reply_text}")

    # Отправляем ответ в Talk-Me
    talkme_send_reply(conv_id, reply_text, data["talkme_token"])

    # Удаляем накопленные данные для этого conv_id
    del pending_updates[conv_id]

# ------------------------------------------------------
# Функция для планирования сброса (таймера)
# ------------------------------------------------------
def schedule_flush(conv_id, delay):
    global pending_updates
    data = pending_updates.get(conv_id)
    if not data:
        return
    old_timer = data.get("timer")
    if old_timer:
        old_timer.cancel()
    new_timer = threading.Timer(delay, flush_pending, args=[conv_id])
    data["timer"] = new_timer
    new_timer.start()

# ------------------------------------------------------
# Функция отправки ответа в Talk-Me
# ------------------------------------------------------
def talkme_send_reply(conv_id, reply_text, talkme_token):
    url = "https://lcab.talk-me.ru/json/v1.0/customBot/send"
    body = {"content": {"text": reply_text}}
    headers = {
        "X-Token": talkme_token,
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(url, json=body, headers=headers)
        logging.info(f"[talkme_send_reply] conv_id={conv_id} Ответ Talk-Me: {resp.status_code} {resp.text}")
    except Exception as e:
        logging.error(f"[talkme_send_reply] conv_id={conv_id} Ошибка при отправке ответа: {e}")

# ------------------------------------------------------
# Корневой маршрут
# ------------------------------------------------------
@app.route('/', methods=['GET'])
def index():
    logging.debug("[index] Вызван корневой маршрут /")
    return "Bot with ChatGPT (с ключом в файле) is running", 200

# ------------------------------------------------------
# Локальный запуск
# ------------------------------------------------------
if __name__ == '__main__':
    logging.info("Запуск Flask-приложения...")
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        logging.error(f"Ошибка при запуске приложения: {e}")


