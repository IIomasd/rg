import logging
import csv
import os
import asyncio
import re
import socket
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
import requests
import aiohttp
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------- Импорт FlightRadarAPI (опционально) ----------
try:
    from FlightRadarAPI import FlightRadar24API
    FR24_API_AVAILABLE = True
except ImportError:
    FR24_API_AVAILABLE = False

# ---------- Конфигурация ----------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    # Прокси (можно заменить или убрать)
    PROXY = "http://bjumcuxv:lodgiq7akwo7@31.59.20.176:6754"

    OPENSKY_URL = "https://opensky-network.org/api/states/all"

    ADSB_ENDPOINTS = [
        "https://opendata.adsb.fi/api/v3/aircraft",
        "https://api.adsb.fi/api/v3/aircraft",
        "https://airplanes.live/v1/aircraft",
    ]

    DATABASE_URL = "https://drive.google.com/uc?export=download&id=1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"
    FALLBACK_DATABASE_URL = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    LOCAL_DB_FILE = "aircraftDatabase.csv"
    DEFAULT_INTERVAL = 60
    MIN_INTERVAL = 15
    DB_DOWNLOAD_TIMEOUT = 90
    DB_RETRY_ATTEMPTS = 3
    DB_RETRY_DELAY = 5

# ---------- Список регионов (для выбора) ----------
REGIONS = {
    "indian_ocean": {"name": "🇮🇳 Индийский океан", "lat_min": -30, "lat_max": 30, "lon_min": 40, "lon_max": 120},
    "south_china_sea": {"name": "🇨🇳 Южно-Китайское море", "lat_min": 0, "lat_max": 25, "lon_min": 100, "lon_max": 125},
    "east_china_sea": {"name": "🇨🇳 Восточно-Китайское море", "lat_min": 22, "lat_max": 35, "lon_min": 120, "lon_max": 130},
    "philippine_sea": {"name": "🇵🇭 Филиппинское море", "lat_min": 10, "lat_max": 30, "lon_min": 125, "lon_max": 140},
    "japan_sea": {"name": "🇯🇵 Японское море", "lat_min": 33, "lat_max": 50, "lon_min": 125, "lon_max": 140},
    "yellow_sea": {"name": "🇨🇳 Жёлтое море", "lat_min": 32, "lat_max": 40, "lon_min": 119, "lon_max": 125},
    "bering_sea": {"name": "🇺🇸 Берингово море", "lat_min": 50, "lat_max": 66, "lon_min": -170, "lon_max": -160},
    "chukchi_sea": {"name": "🇷🇺 Чукотское море", "lat_min": 66, "lat_max": 75, "lon_min": -180, "lon_max": -160},
    "australia": {"name": "🇦🇺 Австралия", "lat_min": -40, "lat_max": -10, "lon_min": 110, "lon_max": 155},
    "pacific_west": {"name": "🌊 Тихий океан (запад)", "lat_min": -60, "lat_max": 70, "lon_min": 130, "lon_max": 180},
    "pacific_east": {"name": "🌊 Тихий океан (восток)", "lat_min": -60, "lat_max": 70, "lon_min": -180, "lon_max": -120},
}

# ---------- Полные словари ----------
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

# ---------- Целевые типы (расширенный список) ----------
TARGET_CODES = {
    # Транспортные и заправщики
    'C130', 'KC130', 'MC130', 'C17', 'C5', 'C2',
    'KC135', 'KC10', 'KC46', 'DC10', 'A400M',
    'C30J', 'C27J', 'C295', 'C30', 'C5M', 'C2A',
    # Бизнес-джеты (часто военные)
    'GLF5', 'GLF6', 'GLEX', 'GL7T', 'GL5T',
    'E545', 'E55P', 'CL35', 'C680', 'C700', 'FA6X',
    'LJ45', 'LJ31', 'EMB505', 'CHALLENGER350', 'CHALLENGER300',
    'CITATION', 'CITATIONCJ2', 'CITATIONV', 'CITATIONLATITUDE',
    'P180', 'B200C', 'BE20', 'BE9L', 'TEX2', 'EC45', 'H60', 'AS65',
    # Истребители и бомбардировщики
    'F16', 'F15', 'F22', 'F35', 'F18', 'EA18G',
    'B1', 'B2', 'B52', 'B1B', 'B2A',
    # Разведчики и ДРЛО
    'E3', 'E2', 'E8', 'E7', 'E4', 'E6', 'E767',
    'P3', 'P8', 'U2', 'RC135', 'R135', 'K35R',
    # Прочее
    'P1', 'CP140', 'EC45', 'H60', 'AS65'
}

