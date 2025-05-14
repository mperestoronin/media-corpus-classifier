import json
import logging
import os
import signal
import sys
import requests
import ast
import time
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'unclassified_news')
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
LLM_API_URL = os.getenv('LLM_API_URL', 'http://ai.nt.fyi/api/generate')
ADD_DOCUMENT_BACKEND_API_URL = os.getenv('ADD_DOCUMENT_BACKEND_API_URL')
LLM_API_AUTH_USER = os.getenv('LLM_API_AUTH_USER')
LLM_API_AUTH_PASS = os.getenv('LLM_API_AUTH_PASS')
BACKEND_SECRET_KEY = os.getenv('BACKEND_SECRET_KEY')

tags = [
    "Политика", "Внутренняя политика", "Внешняя политика", "Дипломатия", "Законы", "Выборы", "Политические партии",
    "Геополитика", "Экономика", "Макроэкономика", "Финансы", "Банки", "Трансграничные переводы", "Кредитование", "Гранты",
    "Инвестиции", "Энергетика", "Инфляция", "Фондовый рынок", "Бизнес", "Стартапы", "Технологии", "IT", "Цифровизация",
    "Система быстрых платежей", "Искусственный интеллект", "Кибербезопасность", "Наука", "Космос", "Медицина", "Здравоохранение",
    "Эпидемии", "Психология", "Образование", "Школа", "Университеты", "Соцподдержка", "Труд", "Занятость", "Миграция", "Права человека",
    "Экология", "Климат", "Загрязнение", "Ресурсы", "Транспорт", "Инфраструктура", "Происшествия", "Криминал", "ДТП", "Катастрофы",
    "Расследования", "Терроризм", "Скандалы", "Коррупция", "Протесты", "Армия", "Вооруженный конфликт", "Мир", "Международные отношения",
    "США", "Европа", "Азия", "Россия", "СНГ", "Ближний Восток", "Африка", "Латинская Америка", "Спорт", "Футбол", "Хоккей", "Баскетбол",
    "Олимпийские игры", "Спортсмены", "Соревнования", "Трансферы", "Культура", "Кино", "Музыка", "Театр", "Искусство", "Мода",
    "Литература", "Лайфстайл", "Еда", "Рецепты", "Путешествия", "Туризм", "Виза", "Авиация", "Отношения", "Семья", "Саморазвитие",
    "ЗОЖ", "Фитнес", "Ментальное здоровье", "Цифровые технологии", "Соцсети", "Цензура", "СМИ", "Журналистика", "Аналитика", "Интервью",
    "Прогноз", "Рейтинг", "Обзор", "Утечка", "Санкции", "Блокчейн", "Криптовалюта", "NFT", "IPO", "Слияния и поглощения", "Старение",
    "Удаленка", "Рынок труда", "Цены", "Зарплаты", "Социальные выплаты", "Тарифы", "Налоги", "Реформа", "Инновации", "Тренд", "Инцидент"
]

def create_consumer():
    while True:
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=[KAFKA_BOOTSTRAP_SERVERS],
                auto_offset_reset='latest', 
                group_id='news_classification_group',
                value_deserializer=safe_json_deserializer,
                enable_auto_commit=False,
                max_poll_interval_ms=900000,
            )
            logger.info("Успешно создан KafkaConsumer")
            return consumer
        except NoBrokersAvailable as e:
            logger.error("Ошибка подключения к Kafka: %s. Повтор через 5 секунд...", e)
            time.sleep(5)

consumer = create_consumer()

running = True

def safe_json_deserializer(m):
    if m in (None, b''):
        return None  
    try:
        return json.loads(m.decode('utf-8'))
    except json.JSONDecodeError:
        logger.warning("Невалидный JSON: %s", m[:100])
        return None

def signal_handler(sig, frame):
    global running
    logger.info("Завершаем работу...")
    running = False
    consumer.close()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def validate_and_extract_tags(classification_result, known_tags):
    """
    Проверяет поле 'response' из классификации и извлекает теги, если они валидны.
    Возвращает список тегов или None, если формат неверный или обнаружены недопустимые теги.
    """
    try:
        response_str = classification_result.get("response", "")
        # пробуем распарсить строку как список с помощью безопасного ast.literal_eval
        tags_list = ast.literal_eval(response_str)
        if not isinstance(tags_list, list):
            return None
        known_tags_lower = {tag.lower() for tag in known_tags}
        for tag in tags_list:
            if not isinstance(tag, str) or tag.lower() not in known_tags_lower:
                return None
        return tags_list
    except Exception as e:
        logger.error("Ошибка при валидации ответа: %s", e)
        return None

