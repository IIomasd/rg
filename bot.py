import logging
import csv
import os
import asyncio
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
import aiohttp
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------- Импорт источников ----------
try:
    from opensky_api import OpenSkyApi, TokenManager
    OPENSKY_AVAILABLE = True
except ImportError:
    OPENSKY_AVAILABLE = False
    logging.getLogger(__name__).warning("opensky-api не установлена")

try:
    from pyfr24 import FlightRadar24
    FR24_PY_AVAILABLE = True
except ImportError:
    FR24_PY_AVAILABLE = False
    logging.getLogger(__name__).warning("pyfr24 не установлена")

# ---------- Конфигурация ----------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    # Путь к файлу credentials.json для OpenSky OAuth2
    OPENSKY_CREDENTIALS = "credentials.json"

    # Источники данных (порядок важен)
    SOURCES = [
        {"name": "OpenSky (OAuth2)", "type": "opensky_oauth"},
        {"name": "ADS-B Exchange", "type": "adsb"},
        {"name": "Flightradar24 (pyfr24)", "type": "fr24_py"},
    ]

    DATABASE_URL = "https://drive.google.com/uc?export=download&id=1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"
    FALLBACK_DATABASE_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    LOCAL_DB_FILE = "aircraftDatabase.csv"
    DEFAULT_INTERVAL = 60  # секунд
    MIN_INTERVAL = 15
    DB_DOWNLOAD_TIMEOUT = 90
    DB_RETRY_ATTEMPTS = 3
    DB_RETRY_DELAY = 5

# ---------- Географические регионы ----------
REGIONS = [
    # Индийский океан
    {"lat_min": -30, "lat_max": 30, "lon_min": 40, "lon_max": 120},
    # Южно-Китайское море
    {"lat_min": 0, "lat_max": 25, "lon_min": 100, "lon_max": 125},
    # Восточно-Китайское море
    {"lat_min": 22, "lat_max": 35, "lon_min": 120, "lon_max": 130},
    # Филиппинское море
    {"lat_min": 10, "lat_max": 30, "lon_min": 125, "lon_max": 140},
    # Японское море
    {"lat_min": 33, "lat_max": 50, "lon_min": 125, "lon_max": 140},
    # Жёлтое море
    {"lat_min": 32, "lat_max": 40, "lon_min": 119, "lon_max": 125},
    # Берингово море
    {"lat_min": 50, "lat_max": 66, "lon_min": -170, "lon_max": -160},
    # Чукотское море
    {"lat_min": 66, "lat_max": 75, "lon_min": -180, "lon_max": -160},
    # Австралия (прибрежные воды)
    {"lat_min": -40, "lat_max": -10, "lon_min": 110, "lon_max": 155},
    # Тихий океан (западная часть)
    {"lat_min": -60, "lat_max": 70, "lon_min": 130, "lon_max": 180},
    {"lat_min": -60, "lat_max": 70, "lon_min": -180, "lon_max": -120},
]

def is_in_region(lat: float, lon: float) -> bool:
    """Проверяет, находится ли точка в одном из заданных регионов."""
    if lat is None or lon is None:
        return False
    for region in REGIONS:
        if (region["lat_min"] <= lat <= region["lat_max"] and
            region["lon_min"] <= lon <= region["lon_max"]):
            return True
    return False

# ---------- Словари (полные, как в предыдущих версиях) ----------
COUNTRY_CODES = { ... }  # Вставьте полный словарь
AIRCRAFT_NAMES = { ... } # Вставьте полный словарь
TARGET_CODES = { ... }   # Вставьте набор целевых кодов

# ---------- Логирование ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Вспомогательные функции ----------
def get_country_by_registration(registration: str) -> str:
    if not registration:
        return "🌍 Страна неизвестна"
    sorted_prefixes = sorted(COUNTRY_CODES.keys(), key=len, reverse=True)
    for prefix in sorted_prefixes:
        if registration.startswith(prefix):
            return COUNTRY_CODES[prefix]
    return "🌍 Страна неизвестна"

def format_coordinates(lat: float, lon: float) -> str:
    if lat is None or lon is None:
        return "📍 Координаты недоступны"
    try:
        lat_dir = "С" if lat >= 0 else "Ю"
        lon_dir = "В" if lon >= 0 else "З"
        return f"{abs(lat):.2f}°{lat_dir}, {abs(lon):.2f}°{lon_dir}"
    except TypeError:
        return "📍 Координаты недоступны"

