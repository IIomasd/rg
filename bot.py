import logging
import csv
import os
import asyncio
import re
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Попытка импортировать FlightRadarAPI (необязательно)
try:
    from FlightRadarAPI import FlightRadar24API
    FR24_AVAILABLE = True
except ImportError:
    FR24_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.info("FlightRadarAPI не установлена, этот источник будет пропущен")

# -------------------- КОНФИГУРАЦИЯ --------------------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    # Список источников данных (пробуем по порядку)
    SOURCES = [
        {
            "name": "ADS-B Exchange (main)",
            "url": "https://data.adsbexchange.com/aircraft.json",
            "type": "adsb",
        },
        {
            "name": "ADS-B Exchange (alt)",
            "url": "https://api.adsbexchange.com/aircraft.json",
            "type": "adsb",
        },
        {
            "name": "ADS-B.lol",
            "url": "https://api.adsb.lol/aircraft.json",
            "type": "adsb",
        },
        {
            "name": "OpenSky Network",
            "url": "https://opensky-network.org/api/states/all",
            "type": "opensky",
        },
        # Flightradar24 будет добавлен отдельно, если библиотека установлена
    ]

    HEADERS = {
        "User-Agent": "MilitaryAircraftBot/1.0",
        "Accept": "application/json",
    }

    DATABASE_URL = "https://drive.google.com/uc?export=download&id=1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"
    FALLBACK_DATABASE_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    LOCAL_DB_FILE = "aircraftDatabase.csv"
    DEFAULT_INTERVAL = 30
    MIN_INTERVAL = 15
    DB_DOWNLOAD_TIMEOUT = 90
    DB_RETRY_ATTEMPTS = 3
    DB_RETRY_DELAY = 5

# -------------------- СЛОВАРИ (полные) --------------------
COUNTRY_CODES = { ... }  # Вставьте полный словарь из предыдущих версий
AIRCRAFT_NAMES = { ... } # Вставьте полный словарь
TARGET_CODES = { ... }   # Вставьте набор целевых кодов

# -------------------- ЛОГИРОВАНИЕ --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def get_country_by_registration(registration: str) -> str:
    # ... (оставьте как было)
    pass

def format_coordinates(lat: float, lon: float) -> str:
    # ... (оставьте как было)
    pass

def normalize_type(aircraft_type: str) -> str:
    # ... (оставьте как было)
    pass

def is_target_aircraft(aircraft_type: str) -> bool:
    # ... (оставьте как было)
    pass

# -------------------- ЗАГРУЗЧИК БАЗЫ --------------------
class AircraftDatabase:
    # ... (полный класс из предыдущего кода) ...