def classify_news(news):
    title = news.get("title", "")
    text = news.get("text", "")
    tags_str = ", ".join(f"'{tag}'" for tag in tags)
    prompt = (
        f"Скажи мне к каким тегам из списка относится эта новость? "
        f"В ответе укажи просто список подходящих тегов в квадратных скобках, например: ['Экономика', 'Право']. "
        f"Ничего больше писать нельзя. Вот список тегов: [{tags_str}]. "
        f"Вот сама новость - Заголовок: {title}. Текст: {text}."
    )

    models = ["qwen2.5:32b", "llama3:8b"]

    for model in models:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        try:
            response = requests.post(
                LLM_API_URL,
                json=payload,
                auth=(LLM_API_AUTH_USER, LLM_API_AUTH_PASS)
            )
            response.raise_for_status()
            classification_result = response.json()
            validated_tags = validate_and_extract_tags(classification_result, tags)
            if validated_tags and len(validated_tags) > 0:
                logger.info("Классификация получена с моделью %s: %s", model, validated_tags)
                return validated_tags
            else:
                logger.error("Неверный формат ответа от модели %s", model)
        except Exception as e:
            logger.error("Ошибка при вызове LLM API с моделью %s: %s", model, e)

    return ["Не определено"]

def generate_summary(news):
    """
    Генерирует улучшенное краткое содержание новости с использованием LLM (модель qwen).
    Если генерация проходит успешно, возвращает новое краткое содержание,
    иначе возвращает исходное содержание.
    """
    title = news.get("title", "")
    text = news.get("text", "")
    prompt = (
        f"Напиши краткое содержание для новости с заголовком: \"{title}\" и текстом: \"{text}\". "
        f"Сделай его лаконичным, информативным и не длиннее 50 слов, оставь только самую важную информацию. В ответе укажи только краткое содержание, ничего лишнего. Ответ должен быть на руском языке."
    )
    payload = {
        "model": "qwen2.5:32b",
        "prompt": prompt,
        "stream": False
    }
    try:
        response = requests.post(
            LLM_API_URL,
            json=payload,
            auth=(LLM_API_AUTH_USER, LLM_API_AUTH_PASS)
        )
        response.raise_for_status()
        summary_result = response.json()
        new_summary = summary_result.get("response", "").strip()
        if new_summary:
            logger.info("Новый summary сгенерирован: %s", new_summary)
            return new_summary
        else:
            logger.error("Пустой ответ от LLM при генерации summary: %s", summary_result)
            return news.get("summary", "")
    except Exception as e:
        logger.error("Ошибка при генерации summary: %s", e)
        return news.get("summary", "")

def send_to_backend(news):
    """
    Отправляет новость с результатами классификации на backend.
    """
    try:
        headers = {}
        if BACKEND_SECRET_KEY:
            headers['Authorization'] = f'Bearer {BACKEND_SECRET_KEY}'
        response = requests.post(ADD_DOCUMENT_BACKEND_API_URL, json=news, headers=headers)
        response.raise_for_status()
        logger.info("Данные успешно отправлены на backend.")
    except Exception as e:
        logger.error("Ошибка при отправке данных на backend: %s", e)

def main():
    logger.info("Сервис запущен. Ожидаем сообщения из Kafka...")
    for message in consumer:
        if not running:
            break
        news = message.value
        if news is None:
            consumer.commit()
            continue
        logger.info("Получена новость: %s", news.get("title", "Без заголовка"))
        
        validated_tags = classify_news(news)
        news['tags'] = validated_tags
        
        # Если новость прошла классификацию (теги отличны от ["Не определено"]),
        # генерируем новое краткое содержание с помощью LLM 
        if "Не определено" not in validated_tags:
            new_summary = generate_summary(news)
            news['summary'] = new_summary
        send_to_backend(news)
        logger.info("Новость: %s", news.get("title"))
        logger.info("Теги: %s", news.get("tags"))
        consumer.commit()

if __name__ == "__main__":
    main()
