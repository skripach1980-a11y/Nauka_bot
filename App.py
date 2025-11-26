import logging
import sqlite3
import requests
import time
import re
import random
import hashlib
from datetime import datetime, timedelta
import threading
import html
from urllib.parse import quote
import concurrent.futures
import os
from flask import Flask

# ========== СОЗДАЕМ FLASK ПРИЛОЖЕНИЕ ==========
app = Flask(__name__)

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN', "8292008037:AAEKFdmn3fXIWkPKnwkdwgHD8AIgOCfn2oQ")
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# КЛЮЧЕВЫЕ СЛОВА
KEYWORDS = {
    'en': [
        'ufo', 'uap', 'alien', 'extraterrestrial', 'flying saucer', 'unidentified',
        '3I/ATLAS', 'interstellar', 'comet', 'asteroid', 'meteor', 'cosmic',
        'orb', 'sighting', 'strange lights', 'mystery', 'anomaly', 'unexplained',
        'phenomenon', 'paranormal', 'supernatural', 'archaeological', 'ancient',
        'artifact', 'lost civilization', 'space', 'NASA', 'astronomy', 'celestial',
        'planet', 'mars', 'moon', 'solar system', 'galaxy', 'universe', 'science',
        'discovery', 'research', 'study', 'scientists', 'astronomers'
    ],
    'de': [
        'ufo', 'außerirdisch', 'unidentifiziert', 'komet', 'asteroid', 'meteor',
        'raum', 'weltraum', 'sichtung', 'seltsam', 'rätsel', 'phänomen', 'wissenschaft'
    ],
    'fr': [
        'ovni', 'extraterrestre', 'non identifié', 'comète', 'astéroïde', 'météore',
        'espace', 'observation', 'étrange', 'mystère', 'phénomène', 'science'
    ],
    'es': [
        'ovni', 'extraterrestre', 'no identificado', 'cometa', 'asteroide', 'meteoro',
        'espacio', 'avistamiento', 'extraño', 'misterio', 'fenómeno', 'ciencia'
    ],
    'pt': [
        'ovni', 'extraterrestre', 'não identificado', 'cometa', 'asteroide', 'meteoro',
        'espaço', 'avistamento', 'estranho', 'mistério', 'fenômeno', 'ciência'
    ],
    'ru': [
        'нло', 'пришелец', 'инопланетянин', 'неопознанный', 'комета', 'астероид',
        'метеор', 'космос', 'космический', 'аномалия', 'загадочный', 'необъяснимый',
        'наука', 'открытие', 'исследование'
    ]
}

# РАБОЧИЕ ИСТОЧНИКИ
NEWS_SOURCES = {
    'NASA News': {'url': 'https://www.nasa.gov/rss/dyn/breaking_news.rss', 'lang': 'en'},
    'Space.com': {'url': 'https://www.space.com/feeds/all', 'lang': 'en'},
    'The Guardian Science': {'url': 'https://www.theguardian.com/science/rss', 'lang': 'en'},
    'New Scientist Space': {'url': 'https://www.newscientist.com/subject/space/feed/', 'lang': 'en'},
    'Science Alert': {'url': 'https://www.sciencealert.com/feed', 'lang': 'en'},
    'Astronomy Magazine': {'url': 'https://www.astronomy.com/feed', 'lang': 'en'},
    'Universe Today': {'url': 'https://www.universetoday.com/feed/', 'lang': 'en'},
    'Phys.org': {'url': 'https://phys.org/rss-feed/breaking/', 'lang': 'en'},
    'Der Spiegel Wissenschaft': {'url': 'https://www.spiegel.de/wissenschaft/index.rss', 'lang': 'de'},
    'Le Monde Science': {'url': 'https://www.lemonde.fr/sciences/rss_full.xml', 'lang': 'fr'},
    'Science et Vie': {'url': 'https://www.science-et-vie.com/feed', 'lang': 'fr'},
    'Folha de S.Paulo Ciência': {'url': 'https://feeds.folha.uol.com.br/ciencia/rss091.xml', 'lang': 'pt'},
}

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальная блокировка для работы с БД
db_lock = threading.Lock()

# Глобальная блокировка для предотвращения одновременного поиска
search_lock = threading.Lock()