# -------------------- ОСНОВНОЙ ТРЕКЕР (с перебором источников) --------------------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked_aircrafts: Dict[str, Dict] = {}
        self.active_chats: set = set()
        self.chat_intervals: Dict[int, int] = {}
        self.fr_api = FlightRadar24API() if FR24_AVAILABLE else None

    def get_interval(self, chat_id: int) -> int:
        return self.chat_intervals.get(chat_id, Config.DEFAULT_INTERVAL)

    def set_interval(self, chat_id: int, interval_seconds: int):
        self.chat_intervals[chat_id] = interval_seconds

    # ---------- ADS-B Exchange парсинг ----------
    def parse_adsb_data(self, json_data: dict) -> List[Dict]:
        aircrafts = []
        if not json_data:
            return aircrafts
        try:
            ac_list = json_data.get('ac', [])
            for ac in ac_list:
                if not isinstance(ac, dict):
                    continue
                icao = ac.get('hex', '').upper()
                if not icao:
                    continue
                if ac.get('gnd', False):
                    continue
                aircraft = {
                    'icao': icao,
                    'registration': ac.get('r', '').strip() or 'N/A',
                    'call_sign': ac.get('flight', '').strip() or 'N/A',
                    'type': ac.get('t', 'N/A'),
                    'operator': ac.get('ownOp', '').strip() or 'N/A',
                    'lat': ac.get('lat'),
                    'lon': ac.get('lon'),
                    'altitude': ac.get('alt'),
                    'speed': ac.get('speed'),
                    'timestamp': datetime.now()
                }
                aircraft['country'] = get_country_by_registration(aircraft['registration'])
                aircraft['coordinates'] = format_coordinates(aircraft['lat'], aircraft['lon'])
                aircrafts.append(aircraft)
        except Exception as e:
            logger.error(f"Ошибка парсинга ADS-B: {e}", exc_info=True)
        return aircrafts

    # ---------- OpenSky парсинг ----------
    def parse_opensky_data(self, json_data: dict) -> List[Dict]:
        aircrafts = []
        if not json_data or 'states' not in json_data:
            return aircrafts
        for state in json_data['states']:
            if not state or len(state) < 8:
                continue
            icao = state[0] or 'N/A'
            if icao == 'N/A':
                continue
            if state[8]:  # on_ground
                continue
            aircraft = {
                'icao': icao,
                'registration': 'N/A',
                'call_sign': (state[1] or '').strip() or 'N/A',
                'type': 'N/A',
                'operator': 'N/A',
                'lat': state[6],
                'lon': state[5],
                'altitude': state[7],
                'speed': state[9],
                'timestamp': datetime.now(),
                'country': state[2] or 'Неизвестно',
                'coordinates': format_coordinates(state[6], state[5])
            }
            aircrafts.append(aircraft)
        return aircrafts

    # ---------- Flightradar24 через библиотеку ----------
    async def fetch_fr24(self) -> List[Dict]:
        if not self.fr_api:
            return []
        try:
            logger.info("Запрос к Flightradar24 (библиотека)...")
            flights = await asyncio.to_thread(self.fr_api.get_flights)
            if not flights:
                return []
            aircrafts = []
            for flight in flights:
                icao = getattr(flight, 'id', '').upper()
                if not icao:
                    continue
                aircraft = {
                    'icao': icao,
                    'registration': getattr(flight, 'registration', 'N/A') or 'N/A',
                    'call_sign': getattr(flight, 'callsign', 'N/A') or 'N/A',
                    'type': getattr(flight, 'type', 'N/A') or 'N/A',
                    'operator': getattr(flight, 'operator', 'N/A') or 'N/A',
                    'lat': getattr(flight, 'latitude', None),
                    'lon': getattr(flight, 'longitude', None),
                    'altitude': getattr(flight, 'altitude', None),
                    'speed': getattr(flight, 'speed', None),
                    'timestamp': datetime.now(),
                    'country': getattr(flight, 'origin_country', 'Неизвестно') or 'Неизвестно',
                    'coordinates': format_coordinates(
                        getattr(flight, 'latitude', None),
                        getattr(flight, 'longitude', None)
                    )
                }
                aircrafts.append(aircraft)
            logger.info(f"FR24: получено {len(aircrafts)} бортов")
            return aircrafts
        except Exception as e:
            logger.error(f"Ошибка получения данных из Flightradar24: {e}")
            return []

    # ---------- Основной мониторинг ----------
    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        aircrafts = []
        source_used = None

        # 1. Пробуем все источники из Config.SOURCES (ADS-B, OpenSky)
        async with aiohttp.ClientSession(headers=Config.HEADERS) as session:
            for source in Config.SOURCES:
                try:
                    logger.info(f"Пробую источник: {source['name']} ({source['url']})")
                    if source['type'] == 'adsb':
                        async with session.get(source['url'], timeout=20) as response:
                            if response.status == 200:
                                try:
                                    json_data = await response.json(content_type=None)
                                    aircrafts = self.parse_adsb_data(json_data)
                                    if aircrafts:
                                        source_used = source['name']
                                        break
                                except Exception as json_err:
                                    text = await response.text()
                                    logger.error(f"Ошибка парсинга JSON от {source['name']}: {json_err}, получено: {text[:200]}...")
                                    continue
                            else:
                                logger.warning(f"{source['name']} статус {response.status}")
                    elif source['type'] == 'opensky':
                        connector = aiohttp.TCPConnector(family=socket.AF_INET)
                        timeout = aiohttp.ClientTimeout(total=30, connect=10)
                        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as os_session:
                            async with os_session.get(source['url'], headers={'User-Agent': 'Mozilla/5.0'}) as response:
                                if response.status == 200:
                                    json_data = await response.json()
                                    aircrafts = self.parse_opensky_data(json_data)
                                    if aircrafts:
                                        source_used = source['name']
                                        break
                                else:
                                    logger.warning(f"OpenSky статус {response.status}")
                except Exception as e:
                    logger.error(f"Ошибка при обращении к {source['name']}: {e}")
                    continue

        # 2. Если ни один не дал данных, пробуем Flightradar24 (если доступен)
        if not aircrafts and self.fr_api:
            logger.info("ADS-B и OpenSky не дали данных, пробую Flightradar24...")
            aircrafts = await self.fetch_fr24()
            if aircrafts:
                source_used = "Flightradar24"

        if aircrafts:
            logger.info(f"Источник: {source_used}, получено {len(aircrafts)} бортов")
            await self.process_aircrafts(aircrafts, chat_id, context)
        else:
            logger.info("Новых целей не найдено (все источники недоступны или нет данных)")

    async def process_aircrafts(self, aircrafts: List[Dict], chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> List[str]:
        new_detections = []

        for aircraft in aircrafts:
            icao = aircraft['icao']
            if icao in self.tracked_aircrafts:
                continue

            db_entry = self.db.get(icao)
            if db_entry:
                aircraft_type = db_entry['type']
                registration = db_entry['registration']
            else:
                aircraft_type = aircraft.get('type', 'N/A')
                registration = aircraft.get('registration', 'N/A')

            if aircraft_type == 'N/A':
                continue

            if not is_target_aircraft(aircraft_type):
                continue

            aircraft['type'] = aircraft_type
            aircraft['registration'] = registration if registration != 'N/A' else aircraft.get('registration', 'N/A')
            self.tracked_aircrafts[icao] = aircraft

            clean_type = normalize_type(aircraft_type)
            type_name = AIRCRAFT_NAMES.get(clean_type, aircraft_type)

            message = (
                "🚨 Самолет обнаружен!\n"
                f"🕒 Время: {aircraft['timestamp'].strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"▫️ ICAO: {icao}\n"
                f"▫️ Регистрация: {aircraft['registration'] or 'N/A'}\n"
                f"▫️ Позывной: {aircraft['call_sign'] or 'N/A'}\n"
                f"▫️ Тип: {type_name}\n"
                f"▫️ Страна: {aircraft['country']}\n"
                f"▫️ Координаты: {aircraft['coordinates']}"
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                disable_web_page_preview=True
            )
            new_detections.append(icao)

        if new_detections:
            logger.info(f"Обнаружено {len(new_detections)} новых целей")
        else:
            logger.info("Новых целей не найдено")

        return new_detections

# -------------------- ОБРАБОТЧИКИ КОМАНД (без изменений) --------------------
tracker = None

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🟢 Запустить мониторинг", "🔴 Остановить"],
            ["📊 Статус", "⚙️ Интервал"]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие"
    )

