import logging
import csv
import os
import json
import asyncio
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import aiohttp
import requests  # <-- добавлено
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# -------------------- КОНФИГУРАЦИЯ --------------------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    API_URL = "https://opensky-network.org/api/states/all"
    # Используем Google Drive прямую ссылку
    DATABASE_URL = "https://drive.google.com/uc?export=download&id=1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"
    FALLBACK_DATABASE_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    LOCAL_DB_FILE = "aircraftDatabase.csv"
    MONITOR_INTERVAL = 30
    REQUEST_TIMEOUT = 120
    DB_DOWNLOAD_TIMEOUT = 90
    DB_RETRY_ATTEMPTS = 3
    DB_RETRY_DELAY = 5

# -------------------- ДАННЫЕ --------------------
AIRCRAFT_NAMES = {
    'B52': 'B-52 Stratofortress',
    'C17': 'C-17 Globemaster III',
    'F16': 'F-16 Fighting Falcon',
    'F35': 'F-35 Lightning II',
    'KC135': 'KC-135 Stratotanker',
    'KC10': 'KC-10 Extender',
    'E3': 'E-3 Sentry',
    'U2': 'U-2 Dragon Lady',
    'RC135': 'RC-135 Rivet Joint',
    'C130': 'C-130 Hercules',
    'A400M': 'A400M Atlas',
    'P8': 'P-8 Poseidon',
    'C5': 'C-5 Galaxy',
    'C2': 'C-2 Greyhound',
    'KC46': 'KC-46 Pegasus',
    'DC10': 'DC-10',
    'P1': 'P-1',
    'CP140': 'CP-140 Aurora',
    'F15': 'F-15 Eagle',
    'F22': 'F-22 Raptor',
    'F18': 'F/A-18 Hornet',
    'EA18G': 'EA-18G Growler',
    'B1': 'B-1 Lancer',
    'B2': 'B-2 Spirit',
    'E2': 'E-2 Hawkeye',
    'E7': 'E-7 Wedgetail',
    'E4': 'E-4 Nightwatch',
    'E6': 'E-6 Mercury',
    'E767': 'E-767',
    'P3': 'P-3 Orion',
    'E2C': 'E-2C Hawkeye',
    'E2K': 'E-2K Hawkeye',
    'E737': 'E-737 Wedgetail',
    'C2A': 'C-2A Greyhound',
    'K35R': 'KC-135R Stratotanker',
    'R135': 'RC-135',
    'C30': 'C-30',
    'C30J': 'C-30J',
    'C5M': 'C-5M Super Galaxy',
    'E3TF': 'E-3 Sentry (Турция)',
    'C17A': 'C-17A Globemaster III',
    'KC135R': 'KC-135R Stratotanker',
    'KC135T': 'KC-135T Stratotanker',
    'KC10A': 'KC-10A Extender',
    'KC46A': 'KC-46A Pegasus',
    'F16C': 'F-16C Fighting Falcon',
    'F15E': 'F-15E Strike Eagle',
    'F22A': 'F-22A Raptor',
    'F35A': 'F-35A Lightning II',
    'F35B': 'F-35B Lightning II',
    'F35C': 'F-35C Lightning II',
    'B1B': 'B-1B Lancer',
    'B2A': 'B-2A Spirit',
    'E3G': 'E-3G Sentry',
    'E2D': 'E-2D Advanced Hawkeye',
    'P8A': 'P-8A Poseidon',
    'MC130': 'MC-130',
    'KC130': 'KC-130',
    'KC130J': 'KC-130J'
}