# ========== БАЗА ДАННЫХ ==========
def init_db():
    """Инициализация базы данных SQLite"""
    with db_lock:
        conn = sqlite3.connect('strange_news.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS published_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                lang TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")

def clear_old_news():
    """Очищаем старые новости из базы (старше 1 дня)"""
    with db_lock:
        conn = sqlite3.connect('strange_news.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM published_news WHERE published_at < datetime("now", "-1 days")')
        deleted_count = cursor.rowcount
        
        conn.commit()
        conn.close()
        logger.info(f"🧹 Очищено {deleted_count} старых новостей из базы")
        return deleted_count

def get_content_hash(title, description):
    """Создаем хэш контента для проверки дубликатов"""
    content = f"{title}_{description}" if description else title
    return hashlib.md5(content.encode()).hexdigest()

def is_news_published(content_hash):
    """Проверяем, публиковали ли мы уже эту новость по хэшу"""
    with db_lock:
        conn = sqlite3.connect('strange_news.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('SELECT 1 FROM published_news WHERE content_hash = ?', (content_hash,))
        result = cursor.fetchone()
        
        conn.close()
        return result is not None

def mark_news_as_published(url, title, source, lang, content_hash):
    """Добавляем новость в базу как опубликованную"""
    with db_lock:
        conn = sqlite3.connect('strange_news.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT OR IGNORE INTO published_news (url, title, source, lang, content_hash) VALUES (?, ?, ?, ?, ?)',
            (url, title, source, lang, content_hash)
        )
        
        conn.commit()
        conn.close()

def add_subscriber(chat_id, username, first_name):
    """Добавляем подписчика в базу"""
    with db_lock:
        conn = sqlite3.connect('strange_news.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute(
            'INSERT OR REPLACE INTO subscribers (chat_id, username, first_name) VALUES (?, ?, ?)',
            (chat_id, username, first_name)
        )
        
        conn.commit()
        conn.close()
        logger.info(f"✅ Добавлен подписчик: {first_name}")

def get_subscribers():
    """Получаем список всех подписчиков"""
    with db_lock:
        conn = sqlite3.connect('strange_news.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute('SELECT chat_id FROM subscribers')
        results = cursor.fetchall()
        
        conn.close()
        return [row[0] for row in results]

# ========== ЛЕНТА НОВОСТЕЙ ==========
def clean_html(text):
    """Очищаем текст от HTML-тегов"""
    if not text: return ""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', text)

def is_strange_news(title, description, lang):
    """Проверяем, относится ли новость к странным событиям"""
    if not title: 
        return False
    
    text = f"{title} {description or ''}".lower()
    
    # ТОЛЬКО самые важные исключения
    exclude_words = ['election', 'president', 'trump', 'biden', 'war', 'covid', 'coronavirus']
    if any(word in text for word in exclude_words):
        return False
    
    # УВЕЛИЧИВАЕМ шансы нахождения - проверяем любые ключевые слова
    if lang in KEYWORDS:
        return any(keyword.lower() in text for keyword in KEYWORDS[lang])
    
    return False

def fetch_news_from_source(source_name, source_info):
    """Получаем новости из одного источника с детальным логированием"""
    try:
        rss_url = source_info['url']
        lang = source_info['lang']
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*'
        }
        
        logger.debug(f"🔍 Проверяем источник: {source_name} ({rss_url})")
        response = requests.get(rss_url, headers=headers, timeout=20)
        
        if response.status_code == 200:
            import feedparser
            feed = feedparser.parse(response.content)
            
            total_entries = len(feed.entries) if hasattr(feed, 'entries') else 0
            logger.debug(f"📄 {source_name}: получено {total_entries} записей")
            
            news_items = []
            for entry in feed.entries[:10]:
                try:
                    title = clean_html(entry.title) if hasattr(entry, 'title') else ""
                    description = clean_html(entry.summary) if hasattr(entry, 'summary') else clean_html(entry.description) if hasattr(entry, 'description') else ""
                    link = entry.link if hasattr(entry, 'link') else ""
                    
                    if not title or not link:
                        continue
                    
                    # Проверяем свежесть новости (последние 3 дня)
                    is_recent = True
                    entry_time = None
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        entry_time = datetime(*entry.published_parsed[:6])
                        if datetime.now() - entry_time > timedelta(days=3):
                            is_recent = False
                    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                        entry_time = datetime(*entry.updated_parsed[:6])
                        if datetime.now() - entry_time > timedelta(days=3):
                            is_recent = False
                    
                    if is_recent:
                        if is_strange_news(title, description, lang):
                            published_date = entry_time.strftime("%d.%m.%Y %H:%M") if entry_time else "Недавно"
                            content_hash = get_content_hash(title, description)
                            
                            news_items.append({
                                'title': title,
                                'description': description,
                                'url': link,
                                'source': source_name,
                                'lang': lang,
                                'published': published_date,
                                'content_hash': content_hash,
                                'entry_time': entry_time or datetime.now()
                            })
                        else:
                            logger.debug(f"❌ {source_name}: не подходит по ключевым словам '{title[:50]}...'")
                    else:
                        logger.debug(f"📅 {source_name}: устаревшая новость '{title[:50]}...'")
                        
                except Exception as e:
                    logger.debug(f"⚠️ {source_name}: ошибка обработки записи: {e}")
                    continue
            
            logger.info(f"📡 {source_name}: найдено {len(news_items)} подходящих новостей")
            return news_items
        else:
            logger.warning(f"⚠️ {source_name}: HTTP ошибка {response.status_code}")
            return []
        
    except Exception as e:
        logger.error(f"❌ {source_name}: ошибка получения: {e}")
        return []

def search_strange_news():
    """Поиск новостей во всех источниках с детальным логированием"""
    logger.info("🔍 Начинаем поиск новостей в источниках...")
    all_news = []
    
    source_results = {}
    
    def fetch_source(source_item):
        name, info = source_item
        news = fetch_news_from_source(name, info)
        source_results[name] = len(news)
        return news
    
    # Используем ThreadPoolExecutor с ограничением потоков
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_source, item): item[0] for item in NEWS_SOURCES.items()}
        
        for future in concurrent.futures.as_completed(futures):
            source_name = futures[future]
            try:
                news_items = future.result()
                if news_items:
                    all_news.extend(news_items)
            except Exception as e:
                logger.error(f"❌ Ошибка в потоке для {source_name}: {e}")
    
    # Детальная статистика по источникам
    working_sources = {k: v for k, v in source_results.items() if v > 0}
    
    logger.info(f"📊 ИТОГИ ПОИСКА:")
    logger.info(f"✅ Работающие источники ({len(working_sources)}): {working_sources}")
    logger.info(f"📈 Всего сырых новостей: {len(all_news)}")
    
    # Сортируем по времени публикации (сначала свежие)
    sorted_news = sorted(all_news, 
                        key=lambda x: x.get('entry_time', datetime.now()), 
                        reverse=True)
    
    # Убираем дубликаты по URL и хэшу
    seen_urls = set()
    seen_hashes = set()
    unique_news = []
    
    for news in sorted_news:
        if news['url'] not in seen_urls and news['content_hash'] not in seen_hashes:
            seen_urls.add(news['url'])
            seen_hashes.add(news['content_hash'])
            unique_news.append(news)
    
    # Статистика по языкам
    lang_stats = {}
    for news in unique_news:
        lang = news['lang']
        lang_stats[lang] = lang_stats.get(lang, 0) + 1
    
    logger.info(f"🌐 Уникальные новости по языкам: {lang_stats}")
    logger.info(f"✅ Найдено {len(unique_news)} уникальных неповторяющихся новостей")
    
    return unique_news  # ВОЗВРАЩАЕМ ВСЕ НОВОСТИ БЕЗ ОГРАНИЧЕНИЙ

