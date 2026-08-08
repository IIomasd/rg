import logging
import csv
import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Правильный импорт библиотеки Flightradar24
from FlightRadarAPI import FlightRadar24API

# -------------------- КОНФИГУРАЦИЯ --------------------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    DATABASE_URL = "https://drive.google.com/uc?export=download&id=1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"
    FALLBACK_DATABASE_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    LOCAL_DB_FILE = "aircraftDatabase.csv"
    DEFAULT_INTERVAL = 600
    DB_DOWNLOAD_TIMEOUT = 90
    DB_RETRY_ATTEMPTS = 3
    DB_RETRY_DELAY = 5

# -------------------- ДАННЫЕ (без изменений) --------------------
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

# -------------------- ЗАГРУЗЧИК БАЗЫ --------------------
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
        import requests
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

# -------------------- ОСНОВНОЙ КЛАСС ТРЕКЕРА (Flightradar24) --------------------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked_aircrafts: Dict[str, Dict] = {}
        self.active_chats: set = set()
        self.chat_intervals: Dict[int, int] = {}
        self.fr_api = FlightRadar24API()

    def get_interval(self, chat_id: int) -> int:
        return self.chat_intervals.get(chat_id, Config.DEFAULT_INTERVAL)

    def set_interval(self, chat_id: int, interval_seconds: int):
        self.chat_intervals[chat_id] = interval_seconds

    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        start_time = datetime.now()

        try:
            logger.info("📡 Запрос к Flightradar24...")
            # Получаем список всех активных рейсов
            flights = await asyncio.to_thread(self.fr_api.get_flights)
            
            if not flights:
                logger.info("ℹ️ Flightradar24 не вернул рейсов")
                return

            logger.info(f"✈️ Получено {len(flights)} рейсов от Flightradar24")
            target_count = 0

            for flight in flights:
                # Извлекаем ICAO (в библиотеке это поле 'id')
                icao = getattr(flight, 'id', '').upper()
                if not icao:
                    continue

                # Пропускаем уже отслеженные
                if icao in self.tracked_aircrafts:
                    continue

                # Получаем тип ВС из базы по ICAO
                db_entry = self.db.get(icao)
                if db_entry:
                    aircraft_type = db_entry['type']
                    registration = db_entry['registration']
                else:
                    aircraft_type = "N/A"
                    registration = "N/A"

                # Если тип не определён, пытаемся получить его напрямую из flight
                if aircraft_type == "N/A":
                    # В объекте Flight может быть поле 'type' или 'aircraft_type'
                    aircraft_type = getattr(flight, 'type', 'N/A') or 'N/A'

                # Фильтрация по типу
                if not is_target_aircraft(aircraft_type):
                    continue

                target_count += 1
                logger.info(f"🎯 Найден военный: {icao} ({aircraft_type})")

                # Собираем данные для отправки
                callsign = getattr(flight, 'callsign', 'N/A') or 'N/A'
                country = getattr(flight, 'origin_country', 'Неизвестно') or 'Неизвестно'
                lat = getattr(flight, 'latitude', None)
                lon = getattr(flight, 'longitude', None)

                aircraft = {
                    'icao': icao,
                    'call_sign': callsign,
                    'country': country,
                    'lat': lat,
                    'lon': lon,
                    'timestamp': datetime.now(),
                    'registration': registration,
                    'type': aircraft_type,
                    'coordinates': format_coordinates(lat, lon)
                }

                self.tracked_aircrafts[icao] = aircraft

                clean_type = normalize_type(aircraft_type)
                type_name = AIRCRAFT_NAMES.get(clean_type, aircraft_type if aircraft_type != "N/A" else "Неизвестен")

                message = (
                    "🚨 Военный самолет обнаружен!\n"
                    f"🕒 Время: {aircraft['timestamp'].strftime('%d.%m.%Y %H:%M:%S')}\n"
                    f"▫️ ICAO: {icao}\n"
                    f"▫️ Позывной: {callsign}\n"
                    f"▫️ Регистрация: {registration}\n"
                    f"▫️ Тип: {type_name}\n"
                    f"▫️ Страна: {country}\n"
                    f"▫️ Координаты: {aircraft['coordinates']}"
                )

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    disable_web_page_preview=True
                )
                logger.info(f"✅ Обнаружение отправлено: {icao} ({type_name})")

            if target_count == 0:
                logger.info("ℹ️ Военных целей не найдено в текущем наборе")

        except Exception as e:
            logger.error(f"❌ Ошибка при запросе к Flightradar24: {e}", exc_info=True)

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
        "🛩 Военный авиационный трекер (Flightradar24)\n"
        "Отслеживание военных самолётов.\n"
        "Автоматически запускаю мониторинг...",
        reply_markup=get_main_keyboard()
    )
    try:
        started = await _start_monitoring_for_chat(chat_id, context)
        if started:
            interval = tracker.get_interval(chat_id)
            await update.message.reply_text(f"✅ Мониторинг активен (интервал: {interval//60} мин.)")
        else:
            await update.message.reply_text("⚠️ Мониторинг уже запущен.")
    except RuntimeError as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Военный авиационный трекер*\n\n"
        "Бот отслеживает военные самолёты по данным Flightradar24.\n"
        "Фильтрация по типу из списка целевых.\n"
        "При обнаружении приходит уведомление.\n\n"
        "*Команды:*\n"
        "/start — запустить мониторинг\n"
        "/help — справка\n"
        "/status — статус\n"
        "/stop — остановить\n"
        "/setinterval <сек> — установить интервал (число секунд)",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    interval = tracker.get_interval(chat_id)
    is_active = chat_id in tracker.active_chats
    await update.message.reply_text(
        f"🔍 Отслежено бортов: {len(tracker.tracked_aircrafts)}\n"
        f"⏱ Интервал: {interval//60} мин.\n"
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
            await update.message.reply_text(f"✅ Мониторинг запущен (интервал {interval//60} мин.).", reply_markup=get_main_keyboard())
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
        f"Текущий интервал: *{current//60} мин.*\n\n"
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
                    f"✅ Интервал изменён на {new_interval//60} мин. Мониторинг перезапущен.",
                    reply_markup=get_main_keyboard()
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка при перезапуске: {e}", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(
                f"✅ Интервал сохранён ({new_interval//60} мин.). Запустите мониторинг для применения.",
                reply_markup=get_main_keyboard()
            )
    else:
        await update.message.reply_text("Неизвестный вариант. Используйте кнопки.", reply_markup=get_interval_keyboard())

async def set_interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Укажите интервал в секундах, например: /setinterval 300")
        return
    try:
        seconds = int(context.args[0])
        if seconds < 30:
            await update.message.reply_text("Минимальный интервал – 30 секунд.")
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