TARGET_TYPES = {
    'exact': {
        'C130', 'KC130', 'MC130', 'KC130J', 'C17', 'C5',
        'C2', 'KC135', 'KC10', 'KC46', 'DC10', 'A400M',
        'P1', 'CP140', 'F16', 'F15', 'F22', 'F35', 'F18',
        'EA18G', 'B1', 'B2', 'B52', 'E3', 'E2', 'E8', 'E7',
        'E4', 'E6', 'E767', 'P3', 'P8', 'U2', 'RC135', 'E2C',
        'E2K', 'E737', 'C2A', 'K35R', 'R135', 'C30', 'C30J',
        'C5M', 'E3TF'
    },
    'partial': {
        'C17A', 'KC135R', 'KC135T', 'KC10A', 'KC46A',
        'F16C', 'F15E', 'F22A', 'F35A', 'F35B', 'F35C',
        'EA18G', 'B1B', 'B2A', 'B52', 'E3G', 'E2D', 'P8A', 'MC130',
        'K35R', 'R135', 'C30', 'C30J', 'E3TF'
    }
}

# -------------------- ЛОГИРОВАНИЕ --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
def format_coordinates(lat: Optional[float], lon: Optional[float]) -> str:
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
    return aircraft_type.replace("-", "").replace(" ", "").replace("_", "")

def is_target_aircraft(aircraft_type: str) -> bool:
    if not aircraft_type:
        return False
    clean = normalize_type(aircraft_type)
    if clean in TARGET_TYPES['exact']:
        return True
    for pattern in TARGET_TYPES['partial']:
        if pattern in clean:
            return True
    return False

# -------------------- ЗАГРУЗЧИК БАЗЫ (с повторными попытками) --------------------
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
        """Скачивание с повторными попытками, сначала Google Drive, потом fallback."""
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
                    break  # переходим к fallback
            except (requests.RequestException, OSError) as e:
                logger.warning(f"Ошибка при скачивании с Google Drive (попытка {attempt}): {e}")
                if attempt < Config.DB_RETRY_ATTEMPTS:
                    import time
                    time.sleep(Config.DB_RETRY_DELAY * attempt)
                else:
                    # Пробуем fallback
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
                        else:
                            logger.error(f"Fallback вернул {response.status_code}")
                    except Exception as e2:
                        logger.error(f"Ошибка fallback: {e2}")

        # Если все попытки провалились
        logger.error("Не удалось скачать базу данных. Будет использована пустая база.")
        # Создаём пустой файл, чтобы не пытаться снова при следующем запуске
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

# -------------------- ОСНОВНОЙ КЛАСС ТРЕКЕРА --------------------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked_aircrafts: Dict[str, Dict] = {}
        self.active_chats: set = set()

    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        try:
            timeout = aiohttp.ClientTimeout(total=Config.REQUEST_TIMEOUT, connect=30, sock_read=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info(f"📡 Запрос к {Config.API_URL}")
                async with session.get(Config.API_URL) as response:
                    logger.info(f"📊 Статус ответа: {response.status}")
                    response.raise_for_status()
                    logger.info("⏳ Читаю и парсю JSON...")
                    data = await response.json()
                    logger.info("✅ JSON распарсен")

                    if 'states' not in data or not data['states']:
                        logger.info("ℹ️ Список самолётов пуст")
                        return

                    states = data['states']
                    logger.info(f"✈️ Получено самолётов: {len(states)}")

                    for state in states:
                        aircraft = self.parse_aircraft(state)
                        if not aircraft:
                            continue

                        icao = aircraft['icao']
                        if icao in self.tracked_aircrafts:
                            continue

                        db_entry = self.db.get(icao)
                        if db_entry:
                            aircraft_type = db_entry['type']
                            registration = db_entry['registration']
                        else:
                            aircraft_type = "N/A"
                            registration = "N/A"

                        if not is_target_aircraft(aircraft_type):
                            continue

                        aircraft['registration'] = registration
                        aircraft['type'] = aircraft_type
                        self.tracked_aircrafts[icao] = aircraft
                        aircraft['coordinates'] = format_coordinates(aircraft['lat'], aircraft['lon'])

                        clean_type = normalize_type(aircraft_type)
                        type_name = AIRCRAFT_NAMES.get(clean_type, aircraft_type if aircraft_type != "N/A" else "Неизвестен")

                        message = (
                            "🚨 Военный самолет обнаружен!\n"
                            f"🕒 Время: {aircraft['timestamp'].strftime('%d.%m.%Y %H:%M:%S')}\n"
                            f"▫️ ICAO: {icao}\n"
                            f"▫️ Позывной: {aircraft['call_sign']}\n"
                            f"▫️ Регистрация: {registration}\n"
                            f"▫️ Тип: {type_name}\n"
                            f"▫️ Страна: {aircraft['country']}\n"
                            f"▫️ Координаты: {aircraft['coordinates']}"
                        )

                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            disable_web_page_preview=True
                        )
                        logger.info(f"✅ Обнаружение: {icao} ({type_name})")

        except asyncio.TimeoutError:
            logger.warning("⏳ Таймаут при запросе к OpenSky (повтор в следующем цикле)")
        except aiohttp.ClientError as e:
            logger.error(f"🌐 Ошибка HTTP: {e}")
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга: {e}", exc_info=True)

    def parse_aircraft(self, state: List) -> Optional[Dict]:
        if not isinstance(state, list) or len(state) < 7:
            return None
        icao = state[0] or 'N/A'
        if icao == 'N/A':
            return None

        on_ground = state[8] if len(state) > 8 else None
        if on_ground:
            return None

        callsign = (state[1] or '').strip()
        if not callsign:
            callsign = 'N/A'
        country = (state[2] or '').strip() or 'Неизвестно'
        longitude = state[5] if len(state) > 5 else None
        latitude = state[6] if len(state) > 6 else None

        return {
            'icao': icao,
            'call_sign': callsign,
            'country': country,
            'lat': latitude,
            'lon': longitude,
            'timestamp': datetime.now(),
            'registration': 'N/A',
            'type': 'N/A'
        }

