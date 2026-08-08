import logging
import csv
import os
import asyncio
import re
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional
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

# ---------- Конфигурация ----------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    OPENSKY_URL = "https://opensky-network.org/api/states/all"
    DATABASE_URL = "https://drive.google.com/uc?export=download&id=1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"
    FALLBACK_DATABASE_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    LOCAL_DB_FILE = "aircraftDatabase.csv"
    DEFAULT_INTERVAL = 60
    MIN_INTERVAL = 15
    DB_DOWNLOAD_TIMEOUT = 90
    DB_RETRY_ATTEMPTS = 3
    DB_RETRY_DELAY = 5

# ---------- Географические регионы ----------
REGIONS = [
    {"lat_min": -30, "lat_max": 30, "lon_min": 40, "lon_max": 120},   # Индийский океан
    {"lat_min": 0, "lat_max": 25, "lon_min": 100, "lon_max": 125},    # Южно-Китайское море
    {"lat_min": 22, "lat_max": 35, "lon_min": 120, "lon_max": 130},   # Восточно-Китайское море
    {"lat_min": 10, "lat_max": 30, "lon_min": 125, "lon_max": 140},   # Филиппинское море
    {"lat_min": 33, "lat_max": 50, "lon_min": 125, "lon_max": 140},   # Японское море
    {"lat_min": 32, "lat_max": 40, "lon_min": 119, "lon_max": 125},   # Жёлтое море
    {"lat_min": 50, "lat_max": 66, "lon_min": -170, "lon_max": -160}, # Берингово море
    {"lat_min": 66, "lat_max": 75, "lon_min": -180, "lon_max": -160}, # Чукотское море
    {"lat_min": -40, "lat_max": -10, "lon_min": 110, "lon_max": 155}, # Австралия
    {"lat_min": -60, "lat_max": 70, "lon_min": 130, "lon_max": 180},  # Тихий океан (запад)
    {"lat_min": -60, "lat_max": 70, "lon_min": -180, "lon_max": -120},# Тихий океан (восток)
]

def is_in_region(lat: float, lon: float) -> bool:
    if lat is None or lon is None:
        return False
    for r in REGIONS:
        if r["lat_min"] <= lat <= r["lat_max"] and r["lon_min"] <= lon <= r["lon_max"]:
            return True
    return False