# ---------- Логирование ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Вспомогательные функции ----------
def is_in_selected_regions(lat: float, lon: float, selected_regions: Set[str]) -> bool:
    """Проверяет, находится ли точка в одном из выбранных регионов."""
    if lat is None or lon is None:
        return False
    for region_key in selected_regions:
        r = REGIONS[region_key]
        if r["lat_min"] <= lat <= r["lat_max"] and r["lon_min"] <= lon <= r["lon_max"]:
            return True
    return False

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

def is_target_type(t: str) -> bool:
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

# ---------- Основной трекер ----------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked: Dict[str, Dict] = {}
        self.active_chats: set = set()
        self.chat_intervals: Dict[int, int] = {}
        self.fr24_api = FlightRadar24API() if FR24_API_AVAILABLE else None
        self.proxy = Config.PROXY
        logger.info(f"🔒 Прокси: {self.proxy if self.proxy else 'не используется'}")

    def get_interval(self, chat_id: int) -> int:
        return self.chat_intervals.get(chat_id, Config.DEFAULT_INTERVAL)

    def set_interval(self, chat_id: int, interval_seconds: int):
        self.chat_intervals[chat_id] = interval_seconds

    # ---------- Получение данных из OpenSky (с прокси) ----------
    async def fetch_opensky(self) -> List[Dict]:
        for attempt in range(1, 5):
            try:
                connector = aiohttp.TCPConnector(family=socket.AF_INET)
                timeout = aiohttp.ClientTimeout(total=90, connect=30)
                logger.info(f"🔄 OpenSky: попытка {attempt} (прокси: {self.proxy if self.proxy else 'нет'})...")
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    async with session.get(
                        Config.OPENSKY_URL,
                        headers={'User-Agent': 'Mozilla/5.0'},
                        proxy=self.proxy if self.proxy else None
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"OpenSky статус {resp.status}")
                            continue
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
                        if result:
                            logger.info(f"OpenSky: получено {len(result)} бортов")
                            return result
                        else:
                            return []
            except asyncio.TimeoutError:
                logger.warning(f"OpenSky: таймаут (попытка {attempt})")
                continue
            except Exception as e:
                logger.error(f"OpenSky ошибка: {e}")
                continue
        logger.error("OpenSky: все попытки неудачны")
        return []

    # ---------- ADS-B (несколько эндпоинтов) ----------
    async def fetch_adsb(self) -> List[Dict]:
        headers = {
            "User-Agent": "MilitaryAircraftBot/1.0",
            "Accept": "application/json"
        }
        for url in Config.ADSB_ENDPOINTS:
            try:
                logger.info(f"🔄 ADS-B: запрос к {url}")
                params = {}
                if "adsb.fi" in url or "airplanes.live" in url:
                    params = {"bounds": "90,-90,-180,180", "format": "json"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params, headers=headers, timeout=20) as resp:
                        if resp.status != 200:
                            logger.warning(f"ADS-B статус {resp.status} для {url}")
                            continue
                        try:
                            data = await resp.json(content_type=None)
                        except Exception as e:
                            text = await resp.text()
                            logger.error(f"ADS-B JSON ошибка: {e}, получено: {text[:200]}")
                            continue
                        ac_list = data.get('ac', [])
                        if not ac_list:
                            continue
                        result = []
                        for ac in ac_list:
                            if ac.get('gnd'):
                                continue
                            icao = ac.get('hex', '').upper()
                            if not icao:
                                continue
                            lat, lon = ac.get('lat'), ac.get('lon')
                            result.append({
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
                            })
                        if result:
                            logger.info(f"ADS-B: получено {len(result)} бортов от {url}")
                            return result
            except Exception as e:
                logger.error(f"ADS-B ошибка для {url}: {e}")
                continue
        return []

    # ---------- FlightRadarAPI ----------
    async def fetch_fr24_api(self) -> List[Dict]:
        if not self.fr24_api:
            return []
        try:
            logger.info("🔄 FlightRadarAPI: запрос...")
            flights = await asyncio.to_thread(self.fr24_api.get_flights)
            if not flights:
                return []
            result = []
            for f in flights:
                icao = getattr(f, 'id', '').upper()
                if not icao:
                    continue
                lat, lon = getattr(f, 'latitude', None), getattr(f, 'longitude', None)
                result.append({
                    'icao': icao,
                    'registration': getattr(f, 'registration', 'N/A') or 'N/A',
                    'call_sign': getattr(f, 'callsign', 'N/A') or 'N/A',
                    'type': getattr(f, 'type', 'N/A') or 'N/A',
                    'operator': getattr(f, 'operator', 'N/A') or 'N/A',
                    'lat': lat,
                    'lon': lon,
                    'altitude': getattr(f, 'altitude', None),
                    'speed': getattr(f, 'speed', None),
                    'timestamp': datetime.now(),
                    'country': getattr(f, 'origin_country', 'Неизвестно') or 'Неизвестно',
                    'coordinates': format_coordinates(lat, lon)
                })
            if result:
                logger.info(f"FlightRadarAPI: получено {len(result)} бортов")
            return result
        except Exception as e:
            logger.error(f"FlightRadarAPI ошибка: {e}")
            return []

    # ---------- Мониторинг (с применением настроек) ----------
    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        chat_data = context.chat_data

        # Получаем настройки пользователя
        selected_regions = chat_data.get('selected_regions', set(REGIONS.keys()))
        filter_mode = chat_data.get('filter_mode', 'targets')  # 'targets' или 'all'

        if not selected_regions:
            logger.info(f"Чат {chat_id}: нет выбранных регионов, пропускаем")
            return

        # Получаем данные из источников
        aircrafts = []
        for fetcher in [self.fetch_opensky, self.fetch_adsb, self.fetch_fr24_api]:
            try:
                result = await fetcher()
                if result:
                    aircrafts = result
                    break
            except Exception as e:
                logger.error(f"Ошибка в источнике: {e}")
                continue

        if not aircrafts:
            logger.info("❌ Нет данных от всех источников")
            return

        # Фильтруем по региону
        region_filtered = [a for a in aircrafts if is_in_selected_regions(a['lat'], a['lon'], selected_regions)]
        logger.info(f"Чат {chat_id}: {len(aircrafts)} всего, {len(region_filtered)} в регионах")

        if not region_filtered:
            return

        # Фильтруем по типу (в зависимости от режима)
        if filter_mode == 'targets':
            processed = []
            for ac in region_filtered:
                icao = ac['icao']
                if icao in self.tracked:
                    continue
                db_entry = self.db.get(icao)
                if db_entry:
                    ac['type'] = db_entry['type']
                    ac['registration'] = db_entry['registration']
                else:
                    ac['type'] = ac.get('type', 'N/A')
                    ac['registration'] = ac.get('registration', 'N/A')
                if ac['type'] == 'N/A' or not is_target_type(ac['type']):
                    continue
                processed.append(ac)
        else:  # 'all' — все самолёты
            processed = []
            for ac in region_filtered:
                icao = ac['icao']
                if icao in self.tracked:
                    continue
                db_entry = self.db.get(icao)
                if db_entry:
                    ac['type'] = db_entry['type']
                    ac['registration'] = db_entry['registration']
                else:
                    ac['type'] = ac.get('type', 'N/A')
                    ac['registration'] = ac.get('registration', 'N/A')
                if ac['type'] == 'N/A':
                    continue
                processed.append(ac)

        # Отправляем уведомления
        new_count = 0
        for ac in processed:
            self.tracked[ac['icao']] = ac
            type_name = AIRCRAFT_NAMES.get(normalize_type(ac['type']), ac['type'])
            msg = (
                "🚨 Самолет обнаружен!\n"
                f"🕒 Время: {ac['timestamp'].strftime('%d.%m.%Y %H:%M:%S')}\n"
                f"▫️ ICAO: {ac['icao']}\n"
                f"▫️ Регистрация: {ac['registration'] or 'N/A'}\n"
                f"▫️ Позывной: {ac['call_sign'] or 'N/A'}\n"
                f"▫️ Тип: {type_name}\n"
                f"▫️ Страна: {ac['country']}\n"
                f"▫️ Координаты: {ac['coordinates']}"
            )
            await context.bot.send_message(chat_id=chat_id, text=msg, disable_web_page_preview=True)
            new_count += 1

        logger.info(f"Чат {chat_id}: отправлено {new_count} новых уведомлений")