# -------------------- ОБРАБОТЧИКИ КОМАНД --------------------
tracker = None

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [["🟢 Запустить мониторинг", "🔴 Остановить", "📊 Статус"]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие"
    )

async def _start_monitoring_for_chat(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if jobs:
        return False
    context.job_queue.run_repeating(
        tracker.monitor,
        interval=timedelta(seconds=Config.MONITOR_INTERVAL),
        first=5,
        chat_id=chat_id,
        name=str(chat_id)
    )
    tracker.active_chats.add(chat_id)
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🛩 Военный авиационный трекер\n"
        "Отслеживание военных самолётов по типу (OpenSky).\n"
        "Автоматически запускаю мониторинг...",
        reply_markup=get_main_keyboard()
    )
    started = await _start_monitoring_for_chat(chat_id, context)
    if started:
        await update.message.reply_text(f"✅ Мониторинг активен (каждые {Config.MONITOR_INTERVAL} сек.)")
    else:
        await update.message.reply_text("⚠️ Мониторинг уже запущен.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Военный авиационный трекер*\n\n"
        "Бот отслеживает военные самолёты по данным OpenSky Network.\n"
        "Фильтрация по типу из списка целевых (B-52, F-16, C-17 и др.).\n"
        "При обнаружении приходит уведомление с регистрацией и типом.\n\n"
        "*Команды:*\n"
        "/start — запустить мониторинг\n"
        "/help — справка\n"
        "/status — статус\n"
        "/stop — остановить",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🔍 Отслежено бортов: {len(tracker.tracked_aircrafts)}\n"
        f"⏱ Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        reply_markup=get_main_keyboard()
    )

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    started = await _start_monitoring_for_chat(chat_id, context)
    if started:
        await update.message.reply_text("✅ Мониторинг запущен.", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("⚠️ Мониторинг уже активен.", reply_markup=get_main_keyboard())

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
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

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используйте кнопки ⬇️", reply_markup=get_main_keyboard())

# -------------------- HTTP-HEALTHCHECK ДЛЯ RAILWAY --------------------
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

    application.add_handler(MessageHandler(filters.Text("🟢 Запустить мониторинг"), start_monitoring))
    application.add_handler(MessageHandler(filters.Text("🔴 Остановить"), stop_monitoring))
    application.add_handler(MessageHandler(filters.Text("📊 Статус"), status))
    application.add_handler(MessageHandler(filters.ALL, unknown_command))

    logger.info("🚀 Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    main()