# ---------- Словари (полные, как в предыдущих версиях) ----------
COUNTRY_CODES = {
    'A2': '🇧🇼 Ботсвана', 'A3': '🇹🇴 Тонга', 'A4': '🇴🇲 Оман', 'A5': '🇧🇹 Бутан',
    'A6': '🇦🇪 ОАЭ', 'A7': '🇶🇦 Катар', 'A8': '🇱🇷 Либерия', 'A9': '🇧🇭 Бахрейн',
    'AP': '🇵🇰 Пакистан', 'B': '🇨🇳 Китай', 'C': '🇨🇦 Канада', 'CC': '🇨🇱 Чили',
    'CD': '🇨🇩 ДР Конго', 'CR': '🇨🇷 Коста-Рика', 'CU': '🇨🇺 Куба', 'CX': '🇺🇾 Уругвай',
    'D': '🇩🇪 Германия', 'DQ': '🇫🇯 Фиджи', 'DR': '🇳🇪 Нигер', 'EC': '🇪🇨 Эквадор',
    'EI': '🇮🇪 Ирландия', 'EK': '🇩🇰 Дания', 'EL': '🇱🇷 Либерия', 'EP': '🇮🇷 Иран',
    'ER': '🇲🇩 Молдова', 'ES': '🇪🇪 Эстония', 'ET': '🇩🇪 Германия (военные)',
    'EW': '🇬🇪 Грузия', 'EX': '🇰🇬 Кыргызстан', 'EY': '🇹🇯 Таджикистан', 'F': '🇫🇷 Франция',
    'G': '🇬🇧 Великобритания', 'H4': '🇸🇧 Соломоновы Острова', 'HA': '🇭🇺 Венгрия',
    'HB': '🇱🇮 Лихтенштейн', 'HL': '🇰🇷 Южная Корея', 'HP': '🇵🇦 Панама', 'HR': '🇭🇳 Гондурас',
    'HS': '🇹🇭 Таиланд', 'HU': '🇸🇻 Сальвадор', 'I': '🇮🇹 Италия', 'J': '🇯🇵 Япония',
    'JA': '🇯🇵 Япония', 'JY': '🇯🇴 Иордания', 'LN': '🇳🇴 Норвегия', 'LV': '🇦🇷 Аргентина',
    'LZ': '🇧🇬 Болгария', 'N': '🇺🇸 США', 'OB': '🇵🇪 Перу', 'OD': '🇱🇧 Ливан',
    'OE': '🇸🇦 Саудовская Аравия', 'OH': '🇫🇮 Финляндия', 'OK': '🇨🇿 Чехия',
    'OM': '🇸🇰 Словакия', 'OO': '🇧🇪 Бельгия', 'OY': '🇩🇰 Дания', 'P': '🇰🇵 Северная Корея',
    'PH': '🇳🇱 Нидерланды', 'PT': '🇧🇷 Бразилия', 'RA': '🇷🇺 Россия', 'RDPL': '🇱🇦 Лаос',
    'RP': '🇵🇭 Филиппины', 'SE': '🇸🇪 Швеция', 'SP': '🇵🇱 Польша', 'ST': '🇸🇩 Судан',
    'SU': '🇪🇬 Египет', 'SX': '🇬🇷 Греция', 'T7': '🇸🇲 Сан-Марино', 'TC': '🇹🇷 Турция',
    'TF': '🇮🇸 Исландия', 'TG': '🇬🇹 Гватемала', 'TI': '🇨🇷 Коста-Рика', 'TJ': '🇨🇲 Камерун',
    'TL': '🇨🇫 ЦАР', 'TR': '🇬🇦 Габон', 'TS': '🇹🇳 Тунис', 'TT': '🇨🇭 Швейцария',
    'TU': '🇨🇮 Кот-д\'Ивуар', 'TY': '🇧🇯 Бенин', 'TZ': '🇲🇱 Мали', 'UR': '🇺🇦 Украина',
    'V2': '🇦🇬 Антигуа и Барбуда', 'V3': '🇧🇿 Белиз', 'V4': '🇰🇳 Сент-Китс и Невис',
    'V5': '🇳🇦 Намибия', 'V6': '🇫🇲 Микронезия', 'V7': '🇲🇭 Маршалловы Острова',
    'V8': '🇧🇳 Бруней', 'XA': '🇲🇽 Мексика', 'XT': '🇧🇫 Буркина-Фасо', 'XY': '🇲🇲 Мьянма',
    'XZ': '🇲🇳 Монголия', 'YA': '🇦🇫 Афганистан', 'YI': '🇮🇶 Ирак', 'YJ': '🇻🇺 Вануату',
    'YK': '🇸🇾 Сирия', 'YL': '🇱🇻 Латвия', 'YN': '🇳🇮 Никарагуа', 'YR': '🇷🇴 Румыния',
    'YS': '🇸🇻 Сальвадор', 'YU': '🇷🇸 Сербия', 'YV': '🇻🇪 Венесуэла', 'Z': '🇿🇦 ЮАР',
    'ZA': '🇦🇱 Албания', 'ZK': '🇳🇿 Новая Зеландия', 'ZP': '🇵🇾 Парагвай', 'ZS': '🇿🇦 ЮАР',
    'ZT': '🇿🇲 Замбия', 'ZU': '🇿🇼 Зимбабве', '3B': '🇲🇺 Маврикий', '3C': '🇬🇶 Экв. Гвинея',
    '3D': '🇸🇿 Эсватини', '3X': '🇬🇳 Гвинея', '4K': '🇦🇿 Азербайджан', '4R': '🇱🇰 Шри-Ланка',
    '4X': '🇮🇱 Израиль', '5A': '🇱🇾 Ливия', '5B': '🇨🇾 Кипр', '5H': '🇹🇿 Танзания',
    '5N': '🇳🇬 Нигерия', '5R': '🇲🇬 Мадагаскар', '5T': '🇲🇷 Мавритания', '5U': '🇳🇪 Нигер',
    '5V': '🇹🇬 Того', '5X': '🇺🇬 Уганда', '6O': '🇸🇴 Сомали', '6V': '🇸🇳 Сенегал',
    '6W': '🇸🇸 Южный Судан', '7O': '🇾🇪 Йемен', '7P': '🇱🇸 Лесото', '7Q': '🇲🇼 Малави',
    '7T': '🇩🇿 Алжир', '8P': '🇧🇧 Барбадос', '8Q': '🇲🇻 Мальдивы', '8R': '🇬🇾 Гайана',
    '9A': '🇭🇷 Хорватия', '9G': '🇬🇭 Гана', '9H': '🇲🇹 Мальта', '9J': '🇿🇲 Замбия',
    '9K': '🇰🇼 Кувейт', '9L': '🇸🇱 Сьерра-Леоне', '9M': '🇲🇾 Малайзия', '9N': '🇳🇵 Непал',
    '9Q': '🇨🇩 ДР Конго', '9U': '🇧🇮 Бурунди', '9V': '🇸🇬 Сингапур', '9XR': '🇷🇼 Руанда',
    'C2': '🇳🇷 Науру', 'D2': '🇦🇴 Ангола', 'D4': '🇨🇻 Кабо-Верде', 'E3': '🇪🇷 Эритрея',
    'E5': '🇨🇰 Острова Кука', 'HZ': '🇸🇦 Саудовская Аравия', 'J2': '🇩🇯 Джибути',
    'J3': '🇬🇩 Гренада', 'S7': '🇸🇨 Сейшелы', 'T9': '🇧🇦 Босния и Герцеговина',
    'UP': '🇰🇿 Казахстан', 'VH': '🇦🇺 Австралия', 'VP-B': '🇧🇲 Бермуды',
    'VP-L': '🇲🇴 Макао', 'VQ-H': '🇬🇬 Гернси', 'VQ-T': '🇹🇨 Теркс и Кайкос',
    'Z3': '🇲🇰 Северная Македония'
}
  # Вставьте полный словарь
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
} # Вставьте полный словарь
TARGET_CODES = {
    'C130', 'KC130', 'MC130', 'C17', 'C5', 'C2',
    'KC135', 'KC10', 'KC46', 'DC10', 'A400M',
    'P1', 'CP140', 'F16', 'F15', 'F22', 'F35', 'F18',
    'EA18G', 'B1', 'B2', 'B52', 'E3', 'E2', 'E8', 'E7',
    'E4', 'E6', 'E767', 'P3', 'P8', 'U2', 'RC135',
    'C30', 'K35R', 'R135', 'C30J', 'C5M'
}   # Вставьте набор целевых кодов

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
    for prefix in sorted(COUNTRY_CODES.keys(), key=len, reverse=True):
        if registration.startswith(prefix):
            return COUNTRY_CODES[prefix]
    return "🌍 Страна неизвестна"

