async def monitor(self, context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    start_time = datetime.now()

    timeout = aiohttp.ClientTimeout(
        total=90,
        connect=30,
        sock_read=60
    )
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    connector = aiohttp.TCPConnector(family=socket.AF_INET)

    for attempt in range(1, 4):
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                logger.info(f"📡 Запрос к {Config.API_URL} (попытка {attempt})")
                async with session.get(Config.API_URL) as response:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    logger.info(f"📊 Статус ответа: {response.status} (время {elapsed:.1f}с)")
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status}")
                    data = await response.json()
                    logger.info(f"✅ JSON получен за {(datetime.now() - start_time).total_seconds():.1f}с")
                    
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
                    
                    return  # успешно завершаем
                
        except asyncio.TimeoutError:
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.warning(f"⏳ Таймаут через {elapsed:.1f}с (попытка {attempt})")
            if attempt == 3:
                logger.error("❌ Все попытки исчерпаны, пропускаем цикл")
                return
            await asyncio.sleep(10)
        except aiohttp.ClientResponseError as e:
            logger.error(f"🌐 Ошибка HTTP: {e.status} – {e.message} (попытка {attempt})")
            if attempt == 3:
                return
            await asyncio.sleep(5)
        except aiohttp.ClientError as e:
            logger.error(f"🌐 Ошибка клиента: {e} (попытка {attempt})")
            if attempt == 3:
                return
            await asyncio.sleep(5)
        except ValueError as e:
            logger.error(f"❌ Ошибка значения: {e} (попытка {attempt})")
            if attempt == 3:
                return
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"❌ Непредвиденная ошибка: {e}", exc_info=True)
            # Возможно, стоит прервать цикл, чтобы не зацикливаться
            return
