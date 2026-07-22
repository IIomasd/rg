#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import csv
import os
import json
import asyncio
import uuid
import gzip
import shutil
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any

import aiohttp
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
)

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8821345795:AAFKqpmAdnNMYTkR9l_iT7BKP4fPk3BdLy0")  # замените на свой
PORT = int(os.getenv("PORT", 8443))

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКИ ПО УМОЛЧАНИЮ ====================
DEFAULT_CONFIG = {
    "interval_seconds": 30,
    "expiry_minutes": 60,
    "allowed_countries": [],
    "target_exact": [
        'C130', 'KC130', 'MC130', 'KC130J', 'C17', 'C5',
        'C2', 'KC135', 'KC10', 'KC46', 'DC10', 'A400M',
        'P1', 'CP140', 'F16', 'F15', 'F22', 'F35', 'F18',
        'EA18G', 'B1', 'B2', 'B52', 'E3', 'E2', 'E8', 'E7',
        'E4', 'E6', 'E767', 'P3', 'P8', 'U2', 'RC135', 'E2C',
        'E2K', 'E737', 'C2A', 'K35R', 'R135', 'C30', 'C30J',
        'C5M', 'E3TF'
    ],
    "target_partial": [
        'C17A', 'KC135R', 'KC135T', 'KC10A', 'KC46A',
        'F16C', 'F15E', 'F22A', 'F35A', 'F35B', 'F35C',
        'EA18G', 'B1B', 'B2A', 'B52', 'E3G', 'E2D', 'P8A', 'MC130',
        'K35R', 'R135', 'C30', 'C30J', 'E3TF'
    ]
}

# ==================== ПРЕДОПРЕДЕЛЁННЫЕ РАЙОНЫ ====================
PREDEFINED_REGIONS = {
    "region_1": {
        "name": "🌏 Дальний Восток и Тихий океан",
        "description": "Японское море, Жёлтое море, Восточно-Китайское море, Южно-Китайское море, Тихий океан, Берингово море",
        "boxes": [
            [0, 65, 100, 180],
            [0, 65, -180, -170]
        ]
    },
    "region_2": {
        "name": "🌏 Индийский океан и Аравийский полуостров",
        "description": "Индийский океан, Аравийское море, Красное море, Аравийский полуостров",
        "boxes": [
            [0, 30, 30, 80]
        ]
    },
    "region_3": {
        "name": "🌍 Европа, Чёрное и Средиземное моря",
        "description": "Чёрное море, Средиземное море, Европа",
        "boxes": [
            [30, 70, -10, 45]
        ]
    }
}

# ==================== ЗАГРУЗЧИК КОНФИГА ====================
class ConfigManager:
    CONFIG_FILE = "tracker_config.json"

    @classmethod
    def load(cls) -> Dict:
        if os.path.exists(cls.CONFIG_FILE):
            try:
                with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    for key, val in DEFAULT_CONFIG.items():
                        if key not in config:
                            config[key] = val
                    return config
            except Exception as e:
                logger.error(f"Ошибка загрузки конфига: {e}")
        return DEFAULT_CONFIG.copy()

    @classmethod
    def save(cls, config: Dict):
        try:
            with open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения конфига: {e}")

    @classmethod
    def get_interval(cls) -> int:
        return cls.load().get("interval_seconds", 30)

    @classmethod
    def get_expiry(cls) -> int:
        return cls.load().get("expiry_minutes", 60)

