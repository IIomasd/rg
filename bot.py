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

# Попытка импорта Flightradar24
try:
    from FlightRadarAPI import FlightRadar24API
    FR24_AVAILABLE = True
except ImportError:
    FR24_AVAILABLE = False
    logging.getLogger(__name__).warning("FlightRadarAPI не установлена, этот источник будет пропущен")

# -------------------- КОНФИГУРАЦИЯ --------------------
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")

    # Источники данных
    SOURCES = [
        {"name": "Flightradar24 (библиотека)", "type": "fr24"},
        {"name": "ADS-B Exchange (data.adsbexchange.com)", "type": "adsb", "url": "https://data.adsbexchange.com/aircraft.json"},
        {"name": "ADS-B Exchange (api.adsbexchange.com)", "type": "adsb", "url": "https://api.adsbexchange.com/aircraft.json"},
        {"name": "OpenSky Network", "type": "opensky", "url": "https://opensky-network.org/api/states/all"},
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

# Целевые коды (поиск по вхождению)
TARGET_CODES = {
    'C130', 'KC130', 'MC130', 'C17', 'C5', 'C2',
    'KC135', 'KC10', 'KC46', 'DC10', 'A400M',
    'P1', 'CP140', 'F16', 'F15', 'F22', 'F35', 'F18',
    'EA18G', 'B1', 'B2', 'B52', 'E3', 'E2', 'E8', 'E7',
    'E4', 'E6', 'E767', 'P3', 'P8', 'U2', 'RC135',
    'C30', 'K35R', 'R135', 'C30J', 'C5M'
}

# -------------------- ЛОГИРОВАНИЕ --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ --------------------
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

# -------------------- ЗАГРУЗЧИК БАЗЫ ДАННЫХ --------------------
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

# -------------------- ОСНОВНОЙ ТРЕКЕР (с перебором всех источников) --------------------
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked_aircrafts: Dict[str, Dict] = {}
        self.active_chats: set = set()
        self.chat_intervals: Dict[int, int] = {}
        self.fr_api = FlightRadar24API() if FR24_AVAILABLE else None
        self.debug_types_shown = False

    def get_interval(self, chat_id: int) -> int:
        return self.chat_intervals.get(chat_id, Config.DEFAULT_INTERVAL)

    def set_interval(self, chat_id: int, interval_seconds: int):
        self.chat_intervals[chat_id] = interval_seconds

    # ---------- Flightradar24 (библиотека) ----------
    async def fetch_fr24(self) -> List[Dict]:
        if not self.fr_api:
            return []
        try:
            logger.info("🔄 Flightradar24 (библиотека): запрос данных...")
            flights = await asyncio.to_thread(self.fr_api.get_flights)
            if not flights:
                logger.info("Flightradar24: пустой ответ")
                return []
            logger.info(f"Flightradar24: получено {len(flights)} бортов")
            aircrafts = []
            debug_count = 0
            for flight in flights:
                icao = getattr(flight, 'id', '').upper()
                if not icao:
                    continue
                aircraft_type = getattr(flight, 'type', 'N/A') or 'N/A'
                if not self.debug_types_shown and debug_count < 10:
                    logger.info(f"DEBUG (FR24): тип '{aircraft_type}' -> нормализованный '{normalize_type(aircraft_type)}' для ICAO {icao}")
                    debug_count += 1
                if debug_count == 10 and not self.debug_types_shown:
                    logger.info("DEBUG: показаны первые 10 типов FR24")
                    self.debug_types_shown = True

                aircraft = {
                    'icao': icao,
                    'registration': getattr(flight, 'registration', 'N/A') or 'N/A',
                    'call_sign': getattr(flight, 'callsign', 'N/A') or 'N/A',
                    'type': aircraft_type,
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
            return aircrafts
        except Exception as e:
            logger.error(f"Ошибка Flightradar24: {e}")
            return []

    # ---------- ADS-B Exchange (HTTP) ----------
    async def fetch_adsb(self, session: aiohttp.ClientSession, url: str) -> List[Dict]:
        try:
            logger.info(f"🔄 ADS-B ({url}): запрос...")
            async with session.get(url, headers=Config.HEADERS, timeout=20) as response:
                if response.status != 200:
                    logger.warning(f"ADS-B ({url}): статус {response.status}")
                    return []
                try:
                    json_data = await response.json(content_type=None)
                except Exception as json_err:
                    text = await response.text()
                    logger.error(f"ADS-B ({url}): ошибка JSON: {json_err}, получено: {text[:200]}...")
                    return []
                ac_list = json_data.get('ac', [])
                if not ac_list:
                    logger.info(f"ADS-B ({url}): пустой список")
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
                    aircraft_type = ac.get('t', 'N/A')
                    aircraft = {
                        'icao': icao,
                        'registration': ac.get('r', '').strip() or 'N/A',
                        'call_sign': ac.get('flight', '').strip() or 'N/A',
                        'type': aircraft_type,
                        'operator': ac.get('ownOp', '').strip() or 'N/A',
                        'lat': ac.get('lat'),
                        'lon': ac.get('lon'),
                        'altitude': ac.get('alt'),
                        'speed': ac.get('speed'),
                        'timestamp': datetime.now(),
                        'country': get_country_by_registration(ac.get('r', '')),
                        'coordinates': format_coordinates(ac.get('lat'), ac.get('lon'))
                    }
                    aircrafts.append(aircraft)
                logger.info(f"ADS-B ({url}): получено {len(aircrafts)} бортов")
                return aircrafts
        except Exception as e:
            logger.error(f"ADS-B ({url}): ошибка - {e}")
            return []

    # ---------- OpenSky (HTTP) ----------
    async def fetch_opensky(self) -> List[Dict]:
        try:
            logger.info("🔄 OpenSky: запрос...")
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(Config.SOURCES[3]['url'], headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    if response.status != 200:
                        logger.warning(f"OpenSky статус {response.status}")
                        return []
                    json_data = await response.json()
                    if 'states' not in json_data:
                        logger.info("OpenSky: нет 'states'")
                        return []
                    aircrafts = []
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
                    logger.info(f"OpenSky: получено {len(aircrafts)} бортов")
                    return aircrafts
        except Exception as e:
            logger.error(f"OpenSky: ошибка - {e}")
            return []

    # ---------- Основной мониторинг ----------
    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        aircrafts = []

        # Перебираем все источники по порядку
        for source in Config.SOURCES:
            if source["type"] == "fr24" and FR24_AVAILABLE:
                aircrafts = await self.fetch_fr24()
            elif source["type"] == "adsb":
                async with aiohttp.ClientSession(headers=Config.HEADERS) as session:
                    aircrafts = await self.fetch_adsb(session, source["url"])
            elif source["type"] == "opensky":
                aircrafts = await self.fetch_opensky()
            else:
                continue

            if aircrafts:
                logger.info(f"✅ Использован источник: {source['name']}, получено {len(aircrafts)} бортов")
                await self.process_aircrafts(aircrafts, chat_id, context)
                return  # успешно получили данные – выходим
            else:
                logger.info(f"❌ Источник {source['name']} не вернул данные, пробуем следующий...")

        # Если ни один источник не дал данных
        logger.info("❌ Все источники данных не вернули самолёты")

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
        "🛩 Мульти-трекер (FR24, ADS-B, OpenSky)\n"
        "Отслеживание самолётов по типам из списка.\n"
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
        "Бот опрашивает по очереди:\n"
        "- Flightradar24 (библиотека)\n"
        "- ADS-B Exchange (2 эндпоинта)\n"
        "- OpenSky Network\n\n"
        "При обнаружении цели из списка – приходит уведомление.\n\n"
        "*Команды:*\n"
        "/start — запустить мониторинг\n"
        "/help — справка\n"
        "/status — статус\n"
        "/stop — остановить\n"
        "/setinterval <сек> — установить интервал (минимум 15)",
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