# ---------- Обработчики команд ----------
tracker = None

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🟢 Запустить мониторинг", "🔴 Остановить"],
            ["📊 Статус", "🌍 Регионы", "🎯 Режим фильтрации"],
            ["⚙️ Интервал"]
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие"
    )

def get_interval_keyboard():
    buttons = [[KeyboardButton(f"{s} сек")] for s in [30, 60, 120, 300, 600]]
    buttons.append(["🔙 Назад"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)

# ---------- Регионы ----------
async def regions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    selected = context.chat_data.get('selected_regions', set(REGIONS.keys()))
    keyboard = []
    for key, reg in REGIONS.items():
        check = "✅" if key in selected else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {reg['name']}", callback_data=f"region_{key}")])
    keyboard.append([InlineKeyboardButton("🔘 Выбрать все", callback_data="region_all")])
    keyboard.append([InlineKeyboardButton("🔘 Снять все", callback_data="region_none")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="region_back")])
    await update.message.reply_text(
        "🌍 *Выберите регионы для отслеживания:*\n"
        "Нажмите на регион, чтобы включить/выключить.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def region_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    data = query.data
    if data == "region_back":
        await query.edit_message_text("Главное меню", reply_markup=None)
        await query.message.reply_text("Главное меню", reply_markup=get_main_keyboard())
        return
    selected = set(context.chat_data.get('selected_regions', set(REGIONS.keys())))
    if data == "region_all":
        selected = set(REGIONS.keys())
    elif data == "region_none":
        selected = set()
    elif data.startswith("region_"):
        key = data[7:]
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
    context.chat_data['selected_regions'] = selected
    keyboard = []
    for key, reg in REGIONS.items():
        check = "✅" if key in selected else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {reg['name']}", callback_data=f"region_{key}")])
    keyboard.append([InlineKeyboardButton("🔘 Выбрать все", callback_data="region_all")])
    keyboard.append([InlineKeyboardButton("🔘 Снять все", callback_data="region_none")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="region_back")])
    await query.edit_message_text(
        "🌍 *Выберите регионы для отслеживания:*\n"
        "Нажмите на регион, чтобы включить/выключить.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ---------- Режим фильтрации ----------
async def filter_mode_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_mode = context.chat_data.get('filter_mode', 'targets')
    mode_names = {'targets': '🎯 Только целевые', 'all': '✈️ Все самолёты'}
    keyboard = []
    for mode, label in mode_names.items():
        check = "✅" if mode == current_mode else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {label}", callback_data=f"filter_{mode}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="filter_back")])
    await update.message.reply_text(
        "🎯 *Режим фильтрации*\n\n"
        "Выберите, какие самолёты показывать:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "filter_back":
        await query.edit_message_text("Главное меню", reply_markup=None)
        await query.message.reply_text("Главное меню", reply_markup=get_main_keyboard())
        return
    if data.startswith("filter_"):
        mode = data[7:]
        context.chat_data['filter_mode'] = mode
        mode_names = {'targets': '🎯 Только целевые', 'all': '✈️ Все самолёты'}
        await query.edit_message_text(
            f"✅ Режим изменён на: *{mode_names[mode]}*",
            parse_mode="Markdown"
        )
        await query.message.reply_text("Главное меню", reply_markup=get_main_keyboard())

# ---------- Остальные команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'selected_regions' not in context.chat_data:
        context.chat_data['selected_regions'] = set(REGIONS.keys())
    if 'filter_mode' not in context.chat_data:
        context.chat_data['filter_mode'] = 'targets'
    await update.message.reply_text(
        "🛩 *Мульти-трекер с настройками*\n\n"
        "Вы можете:\n"
        "• Выбрать регионы для отслеживания\n"
        "• Включить фильтр только по целевым типам\n"
        "• Настроить интервал опроса\n\n"
        "Используйте кнопки ниже.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    interval = tracker.get_interval(chat_id)
    is_active = chat_id in tracker.active_chats
    selected = context.chat_data.get('selected_regions', set())
    filter_mode = context.chat_data.get('filter_mode', 'targets')
    mode_names = {'targets': '🎯 Только целевые', 'all': '✈️ Все самолёты'}
    regions_text = "\n".join([f"• {REGIONS[r]['name']}" for r in selected]) if selected else "❌ Не выбрано"
    await update.message.reply_text(
        f"📊 *Статус*\n\n"
        f"🟢 Мониторинг: {'активен' if is_active else 'остановлен'}\n"
        f"⏱ Интервал: {interval} сек.\n"
        f"🎯 Режим фильтрации: {mode_names[filter_mode]}\n"
        f"🌍 Выбрано регионов: {len(selected)}\n"
        f"🔍 Отслежено бортов: {len(tracker.tracked)}\n"
        f"⏳ Последнее обновление: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
        f"Регионы:\n{regions_text}",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.job_queue is None:
        await update.message.reply_text("❌ Планировщик не доступен.")
        return
    for job in context.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
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
    await update.message.reply_text(f"✅ Мониторинг запущен (интервал {interval} сек.).", reply_markup=get_main_keyboard())

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if context.job_queue is None:
        await update.message.reply_text("❌ Планировщик не доступен.")
        return
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if not jobs:
        await update.message.reply_text("ℹ️ Мониторинг не активен")
        return
    for job in jobs:
        job.schedule_removal()
    tracker.active_chats.discard(chat_id)
    if not tracker.active_chats:
        tracker.tracked.clear()
    await update.message.reply_text("⛔ Мониторинг остановлен", reply_markup=get_main_keyboard())

async def interval_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ *Настройка интервала*\nВыберите новый интервал:",
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
        seconds = int(text.split()[0])
        if seconds < Config.MIN_INTERVAL:
            await update.message.reply_text(f"Минимальный интервал – {Config.MIN_INTERVAL} сек.")
            return
        tracker.set_interval(chat_id, seconds)
        if chat_id in tracker.active_chats:
            for job in context.job_queue.get_jobs_by_name(str(chat_id)):
                job.schedule_removal()
            context.job_queue.run_repeating(
                tracker.monitor,
                interval=timedelta(seconds=seconds),
                first=5,
                chat_id=chat_id,
                name=str(chat_id),
                job_kwargs={'max_instances': 1}
            )
            await update.message.reply_text(f"✅ Интервал изменён на {seconds} сек. Мониторинг перезапущен.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(f"✅ Интервал сохранён ({seconds} сек.). Запустите мониторинг.", reply_markup=get_main_keyboard())
    except Exception:
        await update.message.reply_text("Неверный формат. Используйте кнопки.", reply_markup=get_interval_keyboard())

async def set_interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажите интервал в секундах, например: /setinterval 30")
        return
    try:
        seconds = int(context.args[0])
        if seconds < Config.MIN_INTERVAL:
            await update.message.reply_text(f"Минимальный интервал – {Config.MIN_INTERVAL} сек.")
            return
        chat_id = update.effective_chat.id
        tracker.set_interval(chat_id, seconds)
        if chat_id in tracker.active_chats:
            for job in context.job_queue.get_jobs_by_name(str(chat_id)):
                job.schedule_removal()
            context.job_queue.run_repeating(
                tracker.monitor,
                interval=timedelta(seconds=seconds),
                first=5,
                chat_id=chat_id,
                name=str(chat_id),
                job_kwargs={'max_instances': 1}
            )
            await update.message.reply_text(f"✅ Интервал изменён на {seconds} сек. Мониторинг перезапущен.", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(f"✅ Интервал сохранён ({seconds} сек.). Запустите мониторинг.", reply_markup=get_main_keyboard())
    except ValueError:
        await update.message.reply_text("Введите число секунд.")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используйте кнопки ⬇️", reply_markup=get_main_keyboard())

# ---------- Healthcheck ----------
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

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("monitor", start_monitoring))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(CommandHandler("setinterval", set_interval_command))

    # Кнопки
    app.add_handler(MessageHandler(filters.Text("🟢 Запустить мониторинг"), start_monitoring))
    app.add_handler(MessageHandler(filters.Text("🔴 Остановить"), stop_monitoring))
    app.add_handler(MessageHandler(filters.Text("📊 Статус"), status))
    app.add_handler(MessageHandler(filters.Text("🌍 Регионы"), regions_menu))
    app.add_handler(MessageHandler(filters.Text("🎯 Режим фильтрации"), filter_mode_menu))
    app.add_handler(MessageHandler(filters.Text("⚙️ Интервал"), interval_settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_interval_choice))
    app.add_handler(MessageHandler(filters.ALL, unknown))

    # Callback-запросы
    app.add_handler(CallbackQueryHandler(region_callback, pattern="^region_"))
    app.add_handler(CallbackQueryHandler(filter_callback, pattern="^filter_"))

    logger.info("🚀 Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