def get_interval_keyboard():
    options = [60, 300, 600, 1800, 3600]
    buttons = []
    for sec in options:
        label = f"{sec // 60} мин"
        buttons.append([KeyboardButton(label)])
    buttons.append(["🔙 Назад"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)

async def _start_monitoring_for_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE, interval: Optional[int] = None):
    if context.job_queue is None:
        raise RuntimeError("JobQueue не доступен.")
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in jobs:
        job.schedule_removal()
    if interval is None:
        interval = tracker.get_interval(chat_id)
    context.job_queue.run_repeating(
        tracker.monitor,
        interval=timedelta(seconds=interval),
        first=5,
        chat_id=chat_id,
        name=str(chat_id),
        job_kwargs={'max_instances': 1}
    )
    tracker.active_chats.add(chat_id)
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🛩 Универсальный авиационный трекер\n"
        "Использует ADS-B Exchange, OpenSky и Flightradar24.\n"
        "Автоматически запускаю мониторинг...",
        reply_markup=get_main_keyboard()
    )
    try:
        started = await _start_monitoring_for_chat(chat_id, context)
        if started:
            interval = tracker.get_interval(chat_id)
            await update.message.reply_text(f"✅ Мониторинг активен (интервал: {interval} сек.)")
        else:
            await update.message.reply_text("⚠️ Мониторинг уже запущен.")
    except RuntimeError as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Универсальный авиационный трекер*\n\n"
        "Бот отслеживает самолёты по данным ADS-B Exchange, OpenSky и Flightradar24.\n"
        "Фильтрация по типу из списка целевых.\n"
        "При обнаружении приходит уведомление.\n\n"
        "*Команды:*\n"
        "/start — запустить мониторинг\n"
        "/help — справка\n"
        "/status — статус\n"
        "/stop — остановить\n"
        "/setinterval <сек> — установить интервал (число секунд, минимум 15)",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    interval = tracker.get_interval(chat_id)
    is_active = chat_id in tracker.active_chats
    await update.message.reply_text(
        f"🔍 Отслежено бортов: {len(tracker.tracked_aircrafts)}\n"
        f"⏱ Интервал: {interval} сек.\n"
        f"🟢 Мониторинг: {'активен' if is_active else 'остановлен'}\n"
        f"⏳ Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        reply_markup=get_main_keyboard()
    )

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        started = await _start_monitoring_for_chat(chat_id, context)
        if started:
            interval = tracker.get_interval(chat_id)
            await update.message.reply_text(f"✅ Мониторинг запущен (интервал {interval} сек.).", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text("⚠️ Мониторинг уже активен.", reply_markup=get_main_keyboard())
    except RuntimeError as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.job_queue is None:
        await update.message.reply_text("❌ Планировщик задач не доступен.", reply_markup=get_main_keyboard())
        return
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if not jobs:
        await update.message.reply_text("ℹ️ Мониторинг не активен", reply_markup=get_main_keyboard())
        return
    for job in jobs:
        job.schedule_removal()
    tracker.active_chats.discard(chat_id)
    if not tracker.active_chats:
        tracker.tracked_aircrafts.clear()
    await update.message.reply_text("⛔ Мониторинг остановлен", reply_markup=get_main_keyboard())

async def interval_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = tracker.get_interval(chat_id)
    await update.message.reply_text(
        f"⚙️ *Настройка интервала опроса*\n\n"
        f"Текущий интервал: *{current} сек.*\n\n"
        "Выберите новый интервал:",
        parse_mode="Markdown",
        reply_markup=get_interval_keyboard()
    )

async def handle_interval_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if text == "🔙 Назад":
        await update.message.reply_text("Главное меню", reply_markup=get_main_keyboard())
        return

    interval_map = {
        "1 мин": 60,
        "5 мин": 300,
        "10 мин": 600,
        "30 мин": 1800,
        "60 мин": 3600
    }
    if text in interval_map:
        new_interval = interval_map[text]
        tracker.set_interval(chat_id, new_interval)
        if chat_id in tracker.active_chats:
            try:
                await _start_monitoring_for_chat(chat_id, context, new_interval)
                await update.message.reply_text(
                    f"✅ Интервал изменён на {new_interval} сек. Мониторинг перезапущен.",
                    reply_markup=get_main_keyboard()
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка при перезапуске: {e}", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(
                f"✅ Интервал сохранён ({new_interval} сек.). Запустите мониторинг для применения.",
                reply_markup=get_main_keyboard()
            )
    else:
        await update.message.reply_text("Неизвестный вариант. Используйте кнопки.", reply_markup=get_interval_keyboard())

async def set_interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Укажите интервал в секундах, например: /setinterval 30")
        return
    try:
        seconds = int(context.args[0])
        if seconds < Config.MIN_INTERVAL:
            await update.message.reply_text(f"Минимальный интервал – {Config.MIN_INTERVAL} секунд.")
            return
        tracker.set_interval(chat_id, seconds)
        if chat_id in tracker.active_chats:
            await _start_monitoring_for_chat(chat_id, context, seconds)
            await update.message.reply_text(f"✅ Интервал изменён на {seconds} сек. Мониторинг перезапущен.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(f"✅ Интервал сохранён ({seconds} сек.). Запустите мониторинг.", reply_markup=get_main_keyboard())
    except ValueError:
        await update.message.reply_text("Введите число секунд.")

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используйте кнопки ⬇️", reply_markup=get_main_keyboard())

# -------------------- HTTP-HEALTHCHECK --------------------
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server(port=8080):
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"Healthcheck сервер запущен на порту {port}")
    server.serve_forever()

# -------------------- ЗАПУСК --------------------
def main():
    global tracker

    health_thread = threading.Thread(target=run_health_server, args=(8080,), daemon=True)
    health_thread.start()

    db = AircraftDatabase()
    db.load_sync()

    tracker = AircraftTracker(db)

    application = Application.builder().token(Config.BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("monitor", start_monitoring))
    application.add_handler(CommandHandler("stop", stop_monitoring))
    application.add_handler(CommandHandler("setinterval", set_interval_command))

    application.add_handler(MessageHandler(filters.Text("🟢 Запустить мониторинг"), start_monitoring))
    application.add_handler(MessageHandler(filters.Text("🔴 Остановить"), stop_monitoring))
    application.add_handler(MessageHandler(filters.Text("📊 Статус"), status))
    application.add_handler(MessageHandler(filters.Text("⚙️ Интервал"), interval_settings))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_interval_choice))

    application.add_handler(MessageHandler(filters.ALL, unknown_command))

    logger.info("🚀 Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()