def format_coordinates(lat: float, lon: float) -> str:
    if lat is None or lon is None:
        return "📍 Координаты недоступны"
    lat_dir = "С" if lat >= 0 else "Ю"
    lon_dir = "В" if lon >= 0 else "З"
    return f"{abs(lat):.2f}°{lat_dir}, {abs(lon):.2f}°{lon_dir}"

def normalize_type(t: str) -> str:
    if not t:
        return ""
    return re.sub(r'[^A-Z0-9]', '', t.upper())

def is_target(t: str) -> bool:
    if not t:
        return False
    clean = normalize_type(t)
    for code in TARGET_CODES:
        if code in clean:
            return True
    return False

# ---------- Загрузчик базы ICAO ----------
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
                resp = requests.get(Config.DATABASE_URL, stream=True, timeout=Config.DB_DOWNLOAD_TIMEOUT, allow_redirects=True)
                if resp.status_code == 200:
                    with open(Config.LOCAL_DB_FILE, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    logger.info("База успешно скачана с Google Drive")
                    return
                else:
                    logger.warning(f"Google Drive ответил {resp.status_code}, пробую fallback...")
                    break
            except Exception as e:
                logger.warning(f"Ошибка при скачивании с Google Drive (попытка {attempt}): {e}")
                if attempt < Config.DB_RETRY_ATTEMPTS:
                    import time
                    time.sleep(Config.DB_RETRY_DELAY * attempt)
                else:
                    logger.info("Попытка скачать с оригинального OpenSky...")
                    try:
                        resp = requests.get(Config.FALLBACK_DATABASE_URL, stream=True, timeout=Config.DB_DOWNLOAD_TIMEOUT, allow_redirects=True)
                        if resp.status_code == 200:
                            with open(Config.LOCAL_DB_FILE, "wb") as f:
                                for chunk in resp.iter_content(chunk_size=8192):
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
                    reg = row.get("registration", "").strip()
                    typ = row.get("model", "").strip()
                    self.data[icao] = {
                        "registration": reg if reg else "N/A",
                        "type": typ if typ else "N/A"
                    }
        except Exception as e:
            logger.error(f"Ошибка чтения базы: {e}")
            self.data = {}

    def get(self, icao: str) -> Optional[Dict[str, str]]:
        return self.data.get(icao.lower())

# ---------- Основной трекер (только OpenSky HTTP) ----------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked: Dict[str, Dict] = {}
        self.active_chats: set = set()
        self.chat_intervals: Dict[int, int] = {}

    def get_interval(self, chat_id: int) -> int:
        return self.chat_intervals.get(chat_id, Config.DEFAULT_INTERVAL)

    def set_interval(self, chat_id: int, interval_seconds: int):
        self.chat_intervals[chat_id] = interval_seconds

    async def fetch_opensky(self) -> List[Dict]:
        try:
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            timeout = aiohttp.ClientTimeout(total=45, connect=15)
            logger.info("🔄 OpenSky: запрос...")
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(Config.OPENSKY_URL, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                    if resp.status != 200:
                        logger.warning(f"OpenSky статус {resp.status}")
                        return []
                    data = await resp.json()
                    if 'states' not in data:
                        return []
                    result = []
                    for s in data['states']:
                        if not s or len(s) < 8:
                            continue
                        if s[8]:  # on_ground
                            continue
                        lat, lon = s[6], s[5]
                        if not is_in_region(lat, lon):
                            continue
                        result.append({
                            'icao': s[0].upper(),
                            'registration': 'N/A',
                            'call_sign': s[1].strip() if s[1] else 'N/A',
                            'type': 'N/A',
                            'operator': 'N/A',
                            'lat': lat,
                            'lon': lon,
                            'altitude': s[7],
                            'speed': s[9],
                            'timestamp': datetime.now(),
                            'country': s[2] or 'Неизвестно',
                            'coordinates': format_coordinates(lat, lon)
                        })
                    logger.info(f"OpenSky: {len(result)} бортов в регионе")
                    return result
        except asyncio.TimeoutError:
            logger.error("OpenSky: таймаут соединения")
            return []
        except Exception as e:
            logger.error(f"OpenSky ошибка: {e}")
            return []

    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        aircrafts = await self.fetch_opensky()
        if aircrafts:
            logger.info(f"✅ Получено {len(aircrafts)} бортов из OpenSky")
            await self.process(aircrafts, chat_id, context)
        else:
            logger.info("❌ OpenSky не вернул данные")

    async def process(self, aircrafts: List[Dict], chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        new = []
        for ac in aircrafts:
            icao = ac['icao']
            if icao in self.tracked:
                continue
            db_entry = self.db.get(icao)
            if db_entry:
                ac['type'] = db_entry['type']
                ac['registration'] = db_entry['registration']
            else:
                ac['type'] = 'N/A'
                ac['registration'] = 'N/A'
            if ac['type'] == 'N/A' or not is_target(ac['type']):
                continue
            self.tracked[icao] = ac
            type_name = AIRCRAFT_NAMES.get(normalize_type(ac['type']), ac['type'])
            msg = (
                "🚨 Самолет обнаружен!\n"
                f"🕒 Время: {ac['timestamp'].strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"▫️ ICAO: {icao}\n"
                f"▫️ Регистрация: {ac['registration'] or 'N/A'}\n"
                f"▫️ Позывной: {ac['call_sign'] or 'N/A'}\n"
                f"▫️ Тип: {type_name}\n"
                f"▫️ Страна: {ac['country']}\n"
                f"▫️ Координаты: {ac['coordinates']}"
            )
            await context.bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
            new.append(icao)
        if new:
            logger.info(f"✅ Обнаружено {len(new)} новых целей")
        else:
            logger.info("ℹ️ Новых целей не найдено")

# ---------- Обработчики команд ----------
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
    buttons = [[KeyboardButton(f"{s} сек")] for s in [30, 60, 120, 300, 600]]
    buttons.append(["🔙 Назад"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)

async def _start_monitoring(chat_id: int, context: ContextTypes.DEFAULT_TYPE, interval: Optional[int] = None):
    if context.job_queue is None:
        raise RuntimeError("JobQueue не доступен.")
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
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
        "🛩 Трекер (OpenSky)\n"
        "Отслеживание в заданных регионах.\n"
        "Автоматически запускаю мониторинг...",
        reply_markup=get_main_keyboard()
    )
    try:
        started = await _start_monitoring(chat_id, context)
        if started:
            interval = tracker.get_interval(chat_id)
            await update.message.reply_text(f"✅ Мониторинг активен (интервал: {interval} сек.)")
        else:
            await update.message.reply_text("⚠️ Мониторинг уже запущен.")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Трекер OpenSky*\n\n"
        "Использует OpenSky Network (анонимный доступ).\n"
        "Фильтрует по регионам (Индийский океан, Южно-Китайское море, ...).\n\n"
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
        f"🔍 Отслежено бортов: {len(tracker.tracked)}\n"
        f"⏱ Интервал: {interval} сек.\n"
        f"🟢 Мониторинг: {'активен' if is_active else 'остановлен'}\n"
        f"⏳ Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        reply_markup=get_main_keyboard()
    )

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        started = await _start_monitoring(chat_id, context)
        if started:
            interval = tracker.get_interval(chat_id)
            await update.message.reply_text(f"✅ Мониторинг запущен (интервал {interval} сек.).", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text("⚠️ Мониторинг уже активен.", reply_markup=get_main_keyboard())
    except Exception as e:
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
        tracker.tracked.clear()
    await update.message.reply_text("⛔ Мониторинг остановлен", reply_markup=get_main_keyboard())

async def interval_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current = tracker.get_interval(chat_id)
    await update.message.reply_text(
        f"⚙️ *Настройка интервала*\n\nТекущий: {current} сек.\nВыберите новый:",
        parse_mode="Markdown",
        reply_markup=get_interval_keyboard()
    )

async def handle_interval_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    if text == "🔙 Назад":
        await update.message.reply_text("Главное меню", reply_markup=get_main_keyboard())
        return
    try:
        new_interval = int(text.split()[0])
        if new_interval < Config.MIN_INTERVAL:
            await update.message.reply_text(f"Минимальный интервал – {Config.MIN_INTERVAL} сек.")
            return
        tracker.set_interval(chat_id, new_interval)
        if chat_id in tracker.active_chats:
            await _start_monitoring(chat_id, context, new_interval)
            await update.message.reply_text(f"✅ Интервал изменён на {new_interval} сек. Мониторинг перезапущен.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(f"✅ Интервал сохранён ({new_interval} сек.). Запустите мониторинг.", reply_markup=get_main_keyboard())
    except Exception:
        await update.message.reply_text("Неверный формат. Используйте кнопки.", reply_markup=get_interval_keyboard())

async def set_interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Укажите интервал в секундах, например: /setinterval 30")
        return
    try:
        seconds = int(context.args[0])
        if seconds < Config.MIN_INTERVAL:
            await update.message.reply_text(f"Минимальный интервал – {Config.MIN_INTERVAL} сек.")
            return
        tracker.set_interval(chat_id, seconds)
        if chat_id in tracker.active_chats:
            await _start_monitoring(chat_id, context, seconds)
            await update.message.reply_text(f"✅ Интервал изменён на {seconds} сек. Мониторинг перезапущен.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(f"✅ Интервал сохранён ({seconds} сек.). Запустите мониторинг.", reply_markup=get_main_keyboard())
    except ValueError:
        await update.message.reply_text("Введите число секунд.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используйте кнопки ⬇️", reply_markup=get_main_keyboard())

# ---------- Healthcheck ----------
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
    threading.Thread(target=run_health_server, args=(8080,), daemon=True).start()
    db = AircraftDatabase()
    db.load_sync()
    tracker = AircraftTracker(db)
    app = Application.builder().token(Config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("monitor", start_monitoring))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(CommandHandler("setinterval", set_interval_command))
    app.add_handler(MessageHandler(filters.Text("🟢 Запустить мониторинг"), start_monitoring))
    app.add_handler(MessageHandler(filters.Text("🔴 Остановить"), stop_monitoring))
    app.add_handler(MessageHandler(filters.Text("📊 Статус"), status))
    app.add_handler(MessageHandler(filters.Text("⚙️ Интервал"), interval_settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_interval_choice))
    app.add_handler(MessageHandler(filters.ALL, unknown))
    logger.info("🚀 Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