def translate_text(text, src_lang):
    """Переводим текст на русский"""
    try:
        if not text or len(text) < 3: 
            return text
            
        lang_map = {
            'zh': 'zh-CN', 'es': 'es', 'pt': 'pt', 'en': 'en',
            'de': 'de', 'fr': 'fr', 'ru': 'ru'
        }
        
        source_lang = lang_map.get(src_lang, 'auto')
        
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            'client': 'gtx',
            'sl': source_lang,
            'tl': 'ru', 
            'dt': 't',
            'q': text
        }
        
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data[0][0][0] if data[0] else text
        return text
    except Exception:
        return text

def create_news_message(article):
    """Создаем форматированное сообщение для ленты новостей"""
    original_lang = article['lang']
    
    # Переводим заголовок
    translated_title = translate_text(article['title'], original_lang)
    
    # Краткий пересказ описания
    translated_summary = ""
    if article['description'] and len(article['description']) > 20:
        full_translation = translate_text(article['description'], original_lang)
        if len(full_translation) > 200:
            translated_summary = full_translation[:200] + "..."
        else:
            translated_summary = full_translation
    
    # Форматируем сообщение в стиле ленты новостей
    lang_emojis = {
        'en': '🇺🇸', 'de': '🇩🇪', 'fr': '🇫🇷', 
        'es': '🇪🇸', 'pt': '🇧🇷', 'ru': '🇷🇺'
    }
    
    topic_emojis = ['👽', '🛸', '🌌', '🌀', '📡', '⚡', '🔭', '🌠', '💫', '✨']
    
    lang_emoji = lang_emojis.get(original_lang, '🌐')
    topic_emoji = random.choice(topic_emojis)
    
    # Формат ленты новостей
    message = f"{topic_emoji} *{translated_title}*\n\n"
    
    if translated_summary:
        message += f"📝 *Кратко:* {translated_summary}\n\n"
    
    message += f"🌐 *Источник:* {article['source']} {lang_emoji}\n"
    message += f"🕒 *Время:* {article['published']}\n"
    message += f"🔗 [Читать полностью]({article['url']})"
    
    return message