def normalize_type(aircraft_type: str) -> str:
    if not aircraft_type:
        return ""
    return re.sub(r'[^A-Z0-9]', '', aircraft_type.upper())

def is_target_aircraft(aircraft_type: str) -> bool:
    if not aircraft_type:
        return False
    clean = normalize_type(aircraft_type)
    for code in TARGET_CODES:
        if code in clean:
            return True
    return False

# ---------- Загрузчик базы данных ICAO ----------
class AircraftDatabase:
    def __init__(self):
        self.data: Dict[str, Dict[str, str]] = {}
        self._loaded = False

    def load_sync(self):
        if self._loaded:
            return
        if not os.path.exists(Config.LOCAL_DB_FILE):
            logger.info("Скачиваю базу данных с Google Drive...")
            self._download_sync()
        else:
            logger.info("Загрузка базы из локального файла")
        self._load_from_file()
        self._loaded = True
        logger.info(f"База загружена: {len(self.data)} записей")

    def _download_sync(self):
        for attempt in range(1, Config.DB_RETRY_ATTEMPTS + 1):
            try:
                logger.info(f"Попытка {attempt} из {Config.DB_RETRY_ATTEMPTS} – скачивание с Google Drive")
                response = requests.get(
                    Config.DATABASE_URL,
                    stream=True,
                    timeout=Config.DB_DOWNLOAD_TIMEOUT,
                    allow_redirects=True
                )
                if response.status_code == 200:
                    with open(Config.LOCAL_DB_FILE, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    logger.info("База успешно скачана с Google Drive")
                    return
                else:
                    logger.warning(f"Google Drive ответил {response.status_code}, пробую fallback...")
                    break
            except Exception as e:
                logger.warning(f"Ошибка при скачивании с Google Drive (попытка {attempt}): {e}")
                if attempt < Config.DB_RETRY_ATTEMPTS:
                    import time
                    time.sleep(Config.DB_RETRY_DELAY * attempt)
                else:
                    logger.info("Попытка скачать с оригинального OpenSky...")
                    try:
                        response = requests.get(
                            Config.FALLBACK_DATABASE_URL,
                            stream=True,
                            timeout=Config.DB_DOWNLOAD_TIMEOUT,
                            allow_redirects=True
                        )
                        if response.status_code == 200:
                            with open(Config.LOCAL_DB_FILE, "wb") as f:
                                for chunk in response.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            logger.info("База скачана с OpenSky (fallback)")
                            return
                    except Exception as e2:
                        logger.error(f"Ошибка fallback: {e2}")

        logger.error("Не удалось скачать базу данных. Будет использована пустая база.")
        with open(Config.LOCAL_DB_FILE, "w") as f:
            f.write("icao24,registration,model\n")
        self.data = {}

    def _load_from_file(self):
        try:
            with open(Config.LOCAL_DB_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    icao = row.get("icao24", "").strip().lower()
                    if not icao:
                        continue
                    registration = row.get("registration", "").strip()
                    aircraft_type = row.get("model", "").strip()
                    self.data[icao] = {
                        "registration": registration if registration else "N/A",
                        "type": aircraft_type if aircraft_type else "N/A"
                    }
        except Exception as e:
            logger.error(f"Ошибка чтения базы: {e}")
            self.data = {}

    def get(self, icao: str) -> Optional[Dict[str, str]]:
        return self.data.get(icao.lower())

# ---------- Основной трекер ----------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked_aircrafts: Dict[str, Dict] = {}
        self.active_chats: set = set()
        self.chat_intervals: Dict[int, int] = {}

        # Инициализация OpenSky (OAuth2)
        self.opensky_api = None
        if OPENSKY_AVAILABLE and os.path.exists(Config.OPENSKY_CREDENTIALS):
            try:
                token_manager = TokenManager.from_json_file(Config.OPENSKY_CREDENTIALS)
                self.opensky_api = OpenSkyApi(token_manager=token_manager)
                logger.info("OpenSky OAuth2 инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации OpenSky OAuth2: {e}")
        else:
            logger.warning("OpenSky OAuth2 не доступен (нет credentials.json или библиотеки)")

        # Инициализация pyfr24 (если установлена)
        self.fr24_client = None
        if FR24_PY_AVAILABLE:
            try:
                self.fr24_client = FlightRadar24()
                logger.info("pyfr24 инициализирован")
            except Exception as e:
                logger.error(f"Ошибка инициализации pyfr24: {e}")

    def get_interval(self, chat_id: int) -> int:
        return self.chat_intervals.get(chat_id, Config.DEFAULT_INTERVAL)

    def set_interval(self, chat_id: int, interval_seconds: int):
        self.chat_intervals[chat_id] = interval_seconds

    # ---------- OpenSky (OAuth2) ----------
    async def fetch_opensky_oauth(self) -> List[Dict]:
        if not self.opensky_api:
            return []
        try:
            logger.info("🔄 OpenSky (OAuth2): запрос...")
            # Запрос всех состояний (можно добавить bounds для оптимизации)
            states = await asyncio.to_thread(self.opensky_api.get_states)
            if not states or not states.states:
                logger.info("OpenSky OAuth2: пустой ответ")
                return []
            aircrafts = []
            for s in states.states:
                icao = s.icao24.upper()
                if not icao:
                    continue
                # Пропускаем наземные
                if s.on_ground:
                    continue
                lat = s.latitude
                lon = s.longitude
                if not is_in_region(lat, lon):
                    continue
                aircraft = {
                    'icao': icao,
                    'registration': 'N/A',  # OpenSky не даёт регистрацию
                    'call_sign': s.callsign.strip() if s.callsign else 'N/A',
                    'type': 'N/A',  # OpenSky не даёт тип
                    'operator': 'N/A',
                    'lat': lat,
                    'lon': lon,
                    'altitude': s.baro_altitude or s.geo_altitude,
                    'speed': s.velocity,
                    'timestamp': datetime.now(),
                    'country': s.origin_country or 'Неизвестно',
                    'coordinates': format_coordinates(lat, lon)
                }
                aircrafts.append(aircraft)
            logger.info(f"OpenSky OAuth2: получено {len(aircrafts)} бортов в регионе")
            return aircrafts
        except Exception as e:
            logger.error(f"Ошибка OpenSky OAuth2: {e}")
            return []

    # ---------- ADS-B Exchange ----------
    async def fetch_adsb(self) -> List[Dict]:
        try:
            url = "https://data.adsbexchange.com/aircraft.json"
            headers = {
                "User-Agent": "MilitaryAircraftBot/1.0",
                "Accept": "application/json",
                "Referer": "https://globe.adsbexchange.com/"
            }
            logger.info("🔄 ADS-B Exchange: запрос...")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=20) as response:
                    if response.status != 200:
                        logger.warning(f"ADS-B Exchange: статус {response.status}")
                        return []
                    try:
                        json_data = await response.json(content_type=None)
                    except Exception as json_err:
                        text = await response.text()
                        logger.error(f"ADS-B Exchange: ошибка JSON: {json_err}, получено: {text[:200]}...")
                        return []
                    ac_list = json_data.get('ac', [])
                    if not ac_list:
                        logger.info("ADS-B Exchange: пустой список")
                        return []
                    aircrafts = []
                    for ac in ac_list:
                        if not isinstance(ac, dict):
                            continue
                        icao = ac.get('hex', '').upper()
                        if not icao:
                            continue
                        if ac.get('gnd', False):
                            continue
                        lat = ac.get('lat')
                        lon = ac.get('lon')
                        if not is_in_region(lat, lon):
                            continue
                        aircraft = {
                            'icao': icao,
                            'registration': ac.get('r', '').strip() or 'N/A',
                            'call_sign': ac.get('flight', '').strip() or 'N/A',
                            'type': ac.get('t', 'N/A'),
                            'operator': ac.get('ownOp', '').strip() or 'N/A',
                            'lat': lat,
                            'lon': lon,
                            'altitude': ac.get('alt'),
                            'speed': ac.get('speed'),
                            'timestamp': datetime.now(),
                            'country': get_country_by_registration(ac.get('r', '')),
                            'coordinates': format_coordinates(lat, lon)
                        }
                        aircrafts.append(aircraft)
                    logger.info(f"ADS-B Exchange: получено {len(aircrafts)} бортов в регионе")
                    return aircrafts
        except Exception as e:
            logger.error(f"Ошибка ADS-B Exchange: {e}")
            return []

    # ---------- Flightradar24 (pyfr24) ----------
    async def fetch_fr24_py(self) -> List[Dict]:
        if not self.fr24_client:
            return []
        try:
            logger.info("🔄 Flightradar24 (pyfr24): запрос...")
            # Получаем все рейсы (это может быть медленно, поэтому используем asyncio.to_thread)
            flights = await asyncio.to_thread(self.fr24_client.get_flights)
            if not flights:
                logger.info("pyfr24: пустой ответ")
                return []
            aircrafts = []
            for flight in flights:
                # pyfr24 возвращает объекты Flight
                icao = getattr(flight, 'id', '').upper()
                if not icao:
                    continue
                lat = getattr(flight, 'latitude', None)
                lon = getattr(flight, 'longitude', None)
                if not is_in_region(lat, lon):
                    continue
                aircraft = {
                    'icao': icao,
                    'registration': getattr(flight, 'registration', 'N/A') or 'N/A',
                    'call_sign': getattr(flight, 'callsign', 'N/A') or 'N/A',
                    'type': getattr(flight, 'type', 'N/A') or 'N/A',
                    'operator': getattr(flight, 'operator', 'N/A') or 'N/A',
                    'lat': lat,
                    'lon': lon,
                    'altitude': getattr(flight, 'altitude', None),
                    'speed': getattr(flight, 'speed', None),
                    'timestamp': datetime.now(),
                    'country': getattr(flight, 'origin_country', 'Неизвестно') or 'Неизвестно',
                    'coordinates': format_coordinates(lat, lon)
                }
                aircrafts.append(aircraft)
            logger.info(f"pyfr24: получено {len(aircrafts)} бортов в регионе")
            return aircrafts
        except Exception as e:
            logger.error(f"Ошибка pyfr24: {e}")
            return []

    # ---------- Основной мониторинг ----------
    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        aircrafts = []

        for source in Config.SOURCES:
            if source["type"] == "opensky_oauth":
                aircrafts = await self.fetch_opensky_oauth()
            elif source["type"] == "adsb":
                aircrafts = await self.fetch_adsb()
            elif source["type"] == "fr24_py":
                aircrafts = await self.fetch_fr24_py()
            else:
                continue

            if aircrafts:
                logger.info(f"✅ Использован источник: {source['name']}, получено {len(aircrafts)} бортов")
                await self.process_aircrafts(aircrafts, chat_id, context)
                return
            else:
                logger.info(f"❌ Источник {source['name']} не вернул данные, пробуем следующий...")

        logger.info("❌ Все источники данных не вернули самолёты в заданном регионе")

    async def process_aircrafts(self, aircrafts: List[Dict], chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> List[str]:
        new_detections = []

        for aircraft in aircrafts:
            icao = aircraft['icao']
            if icao in self.tracked_aircrafts:
                continue

            # Определяем тип из базы (если есть)
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
            logger.info(f"✅ Обнаружено {len(new_detections)} новых целей")
        else:
            logger.info("ℹ️ Новых целей не найдено")

        return new_detections

# ---------- Обработчики команд (без изменений) ----------
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
        "🛩 Мульти-трекер (OpenSky OAuth2, ADS-B, FR24)\n"
        "Отслеживание только в заданных регионах.\n"
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
        "🤖 *Мульти-трекер*\n\n"
        "Использует источники:\n"
        "- OpenSky (OAuth2) – требуется credentials.json\n"
        "- ADS-B Exchange (с Referer)\n"
        "- Flightradar24 (pyfr24) – если установлена\n\n"
        "Фильтрует по регионам:\n"
        "Индийский океан, Восточно-Китайское, Южно-Китайское,\n"
        "Филиппинское, Японское, Жёлтое, Берингово, Чукотское,\n"
        "Австралия, Тихий океан.\n\n"
        "Команды:\n"
        "/start — запустить мониторинг\n"
        "/help — справка\n"
        "/status — статус\n"
        "/stop — остановить\n"
        "/setinterval <сек> — установить интервал (мин. 15)",
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

# ---------- HTTP-Healthcheck ----------
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

# ---------- Запуск ----------
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