# ==================== ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЕЙ ====================
class UserPreferences:
    PREF_FILE = "user_preferences.json"

    @classmethod
    def load(cls) -> Dict[int, Set[str]]:
        if os.path.exists(cls.PREF_FILE):
            try:
                with open(cls.PREF_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {int(k): set(v) for k, v in data.items()}
            except Exception as e:
                logger.error(f"Ошибка загрузки предпочтений: {e}")
        return {}

    @classmethod
    def save(cls, prefs: Dict[int, Set[str]]):
        try:
            data = {str(k): list(v) for k, v in prefs.items()}
            with open(cls.PREF_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения предпочтений: {e}")

    @classmethod
    def get_regions(cls, user_id: int) -> Set[str]:
        prefs = cls.load()
        return prefs.get(user_id, set())

    @classmethod
    def set_regions(cls, user_id: int, regions: Set[str]):
        prefs = cls.load()
        if regions:
            prefs[user_id] = regions
        else:
            prefs.pop(user_id, None)
        cls.save(prefs)

    @classmethod
    def add_region(cls, user_id: int, region_key: str):
        prefs = cls.load()
        if user_id not in prefs:
            prefs[user_id] = set()
        prefs[user_id].add(region_key)
        cls.save(prefs)

    @classmethod
    def remove_region(cls, user_id: int, region_key: str):
        prefs = cls.load()
        if user_id in prefs:
            prefs[user_id].discard(region_key)
            if not prefs[user_id]:
                del prefs[user_id]
            cls.save(prefs)

# ==================== ПОЛЬЗОВАТЕЛЬСКИЕ РАЙОНЫ ====================
class CustomRegionManager:
    FILE = "custom_regions.json"

    @classmethod
    def load(cls) -> Dict[int, Dict[str, Dict]]:
        if os.path.exists(cls.FILE):
            try:
                with open(cls.FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {int(k): v for k, v in data.items()}
            except Exception as e:
                logger.error(f"Ошибка загрузки пользовательских районов: {e}")
        return {}

    @classmethod
    def save(cls, custom_regions: Dict[int, Dict[str, Dict]]):
        try:
            data = {str(k): v for k, v in custom_regions.items()}
            with open(cls.FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения пользовательских районов: {e}")

    @classmethod
    def get_user_regions(cls, user_id: int) -> Dict[str, Dict]:
        all_data = cls.load()
        return all_data.get(user_id, {})

    @classmethod
    def add_region(cls, user_id: int, region_key: str, region_data: Dict):
        all_data = cls.load()
        if user_id not in all_data:
            all_data[user_id] = {}
        all_data[user_id][region_key] = region_data
        cls.save(all_data)

    @classmethod
    def remove_region(cls, user_id: int, region_key: str):
        all_data = cls.load()
        if user_id in all_data and region_key in all_data[user_id]:
            del all_data[user_id][region_key]
            if not all_data[user_id]:
                del all_data[user_id]
            cls.save(all_data)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def get_all_regions_for_user(user_id: int) -> Dict[str, Dict]:
    regions = PREDEFINED_REGIONS.copy()
    custom = CustomRegionManager.get_user_regions(user_id)
    regions.update(custom)
    return regions

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

def is_target_aircraft(aircraft_type: str, config_targets: Dict[str, List[str]]) -> bool:
    if not aircraft_type:
        return False
    clean = normalize_type(aircraft_type)
    exact_list = config_targets.get("exact", [])
    partial_list = config_targets.get("partial", [])
    if clean in exact_list:
        return True
    for pattern in partial_list:
        if pattern in clean:
            return True
    return False

def is_in_region(lat: float, lon: float, region_data: Dict) -> bool:
    boxes = region_data.get("boxes", [])
    for box in boxes:
        min_lat, max_lat, min_lon, max_lon = box
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return True
    return False

def is_in_selected_regions(lat: float, lon: float, selected_keys: Set[str], all_regions: Dict[str, Dict]) -> bool:
    if not selected_keys:
        return False
    for key in selected_keys:
        region = all_regions.get(key)
        if region and is_in_region(lat, lon, region):
            return True
    return False

# ==================== ЗАГРУЗЧИК БАЗЫ ICAO (С ПОДДЕРЖКОЙ GOOGLE DRIVE) ====================
class AircraftDatabase:
    def __init__(self):
        self.data: Dict[str, Dict[str, str]] = {}
        self._loaded = False
        self._load_attempted = False

    async def load_async(self):
        """Асинхронная загрузка базы – сначала локальный файл, затем Google Drive"""
        if self._loaded or self._load_attempted:
            return
        self._load_attempted = True

        db_file = "aircraftDatabase.csv"
        gz_file = db_file + ".gz"

        # Если есть сжатый файл – распаковываем
        if os.path.exists(gz_file) and not os.path.exists(db_file):
            logger.info("Распаковка базы из .gz...")
            try:
                with gzip.open(gz_file, 'rb') as f_in:
                    with open(db_file, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                logger.info("Распаковка завершена")
            except Exception as e:
                logger.error(f"Ошибка распаковки: {e}")

        # Если CSV уже есть – загружаем
        if os.path.exists(db_file):
            logger.info("Загрузка базы из локального файла")
            self._load_from_file()
            self._loaded = True
            return

        # Пытаемся скачать с Google Drive
        file_id = "1sS8a5AZdiXMze8f08iNnVL7kTnlRuarl"  # ID вашего файла
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        
        logger.info("Скачивание базы с Google Drive...")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=300, connect=60)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    headers = {"User-Agent": "Mozilla/5.0"}
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            content = await response.read()
                            # Проверяем, не страница ли подтверждения
                            if b'<html' in content[:1024]:
                                text = content.decode('utf-8', errors='ignore')
                                match = re.search(r'uc\?export=download&amp;confirm=([a-zA-Z0-9_-]+)&id=' + file_id, text)
                                if match:
                                    confirm = match.group(1)
                                    download_url = f"https://drive.google.com/uc?export=download&confirm={confirm}&id={file_id}"
                                    async with session.get(download_url, headers=headers) as resp2:
                                        if resp2.status == 200:
                                            with open(db_file, 'wb') as f:
                                                f.write(await resp2.read())
                                            logger.info("База скачана с подтверждением")
                                            self._load_from_file()
                                            self._loaded = True
                                            return
                                else:
                                    logger.warning("Не удалось найти ссылку подтверждения")
                            else:
                                with open(db_file, 'wb') as f:
                                    f.write(content)
                                logger.info("База скачана с Google Drive")
                                self._load_from_file()
                                self._loaded = True
                                return
                        else:
                            logger.error(f"Ошибка HTTP {response.status}")
            except asyncio.TimeoutError:
                logger.error(f"Таймаут при скачивании (попытка {attempt+1})")
            except Exception as e:
                logger.error(f"Ошибка скачивания: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(10)

        # Если файл всё же появился (частично) – пробуем загрузить
        if os.path.exists(db_file):
            self._load_from_file()
            self._loaded = True
            logger.warning("Использую существующий файл (возможно, неполный)")
        else:
            logger.error("Не удалось загрузить базу. Фильтрация по типу отключена.")
            self._loaded = False

    def _load_from_file(self):
        try:
            with open("aircraftDatabase.csv", "r", encoding="utf-8") as f:
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
            logger.info(f"Загружено {len(self.data)} записей из базы")
        except Exception as e:
            logger.error(f"Ошибка чтения базы: {e}")
            self.data = {}

    def get(self, icao: str) -> Optional[Dict[str, str]]:
        return self.data.get(icao.lower())

    def is_loaded(self) -> bool:
        return self._loaded

# ==================== КРАСИВЫЕ НАЗВАНИЯ САМОЛЁТОВ ====================
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

# ==================== ОСНОВНОЙ КЛАСС ТРЕКЕРА ====================
class AircraftTracker:
    def __init__(self, db: AircraftDatabase):
        self.db = db
        self.tracked_aircrafts: Dict[str, Dict] = {}
        self.active_chats: Set[int] = set()
        self._config = ConfigManager.load()
        self._targets = {
            "exact": self._config.get("target_exact", DEFAULT_CONFIG["target_exact"]),
            "partial": self._config.get("target_partial", DEFAULT_CONFIG["target_partial"])
        }
        self._filter_by_type = self.db.is_loaded()
        if not self._filter_by_type:
            logger.warning("Фильтрация по типу отключена – база не загружена")

    def reload_config(self):
        self._config = ConfigManager.load()
        self._targets = {
            "exact": self._config.get("target_exact", DEFAULT_CONFIG["target_exact"]),
            "partial": self._config.get("target_partial", DEFAULT_CONFIG["target_partial"])
        }
        self._allowed_countries = self._config.get("allowed_countries", [])
        self._expiry_minutes = self._config.get("expiry_minutes", 60)

    def clean_old_aircrafts(self):
        expiry = timedelta(minutes=self._config.get("expiry_minutes", 60))
        now = datetime.now()
        to_delete = []
        for icao, data in self.tracked_aircrafts.items():
            if now - data['timestamp'] > expiry:
                to_delete.append(icao)
        for icao in to_delete:
            del self.tracked_aircrafts[icao]
        if to_delete:
            logger.info(f"Очищено старых бортов: {len(to_delete)}")

    async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
        chat_id = context.job.chat_id
        user_id = chat_id  # для личных чатов
        self.reload_config()
        self.clean_old_aircrafts()

        selected_keys = UserPreferences.get_regions(user_id)
        if not selected_keys:
            return

        all_regions = get_all_regions_for_user(user_id)

        try:
            timeout = aiohttp.ClientTimeout(total=120, connect=30, sock_read=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = "https://opensky-network.org/api/states/all"
                async with session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    states = data.get('states', [])
                    if not states:
                        return

                    allowed_countries = self._config.get("allowed_countries", [])
                    for state in states:
                        aircraft = self.parse_aircraft(state)
                        if not aircraft:
                            continue

                        icao = aircraft['icao']
                        if icao in self.tracked_aircrafts:
                            continue

                        lat = aircraft['lat']
                        lon = aircraft['lon']
                        if lat is None or lon is None:
                            continue

                        if not is_in_selected_regions(lat, lon, selected_keys, all_regions):
                            continue

                        if allowed_countries:
                            country = aircraft.get('country', '').strip()
                            if country.lower() not in [c.lower() for c in allowed_countries]:
                                continue

                        db_entry = self.db.get(icao)
                        if db_entry:
                            aircraft_type = db_entry['type']
                            registration = db_entry['registration']
                        else:
                            aircraft_type = "N/A"
                            registration = "N/A"

                        if self._filter_by_type and not is_target_aircraft(aircraft_type, self._targets):
                            continue

                        aircraft['registration'] = registration
                        aircraft['type'] = aircraft_type
                        self.tracked_aircrafts[icao] = aircraft
                        aircraft['coordinates'] = format_coordinates(lat, lon)

                        clean_type = normalize_type(aircraft_type)
                        type_name = AIRCRAFT_NAMES.get(clean_type, aircraft_type if aircraft_type != "N/A" else "Неизвестен")

                        region_names = []
                        for key in selected_keys:
                            region = all_regions.get(key)
                            if region and is_in_region(lat, lon, region):
                                region_names.append(region['name'])
                        region_str = ", ".join(region_names) if region_names else "неизвестен"

                        message = (
                            "🚨 Военный самолет обнаружен!\n"
                            f"🕒 Время: {aircraft['timestamp'].strftime('%d.%m.%Y %H:%M:%S')}\n"
                            f"▫️ ICAO: {icao}\n"
                            f"▫️ Позывной: {aircraft['call_sign']}\n"
                            f"▫️ Регистрация: {registration}\n"
                            f"▫️ Тип: {type_name}\n"
                            f"▫️ Страна: {aircraft['country']}\n"
                            f"▫️ Координаты: {aircraft['coordinates']}\n"
                            f"▫️ Район: {region_str}"
                        )

                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            disable_web_page_preview=True
                        )
                        logger.info(f"✅ Обнаружение: {icao} ({type_name}) в районе {region_str}")

        except asyncio.TimeoutError:
            logger.warning("⏳ Таймаут при запросе к OpenSky")
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

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
tracker: Optional[AircraftTracker] = None

# ==================== СОСТОЯНИЯ ДЛЯ CONVERSATION ====================
(
    SET_INTERVAL, SET_EXPIRY,
    ADD_COUNTRY, REMOVE_COUNTRY,
    ADD_EXACT, REMOVE_EXACT,
    ADD_PARTIAL, REMOVE_PARTIAL,
    CREATE_REGION_NAME, CREATE_REGION_BOX, DELETE_REGION
) = range(11)

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("🟢 Запустить мониторинг")],
        [KeyboardButton("🔴 Остановить мониторинг")],
        [KeyboardButton("📊 Статус")],
        [KeyboardButton("⚙️ Настройки")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_settings_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("⏱ Интервал опроса")],
        [KeyboardButton("⏳ Время жизни записи")],
        [KeyboardButton("🌍 Фильтр по странам")],
        [KeyboardButton("✈️ Управление типами")],
        [KeyboardButton("🗺 Мои районы")],
        [KeyboardButton("◀️ Главное меню")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_region_management_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("✏️ Выбрать районы")],
        [KeyboardButton("➕ Создать район")],
        [KeyboardButton("🗑 Удалить район")],
        [KeyboardButton("📋 Мои районы")],
        [KeyboardButton("◀️ Назад в настройки")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_country_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("➕ Добавить страну")],
        [KeyboardButton("➖ Удалить страну")],
        [KeyboardButton("📋 Список стран")],
        [KeyboardButton("◀️ Назад в настройки")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_type_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton("➕ Добавить точный тип")],
        [KeyboardButton("➖ Удалить точный тип")],
        [KeyboardButton("➕ Добавить частичный тип")],
        [KeyboardButton("➖ Удалить частичный тип")],
        [KeyboardButton("📋 Список типов")],
        [KeyboardButton("◀️ Назад в настройки")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)

def get_remove_country_keyboard(countries: List[str]) -> ReplyKeyboardMarkup:
    keyboard = []
    for country in countries:
        keyboard.append([KeyboardButton(f"❌ {country}")])
    keyboard.append([KeyboardButton("◀️ Назад")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_remove_type_keyboard(types: List[str]) -> ReplyKeyboardMarkup:
    keyboard = []
    for t in types:
        keyboard.append([KeyboardButton(f"❌ {t}")])
    keyboard.append([KeyboardButton("◀️ Назад")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_remove_region_keyboard(regions: Dict[str, Dict]) -> ReplyKeyboardMarkup:
    keyboard = []
    for key, reg in regions.items():
        if key.startswith("custom_"):
            keyboard.append([KeyboardButton(f"❌ {reg['name']}")])
    keyboard.append([KeyboardButton("◀️ Назад")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛩 Военный авиационный трекер\n"
        "Отслеживание военных самолётов по данным OpenSky.\n"
        "Используйте кнопки для управления.",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Военный авиационный трекер*\n\n"
        "Отслеживает военные самолёты по данным OpenSky.\n"
        "Фильтрация по типу, странам и географическим районам.\n"
        "При обнаружении приходит уведомление.\n\n"
        "*Кнопки:*\n"
        "🟢 Запустить мониторинг — начать отслеживание в этом чате\n"
        "🔴 Остановить мониторинг — остановить\n"
        "📊 Статус — показать статистику\n"
        "⚙️ Настройки — изменить параметры (интервал, страны, типы, районы)\n\n"
        "Все настройки доступны каждому пользователю.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def start_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if tracker is None:
        await update.message.reply_text("❌ Трекер не инициализирован.")
        return

    selected = UserPreferences.get_regions(user_id)
    if not selected:
        await update.message.reply_text(
            "⚠️ Сначала выберите хотя бы один район в настройках (🗺 Мои районы → ✏️ Выбрать районы).",
            reply_markup=get_main_keyboard()
        )
        return

    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if jobs:
        await update.message.reply_text("⚠️ Мониторинг уже активен.")
        return

    interval = ConfigManager.get_interval()
    context.job_queue.run_repeating(
        tracker.monitor,
        interval=timedelta(seconds=interval),
        first=5,
        chat_id=chat_id,
        name=str(chat_id)
    )
    tracker.active_chats.add(chat_id)
    await update.message.reply_text(
        f"✅ Мониторинг запущен (интервал {interval} сек.)",
        reply_markup=get_main_keyboard()
    )

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    if not jobs:
        await update.message.reply_text("ℹ️ Мониторинг не активен.")
        return
    for job in jobs:
        job.schedule_removal()
    tracker.active_chats.discard(chat_id)
    if not tracker.active_chats:
        tracker.tracked_aircrafts.clear()
    await update.message.reply_text("⛔ Мониторинг остановлен.", reply_markup=get_main_keyboard())

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    is_active = any(job.name == str(chat_id) for job in context.job_queue.jobs())
    status_text = "активен" if is_active else "не активен"
    selected = UserPreferences.get_regions(user_id)
    all_regions = get_all_regions_for_user(user_id)
    region_names = [all_regions.get(r, {}).get('name', r) for r in selected] if selected else ["не выбраны"]
    await update.message.reply_text(
        f"📊 *Статус трекера*\n"
        f"▫️ В этом чате: {status_text}\n"
        f"▫️ Отслежено бортов всего: {len(tracker.tracked_aircrafts)}\n"
        f"▫️ Активных чатов: {len(tracker.active_chats)}\n"
        f"▫️ Выбранные районы: {', '.join(region_names)}",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ *Настройки*\n"
        "Выберите параметр для изменения.",
        parse_mode="Markdown",
        reply_markup=get_settings_keyboard()
    )

async def region_management_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗺 *Управление районами*\n"
        "Вы можете выбрать существующие, создать свои или удалить.",
        parse_mode="Markdown",
        reply_markup=get_region_management_keyboard()
    )

# ==================== ВЫБОР РАЙОНОВ (INLINE) ====================
async def regions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    selected = UserPreferences.get_regions(user_id)
    all_regions = get_all_regions_for_user(user_id)

    keyboard = []
    for key, region in all_regions.items():
        status = "✅" if key in selected else "⬜"
        button_text = f"{status} {region['name']}"
        callback_data = f"region_toggle_{key}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    keyboard.append([
        InlineKeyboardButton("✅ Выбрать все", callback_data="region_select_all"),
        InlineKeyboardButton("⬜ Сбросить все", callback_data="region_deselect_all")
    ])
    keyboard.append([InlineKeyboardButton("◀️ Назад в управление районами", callback_data="region_back_to_management")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🗺 Выберите районы для отслеживания:\n"
        "Нажмите на кнопку, чтобы включить/выключить.",
        reply_markup=reply_markup
    )

async def regions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "region_back_to_management":
        await query.message.delete()
        await query.message.reply_text("Управление районами", reply_markup=get_region_management_keyboard())
        return

    if data == "region_select_all":
        all_regions = get_all_regions_for_user(user_id)
        new_selected = set(all_regions.keys())
        UserPreferences.set_regions(user_id, new_selected)
        await query.message.edit_text("✅ Выбраны все районы.", reply_markup=None)
        await regions_menu_edit(user_id, query.message)
        return

    if data == "region_deselect_all":
        UserPreferences.set_regions(user_id, set())
        await query.message.edit_text("⬜ Все районы сброшены.", reply_markup=None)
        await regions_menu_edit(user_id, query.message)
        return

    if data.startswith("region_toggle_"):
        key = data.split("_")[2]
        selected = UserPreferences.get_regions(user_id)
        if key in selected:
            selected.remove(key)
        else:
            selected.add(key)
        UserPreferences.set_regions(user_id, selected)
        await regions_menu_edit(user_id, query.message)

async def regions_menu_edit(user_id: int, message):
    selected = UserPreferences.get_regions(user_id)
    all_regions = get_all_regions_for_user(user_id)
    keyboard = []
    for key, region in all_regions.items():
        status = "✅" if key in selected else "⬜"
        button_text = f"{status} {region['name']}"
        callback_data = f"region_toggle_{key}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    keyboard.append([
        InlineKeyboardButton("✅ Выбрать все", callback_data="region_select_all"),
        InlineKeyboardButton("⬜ Сбросить все", callback_data="region_deselect_all")
    ])
    keyboard.append([InlineKeyboardButton("◀️ Назад в управление районами", callback_data="region_back_to_management")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.edit_text(
        "🗺 Выберите районы для отслеживания:\n"
        "Нажмите на кнопку, чтобы включить/выключить.",
        reply_markup=reply_markup
    )

# ==================== СОЗДАНИЕ РАЙОНА ====================
async def create_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔧 *Создание нового района*\n\n"
        "Сначала введите название района (например, 'Моя зона').\n"
        "Затем вы будете добавлять прямоугольники (границы).\n"
        "Каждый прямоугольник задаётся четырьмя числами:\n"
        "`min_lat max_lat min_lon max_lon`\n\n"
        "Пример: `30 40 20 30` означает зону от 30° до 40° с.ш. и от 20° до 30° в.д.\n"
        "Вы можете добавить несколько прямоугольников для одного района.\n"
        "Для завершения введите слово `готово`.\n\n"
        "Введите название:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    return CREATE_REGION_NAME

async def create_region_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_region_management_keyboard())
        return ConversationHandler.END
    if not text.strip():
        await update.message.reply_text("❌ Название не может быть пустым. Попробуйте ещё раз.")
        return CREATE_REGION_NAME
    context.user_data['new_region_name'] = text.strip()
    context.user_data['new_region_boxes'] = []
    await update.message.reply_text(
        f"Название: *{text.strip()}*\n\n"
        "Теперь введите координаты первого прямоугольника.\n"
        "Формат: `min_lat max_lat min_lon max_lon`\n"
        "Пример: `30 40 20 30`\n"
        "После ввода прямоугольника вы сможете добавить ещё или завершить.\n\n"
        "Введите координаты или `готово` для завершения:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard()
    )
    return CREATE_REGION_BOX

async def create_region_box(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_region_management_keyboard())
        return ConversationHandler.END

    if text.lower() == "готово":
        boxes = context.user_data.get('new_region_boxes', [])
        if not boxes:
            await update.message.reply_text("❌ Вы не добавили ни одного прямоугольника. Отмена.")
            return ConversationHandler.END
        user_id = update.effective_user.id
        name = context.user_data['new_region_name']
        region_key = f"custom_{user_id}_{uuid.uuid4().hex[:8]}"
        region_data = {
            "name": f"🛩 {name}",
            "description": f"Пользовательский район: {name}",
            "boxes": boxes
        }
        CustomRegionManager.add_region(user_id, region_key, region_data)
        UserPreferences.add_region(user_id, region_key)
        await update.message.reply_text(
            f"✅ Район '{name}' создан и добавлен в ваши выбранные районы.\n"
            f"Добавлено прямоугольников: {len(boxes)}",
            reply_markup=get_region_management_keyboard()
        )
        return ConversationHandler.END

    try:
        parts = text.split()
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ Нужно ровно 4 числа: `min_lat max_lat min_lon max_lon`\n"
                "Пример: `30 40 20 30`",
                parse_mode="Markdown"
            )
            return CREATE_REGION_BOX
        min_lat, max_lat, min_lon, max_lon = map(float, parts)
        if min_lat >= max_lat:
            await update.message.reply_text("❌ Минимальная широта должна быть меньше максимальной.")
            return CREATE_REGION_BOX
        if min_lon >= max_lon:
            await update.message.reply_text("❌ Минимальная долгота должна быть меньше максимальной.")
            return CREATE_REGION_BOX
        if not (-90 <= min_lat <= 90) or not (-90 <= max_lat <= 90):
            await update.message.reply_text("❌ Широта должна быть в пределах [-90, 90].")
            return CREATE_REGION_BOX
        if not (-180 <= min_lon <= 180) or not (-180 <= max_lon <= 180):
            await update.message.reply_text("❌ Долгота должна быть в пределах [-180, 180].")
            return CREATE_REGION_BOX

        context.user_data['new_region_boxes'].append([min_lat, max_lat, min_lon, max_lon])
        count = len(context.user_data['new_region_boxes'])
        await update.message.reply_text(
            f"✅ Прямоугольник {count} добавлен: {min_lat}..{max_lat}° с.ш., {min_lon}..{max_lon}° в.д.\n"
            f"Всего прямоугольников: {count}\n"
            "Введите следующий прямоугольник или `готово` для завершения.",
            parse_mode="Markdown"
        )
        return CREATE_REGION_BOX
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат. Введите 4 числа через пробел.\n"
            "Пример: `30 40 20 30`",
            parse_mode="Markdown"
        )
        return CREATE_REGION_BOX

# ==================== УДАЛЕНИЕ РАЙОНА ====================
async def delete_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    custom = CustomRegionManager.get_user_regions(user_id)
    if not custom:
        await update.message.reply_text("У вас нет пользовательских районов.", reply_markup=get_region_management_keyboard())
        return ConversationHandler.END
    keyboard = get_remove_region_keyboard(custom)
    await update.message.reply_text(
        "Выберите район для удаления:",
        reply_markup=keyboard
    )
    return DELETE_REGION

async def delete_region_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        await update.message.reply_text("Возврат.", reply_markup=get_region_management_keyboard())
        return ConversationHandler.END
    if text.startswith("❌ "):
        name = text[2:].strip()
        user_id = update.effective_user.id
        custom = CustomRegionManager.get_user_regions(user_id)
        found_key = None
        for key, reg in custom.items():
            if reg['name'] == name:
                found_key = key
                break
        if found_key:
            CustomRegionManager.remove_region(user_id, found_key)
            UserPreferences.remove_region(user_id, found_key)
            await update.message.reply_text(f"✅ Район '{name}' удалён.", reply_markup=get_region_management_keyboard())
        else:
            await update.message.reply_text(f"❌ Район '{name}' не найден.", reply_markup=get_region_management_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("Используйте кнопки.")
    return DELETE_REGION

async def list_my_regions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    all_regions = get_all_regions_for_user(user_id)
    selected = UserPreferences.get_regions(user_id)
    if not all_regions:
        await update.message.reply_text("Нет доступных районов.", reply_markup=get_region_management_keyboard())
        return
    text = "📋 *Ваши районы:*\n\n"
    for key, reg in all_regions.items():
        status = "✅" if key in selected else "⬜"
        text += f"{status} {reg['name']}\n"
        if reg.get('description'):
            text += f"   {reg['description']}\n"
    text += "\nВыберите районы через '✏️ Выбрать районы'."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_region_management_keyboard())

# ==================== НАСТРОЙКА ИНТЕРВАЛА ====================
async def set_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите новый интервал опроса в секундах (минимум 5):",
        reply_markup=get_cancel_keyboard()
    )
    return SET_INTERVAL

async def set_interval_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_settings_keyboard())
        return ConversationHandler.END
    try:
        val = int(text.strip())
        if val < 5:
            await update.message.reply_text("❌ Минимум 5 секунд. Попробуйте ещё раз.")
            return SET_INTERVAL
        config = ConfigManager.load()
        config["interval_seconds"] = val
        ConfigManager.save(config)
        for chat_id in list(tracker.active_chats):
            jobs = context.job_queue.get_jobs_by_name(str(chat_id))
            for job in jobs:
                job.schedule_removal()
            context.job_queue.run_repeating(
                tracker.monitor,
                interval=timedelta(seconds=val),
                first=5,
                chat_id=chat_id,
                name=str(chat_id)
            )
        await update.message.reply_text(f"✅ Интервал установлен в {val} секунд.", reply_markup=get_settings_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Введите целое число.")
        return SET_INTERVAL
    return ConversationHandler.END

# ==================== НАСТРОЙКА ВРЕМЕНИ ЖИЗНИ ====================
async def set_expiry_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите время жизни записи о борте в минутах (минимум 1):",
        reply_markup=get_cancel_keyboard()
    )
    return SET_EXPIRY

async def set_expiry_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_settings_keyboard())
        return ConversationHandler.END
    try:
        val = int(text.strip())
        if val < 1:
            await update.message.reply_text("❌ Минимум 1 минута. Попробуйте ещё раз.")
            return SET_EXPIRY
        config = ConfigManager.load()
        config["expiry_minutes"] = val
        ConfigManager.save(config)
        await update.message.reply_text(f"✅ Время жизни установлено в {val} минут.", reply_markup=get_settings_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Введите целое число.")
        return SET_EXPIRY
    return ConversationHandler.END

# ==================== УПРАВЛЕНИЕ СТРАНАМИ ====================
async def add_country_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите название страны для добавления в фильтр (например, Russia):",
        reply_markup=get_cancel_keyboard()
    )
    return ADD_COUNTRY

async def add_country_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_country_keyboard())
        return ConversationHandler.END
    country = text.strip()
    if not country:
        await update.message.reply_text("❌ Введите название страны.")
        return ADD_COUNTRY
    config = ConfigManager.load()
    allowed = config.get("allowed_countries", [])
    if country in allowed:
        await update.message.reply_text(f"ℹ️ Страна '{country}' уже есть в списке.", reply_markup=get_country_keyboard())
    else:
        allowed.append(country)
        config["allowed_countries"] = allowed
        ConfigManager.save(config)
        await update.message.reply_text(f"✅ Страна '{country}' добавлена.", reply_markup=get_country_keyboard())
    return ConversationHandler.END

async def remove_country_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = ConfigManager.load()
    allowed = config.get("allowed_countries", [])
    if not allowed:
        await update.message.reply_text("Список стран пуст.", reply_markup=get_country_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(
        "Выберите страну для удаления:",
        reply_markup=get_remove_country_keyboard(allowed)
    )
    return REMOVE_COUNTRY

async def remove_country_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        await update.message.reply_text("Возврат.", reply_markup=get_country_keyboard())
        return ConversationHandler.END
    if text.startswith("❌ "):
        country = text[2:].strip()
        config = ConfigManager.load()
        allowed = config.get("allowed_countries", [])
        if country in allowed:
            allowed.remove(country)
            config["allowed_countries"] = allowed
            ConfigManager.save(config)
            await update.message.reply_text(f"✅ Страна '{country}' удалена.", reply_markup=get_country_keyboard())
        else:
            await update.message.reply_text(f"❌ Страна '{country}' не найдена.", reply_markup=get_country_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("Используйте кнопки.")
    return REMOVE_COUNTRY

async def list_countries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = ConfigManager.load()
    allowed = config.get("allowed_countries", [])
    if allowed:
        await update.message.reply_text(
            "🌍 Фильтруемые страны:\n" + "\n".join(allowed),
            reply_markup=get_country_keyboard()
        )
    else:
        await update.message.reply_text(
            "🌍 Фильтр по странам не активен (отслеживаются все).",
            reply_markup=get_country_keyboard()
        )

# ==================== УПРАВЛЕНИЕ ТИПАМИ ====================
async def add_exact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите точный тип (например, C17):",
        reply_markup=get_cancel_keyboard()
    )
    return ADD_EXACT

async def add_exact_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    typ = text.strip().upper()
    if not typ:
        await update.message.reply_text("❌ Введите тип.")
        return ADD_EXACT
    config = ConfigManager.load()
    exact = config.get("target_exact", [])
    if typ in exact:
        await update.message.reply_text(f"ℹ️ Тип '{typ}' уже есть.", reply_markup=get_type_keyboard())
    else:
        exact.append(typ)
        config["target_exact"] = exact
        ConfigManager.save(config)
        await update.message.reply_text(f"✅ Точный тип '{typ}' добавлен.", reply_markup=get_type_keyboard())
    return ConversationHandler.END

async def remove_exact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = ConfigManager.load()
    exact = config.get("target_exact", [])
    if not exact:
        await update.message.reply_text("Список точных типов пуст.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(
        "Выберите точный тип для удаления:",
        reply_markup=get_remove_type_keyboard(exact)
    )
    return REMOVE_EXACT

async def remove_exact_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        await update.message.reply_text("Возврат.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    if text.startswith("❌ "):
        typ = text[2:].strip()
        config = ConfigManager.load()
        exact = config.get("target_exact", [])
        if typ in exact:
            exact.remove(typ)
            config["target_exact"] = exact
            ConfigManager.save(config)
            await update.message.reply_text(f"✅ Точный тип '{typ}' удалён.", reply_markup=get_type_keyboard())
        else:
            await update.message.reply_text(f"❌ Тип '{typ}' не найден.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("Используйте кнопки.")
    return REMOVE_EXACT

async def add_partial_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите частичный тип (например, KC135R):",
        reply_markup=get_cancel_keyboard()
    )
    return ADD_PARTIAL

async def add_partial_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    typ = text.strip().upper()
    if not typ:
        await update.message.reply_text("❌ Введите тип.")
        return ADD_PARTIAL
    config = ConfigManager.load()
    partial = config.get("target_partial", [])
    if typ in partial:
        await update.message.reply_text(f"ℹ️ Тип '{typ}' уже есть.", reply_markup=get_type_keyboard())
    else:
        partial.append(typ)
        config["target_partial"] = partial
        ConfigManager.save(config)
        await update.message.reply_text(f"✅ Частичный тип '{typ}' добавлен.", reply_markup=get_type_keyboard())
    return ConversationHandler.END

async def remove_partial_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = ConfigManager.load()
    partial = config.get("target_partial", [])
    if not partial:
        await update.message.reply_text("Список частичных типов пуст.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    await update.message.reply_text(
        "Выберите частичный тип для удаления:",
        reply_markup=get_remove_type_keyboard(partial)
    )
    return REMOVE_PARTIAL

async def remove_partial_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "◀️ Назад":
        await update.message.reply_text("Возврат.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    if text.startswith("❌ "):
        typ = text[2:].strip()
        config = ConfigManager.load()
        partial = config.get("target_partial", [])
        if typ in partial:
            partial.remove(typ)
            config["target_partial"] = partial
            ConfigManager.save(config)
            await update.message.reply_text(f"✅ Частичный тип '{typ}' удалён.", reply_markup=get_type_keyboard())
        else:
            await update.message.reply_text(f"❌ Тип '{typ}' не найден.", reply_markup=get_type_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("Используйте кнопки.")
    return REMOVE_PARTIAL

async def list_types(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = ConfigManager.load()
    exact = config.get("target_exact", [])
    partial = config.get("target_partial", [])
    text = "📋 Текущие списки типов:\n\n"
    text += "Точные:\n" + (", ".join(exact) if exact else " (пусто)")
    text += "\n\nЧастичные:\n" + (", ".join(partial) if partial else " (пусто)")
    await update.message.reply_text(text, reply_markup=get_type_keyboard())

# ==================== НАВИГАЦИЯ МЕЖДУ МЕНЮ ====================
async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Главное меню", reply_markup=get_main_keyboard())

async def back_to_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Настройки", reply_markup=get_settings_keyboard())

async def back_to_country_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Управление странами", reply_markup=get_country_keyboard())

async def back_to_type_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Управление типами", reply_markup=get_type_keyboard())

# ==================== ОБРАБОТЧИК НЕИЗВЕСТНЫХ КОМАНД ====================
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Используйте кнопки для управления.",
        reply_markup=get_main_keyboard()
    )

# ==================== ЗАПУСК ====================
async def main_async():
    global tracker
    db = AircraftDatabase()
    await db.load_async()  # асинхронная загрузка базы

    tracker = AircraftTracker(db)

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # Основные кнопки
    app.add_handler(MessageHandler(filters.Text("🟢 Запустить мониторинг"), start_monitoring))
    app.add_handler(MessageHandler(filters.Text("🔴 Остановить мониторинг"), stop_monitoring))
    app.add_handler(MessageHandler(filters.Text("📊 Статус"), status))
    app.add_handler(MessageHandler(filters.Text("⚙️ Настройки"), settings_menu))
    app.add_handler(MessageHandler(filters.Text("◀️ Главное меню"), back_to_main))
    app.add_handler(MessageHandler(filters.Text("◀️ Назад в настройки"), back_to_settings))
    app.add_handler(MessageHandler(filters.Text("🌍 Фильтр по странам"), back_to_country_menu))
    app.add_handler(MessageHandler(filters.Text("✈️ Управление типами"), back_to_type_menu))
    app.add_handler(MessageHandler(filters.Text("🗺 Мои районы"), region_management_menu))
    app.add_handler(MessageHandler(filters.Text("✏️ Выбрать районы"), regions_menu))
    app.add_handler(MessageHandler(filters.Text("➕ Создать район"), create_region_start))
    app.add_handler(MessageHandler(filters.Text("🗑 Удалить район"), delete_region_start))
    app.add_handler(MessageHandler(filters.Text("📋 Мои районы"), list_my_regions))
    app.add_handler(MessageHandler(filters.Text("📋 Список стран"), list_countries))
    app.add_handler(MessageHandler(filters.Text("📋 Список типов"), list_types))

    # Callback для районов
    app.add_handler(CallbackQueryHandler(regions_callback, pattern="^region_"))

    # Conversation для интервала
    interval_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("⏱ Интервал опроса"), set_interval_start)],
        states={SET_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_interval_receive)]},
        fallbacks=[MessageHandler(filters.Text("❌ Отмена"), set_interval_receive)],
        allow_reentry=True,
    )
    app.add_handler(interval_conv)

    # Conversation для времени жизни
    expiry_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("⏳ Время жизни записи"), set_expiry_start)],
        states={SET_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_expiry_receive)]},
        fallbacks=[MessageHandler(filters.Text("❌ Отмена"), set_expiry_receive)],
        allow_reentry=True,
    )
    app.add_handler(expiry_conv)

    # Conversation для стран
    add_country_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("➕ Добавить страну"), add_country_start)],
        states={ADD_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_receive)]},
        fallbacks=[MessageHandler(filters.Text("❌ Отмена"), add_country_receive)],
        allow_reentry=True,
    )
    app.add_handler(add_country_conv)

    remove_country_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("➖ Удалить страну"), remove_country_start)],
        states={REMOVE_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_country_receive)]},
        fallbacks=[MessageHandler(filters.Text("◀️ Назад"), remove_country_receive)],
        allow_reentry=True,
    )
    app.add_handler(remove_country_conv)

    # Conversation для типов
    add_exact_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("➕ Добавить точный тип"), add_exact_start)],
        states={ADD_EXACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_exact_receive)]},
        fallbacks=[MessageHandler(filters.Text("❌ Отмена"), add_exact_receive)],
        allow_reentry=True,
    )
    app.add_handler(add_exact_conv)

    remove_exact_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("➖ Удалить точный тип"), remove_exact_start)],
        states={REMOVE_EXACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_exact_receive)]},
        fallbacks=[MessageHandler(filters.Text("◀️ Назад"), remove_exact_receive)],
        allow_reentry=True,
    )
    app.add_handler(remove_exact_conv)

    add_partial_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("➕ Добавить частичный тип"), add_partial_start)],
        states={ADD_PARTIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_partial_receive)]},
        fallbacks=[MessageHandler(filters.Text("❌ Отмена"), add_partial_receive)],
        allow_reentry=True,
    )
    app.add_handler(add_partial_conv)

    remove_partial_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("➖ Удалить частичный тип"), remove_partial_start)],
        states={REMOVE_PARTIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_partial_receive)]},
        fallbacks=[MessageHandler(filters.Text("◀️ Назад"), remove_partial_receive)],
        allow_reentry=True,
    )
    app.add_handler(remove_partial_conv)

    # Conversation для создания района (уже добавлен через entry_points, но чтобы избежать дублирования, добавим только один)
    # Убедимся, что мы не дублируем обработчики – они уже есть через отдельные entry_points.

    # Обработчик всего остального
    app.add_handler(MessageHandler(filters.ALL, unknown))

    # Запуск
    public_url = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if public_url:
        webhook_url = f"https://{public_url}/{BOT_TOKEN}"
        logger.info(f"Установка вебхука: {webhook_url}")
        await app.initialize()
        await app.start()
        await app.bot.set_webhook(webhook_url)
        from aiohttp import web
        from telegram.ext import Application

        async def handle(request):
            return await app.process_update(await request.text())

        app_web = web.Application()
        app_web.router.add_post(f"/{BOT_TOKEN}", handle)
        runner = web.AppRunner(app_web)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
        await site.start()
        logger.info("Бот запущен в режиме вебхука")
        await asyncio.Event().wait()
    else:
        logger.info("Запуск в режиме polling")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()