# ========== TELEGRAM BOT ==========
def send_telegram_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    try:
        url = f"{TELEGRAM_URL}/sendMessage"
        payload = {
            'chat_id': chat_id, 
            'text': text, 
            'parse_mode': 'Markdown',
            'disable_web_page_preview': False
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения: {e}")
        return False

def get_updates(offset=None):
    """Получаем обновления от Telegram"""
    try:
        url = f"{TELEGRAM_URL}/getUpdates"
        params = {'timeout': 10, 'offset': offset} if offset else {'timeout': 10}
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            return response.json().get('result', [])
        return []
    except Exception as e:
        logger.error(f"❌ Ошибка получения updates: {e}")
        return []

def handle_updates():
    """Обрабатываем входящие сообщения"""
    global last_update_id
    
    try:
        updates = get_updates(last_update_id)
        
        for update in updates:
            last_update_id = update['update_id'] + 1
            
            if 'message' in update:
                message = update['message']
                chat_id = message['chat']['id']
                text = message.get('text', '')
                user = message.get('from', {})
                
                # АВТОМАТИЧЕСКАЯ ПОДПИСКА ПРИ ЛЮБОМ СООБЩЕНИИ
                add_subscriber(chat_id, user.get('username'), user.get('first_name'))
                
                if text == '/feed' or text == '/test':
                    send_telegram_message(chat_id, "🛸 Загружаю свежую ленту новостей...")
                    
                    def search_and_send():
                        try:
                            news_items = search_strange_news()
                            
                            if news_items:
                                send_telegram_message(chat_id, f"📊 *Найдено новостей:* {len(news_items)}")
                                time.sleep(1)
                                
                                sent_count = 0
                                for article in news_items:
                                    # Финальная проверка перед отправкой
                                    if not is_news_published(article['content_hash']):
                                        message = create_news_message(article)
                                        if send_telegram_message(chat_id, message):
                                            mark_news_as_published(article['url'], article['title'], article['source'], article['lang'], article['content_hash'])
                                            sent_count += 1
                                        time.sleep(1)
                                
                                if sent_count > 0:
                                    send_telegram_message(chat_id, f"✅ Опубликовано {sent_count} новых новостей!")
                                else:
                                    send_telegram_message(chat_id, "📭 Все новости уже были в ленте")
                            else:
                                send_telegram_message(chat_id, "🔍 Новых загадочных новостей не найдено")
                                
                        except Exception as e:
                            send_telegram_message(chat_id, "⚠️ Ошибка при загрузке ленты")
                            logger.error(f"❌ Ошибка в поиске: {e}")
                    
                    threading.Thread(target=search_and_send, daemon=True).start()
                
                elif text == '/stats':
                    # Быстрая проверка источников
                    send_telegram_message(chat_id, "📡 Проверяю работоспособность источников...")
                    
                    def check_sources():
                        try:
                            test_news = search_strange_news()
                            send_telegram_message(chat_id, f"📊 Статистика: найдено {len(test_news)} новостей из {len(NEWS_SOURCES)} источников")
                        except Exception as e:
                            send_telegram_message(chat_id, f"⚠️ Ошибка проверки: {e}")
                    
                    threading.Thread(target=check_sources, daemon=True).start()

                elif text == '/clear':
                    # Очистка старых новостей
                    def clear_db():
                        try:
                            deleted_count = clear_old_news()
                            send_telegram_message(chat_id, f"🧹 Очищено {deleted_count} старых новостей")
                        except Exception as e:
                            send_telegram_message(chat_id, f"⚠️ Ошибка очистки: {e}")
                    
                    threading.Thread(target=clear_db, daemon=True).start()
                        
    except Exception as e:
        logger.error(f"❌ Ошибка в обработке updates: {e}")

# ========== АВТОМАТИЧЕСКАЯ ЛЕНТА ==========
def auto_news_feed():
    """Автоматическое обновление ленты новостей каждые 30 минут"""
    # Ждем 5 минут после запуска бота перед первым обновлением
    time.sleep(300)
    
    # Счетчик циклов для логирования
    cycle_count = 0
    
    while True:
        try:
            subscribers = get_subscribers()
            if subscribers:
                cycle_count += 1
                logger.info(f"🕒 Авто-обновление #{cycle_count}: Запуск...")
                
                # Используем блокировку чтобы избежать одновременного поиска
                if search_lock.acquire(blocking=False):
                    try:
                        news_items = search_strange_news()
                        
                        if news_items:
                            new_count = 0
                            
                            # Отправляем только новые новости
                            for article in news_items:
                                # Финальная проверка перед отправкой
                                if not is_news_published(article['content_hash']):
                                    message = create_news_message(article)
                                    
                                    success_count = 0
                                    for chat_id in subscribers:
                                        if send_telegram_message(chat_id, message):
                                            success_count += 1
                                        time.sleep(0.3)
                                    
                                    if success_count > 0:
                                        mark_news_as_published(article['url'], article['title'], article['source'], article['lang'], article['content_hash'])
                                        new_count += 1
                                        logger.info(f"✅ Новость опубликована: {article['title'][:50]}...")
                                    time.sleep(2)
                            
                            if new_count > 0:
                                logger.info(f"✅ В ленту добавлено {new_count} новостей")
                                
                                # Уведомляем подписчиков
                                for chat_id in subscribers:
                                    send_telegram_message(chat_id, f"🆕 *ОБНОВЛЕНИЕ ЛЕНТЫ*\nДобавлено {new_count} новых новостей!")
                                    break  # Только первому подписчику
                            else:
                                logger.info("📭 Новых новостей для ленты нет")
                        else:
                            logger.info("🔍 Новостей для ленты не найдено")
                            
                    finally:
                        search_lock.release()
                else:
                    logger.info("⏳ Пропускаем цикл - другой поиск уже выполняется")
            
            # Ждем 30 минут до следующего обновления
            logger.info("⏰ Следующее обновление через 30 минут...")
            time.sleep(1800)  # 30 минут
            
        except Exception as e:
            logger.error(f"❌ Ошибка в авто-обновлении ленты: {e}")
            time.sleep(300)  # 5 минут при ошибке

# ========== FLASK ROUTES ==========
@app.route('/')
def home():
    """Главная страница для проверки работы"""
    return "🛸 UFO News Bot is running! Send /feed to Telegram bot"

@app.route('/health')
def health():
    """Эндпоинт для проверки здоровья приложения"""
    return {
        "status": "ok", 
        "bot": "running",
        "timestamp": datetime.now().isoformat()
    }

@app.route('/test')
def test():
    """Тестовый эндпоинт для проверки поиска новостей"""
    try:
        news = search_strange_news()
        return {
            "news_count": len(news),
            "news": news[:3]  # Первые 3 новости для примера
        }
    except Exception as e:
        return {"error": str(e)}, 500

# ========== ИНИЦИАЛИЗАЦИЯ И ЗАПУСК ==========
def initialize_bot():
    """Инициализация и запуск бота"""
    logger.info("🚀 Запуск UFO News Feed бота...")
    
    # Инициализируем базу
    init_db()
    
    # Очищаем старые новости при запуске
    clear_old_news()
    
    global last_update_id
    last_update_id = 0
    
    # Запускаем авто-обновление ленты в отдельном потоке
    threading.Thread(target=auto_news_feed, daemon=True).start()
    
    # Запускаем обработку Telegram updates в отдельном потоке
    threading.Thread(target=updates_worker, daemon=True).start()
    
    logger.info(f"✅ Лента новостей запущена! {len(NEWS_SOURCES)} источников активны")
    logger.info("⏰ Авто-обновление каждые 30 минут")
    logger.info("🤖 Бот готов к работе - отправьте любое сообщение для подписки")

def updates_worker():
    """Рабочий поток для обработки Telegram updates"""
    while True:
        try:
            handle_updates()
            time.sleep(2)
        except Exception as e:
            logger.error(f"❌ Ошибка в обработке updates: {e}")
            time.sleep(10)

# Запускаем бота при импорте
initialize_bot()

# Запуск для Render
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False))