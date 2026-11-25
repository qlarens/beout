"""Тексты бота, карточек и ошибок API: русский, украинский, английский.

Язык пользователя — выбранный в профиле Mini App, иначе язык его Telegram;
языка без перевода (или неизвестного) — английский.
"""

LANGS = ("ru", "uk", "en")
DEFAULT_LANG = "en"


def pick(code):
    """Язык интерфейса по language_code Telegram («ru», «uk», «en-US», …)."""
    base = (code or "").split("-")[0].lower()
    return base if base in LANGS else DEFAULT_LANG


def t(lang, key, **values):
    text = TEXTS[key][lang]
    return text.format(**values) if values else text


TEXTS = {
    # --- команды и кнопка меню ---
    "cmd_start": {"ru": "Как начать тренировку", "uk": "Як почати тренування", "en": "How to start a workout"},
    "cmd_stop": {"ru": "Завершить тренировку", "uk": "Завершити тренування", "en": "Finish the workout"},
    "cmd_weight": {
        "ru": "Вес для калорий: /weight 72", "uk": "Вага для калорій: /weight 72",
        "en": "Weight for calories: /weight 72",
    },
    "cmd_privacy": {"ru": "Условия и конфиденциальность", "uk": "Умови та конфіденційність", "en": "Terms and privacy"},
    "cmd_export": {"ru": "Выгрузить мои данные", "uk": "Вивантажити мої дані", "en": "Export my data"},
    "cmd_delete": {"ru": "Удалить все мои данные", "uk": "Видалити всі мої дані", "en": "Delete all my data"},
    "cmd_top": {"ru": "Рейтинг группы за неделю", "uk": "Рейтинг групи за тиждень", "en": "Group's weekly leaderboard"},
    "menu": {"ru": "Мои тренировки", "uk": "Мої тренування", "en": "My workouts"},

    # --- согласие и приветствие ---
    "consent": {
        "ru": "Перед началом — коротко о данных.\n\n"
              "Я сохраняю геопозицию во время трансляции (координаты, время, точность), "
              "твой Telegram ID, язык и вес, если укажешь. Это нужно только для маршрута и статистики. "
              "Данные не продаются и не используются для рекламы.\n\n"
              "Выгрузить свои данные: /export. Удалить всё: /delete.\n\n"
              "Нажимая «Принимаю», ты соглашаешься с условиями использования "
              "и политикой конфиденциальности.",
        "uk": "Перед початком — коротко про дані.\n\n"
              "Я зберігаю геопозицію під час трансляції (координати, час, точність), "
              "твій Telegram ID, мову та вагу, якщо вкажеш. Це потрібно лише для маршруту та статистики. "
              "Дані не продаються і не використовуються для реклами.\n\n"
              "Вивантажити свої дані: /export. Видалити все: /delete.\n\n"
              "Натискаючи «Приймаю», ти погоджуєшся з умовами використання "
              "та політикою конфіденційності.",
        "en": "Before we start, a word about data.\n\n"
              "While you share your live location, I store it (coordinates, time, accuracy) "
              "along with your Telegram ID, language and weight, if you enter it. It is only used for your route "
              "and stats. Data is never sold or used for ads.\n\n"
              "Export your data: /export. Delete everything: /delete.\n\n"
              "By tapping “I accept” you agree to the terms of use and the privacy policy.",
    },
    "consent_accepted": {"ru": "✅ <b>Принято</b>", "uk": "✅ <b>Прийнято</b>", "en": "✅ <b>Accepted</b>"},
    "consent_then_restart": {
        "ru": "Пока условия не приняты, я ничего не записываю. После этого начни трансляцию заново.",
        "uk": "Поки умови не прийняті, я нічого не записую. Після цього почни трансляцію знову.",
        "en": "Until you accept, I don't record anything. After that, start sharing your live location again.",
    },
    "btn_accept": {"ru": "✅ Принимаю", "uk": "✅ Приймаю", "en": "✅ I accept"},
    "btn_read": {"ru": "📄 Прочитать", "uk": "📄 Прочитати", "en": "📄 Read"},
    "policy_updated": {
        "ru": "📄 <b>Условия использования обновились.</b>\n\n"
              "Что нового: велотренировки привязываются к дорогам через сервис OSRM — так точнее "
              "дистанция и скорость (ему уходят только координаты и время, без Telegram ID). "
              "Раньше добавились приглашения друзей, своё фото на карточке и обезличенная статистика.\n\n"
              "Пока ты не примешь новые условия, я не записываю тренировки.",
        "uk": "📄 <b>Умови використання оновилися.</b>\n\n"
              "Що нового: велотренування прив'язуються до доріг через сервіс OSRM — так точніші "
              "дистанція та швидкість (йому йдуть лише координати й час, без Telegram ID). "
              "Раніше додалися запрошення друзів, своє фото на картці та знеособлена статистика.\n\n"
              "Поки ти не приймеш нові умови, я не записую тренування.",
        "en": "📄 <b>The terms of use have been updated.</b>\n\n"
              "What's new: bike rides are snapped to roads by the OSRM service for more precise "
              "distance and speed (it only gets coordinates and times, no Telegram ID). "
              "Earlier we added friend invites, your own photo on cards and anonymised statistics.\n\n"
              "Until you accept the new terms, I won't record workouts.",
    },
    "btn_policy": {
        "ru": "📄 Условия и конфиденциальность", "uk": "📄 Умови та конфіденційність", "en": "📄 Terms and privacy",
    },
    "welcome": {
        "ru": "Привет! Я записываю тренировки: велосипед, бег и ходьбу.\n\n"
              "<b>Как начать тренировку:</b>\n"
              "1. Нажми 📎 → «Геопозиция» → «Транслировать геопозицию» и выбери срок с запасом.\n"
              "2. Двигайся — телефон можно убрать в карман. Следи за дорогой, а не за экраном.\n"
              "3. В конце нажми «Финиш» (или останови трансляцию, или отправь /stop).\n\n"
              "Вес для расчёта калорий: <b>{weight} кг</b>. Изменить: /weight 72\n"
              "Мои данные: /export, удалить всё: /delete.",
        "uk": "Привіт! Я записую тренування: велосипед, біг і ходьбу.\n\n"
              "<b>Як почати тренування:</b>\n"
              "1. Натисни 📎 → «Геопозиція» → «Транслювати геопозицію» і вибери термін із запасом.\n"
              "2. Рухайся — телефон можна сховати в кишеню. Стеж за дорогою, а не за екраном.\n"
              "3. Наприкінці натисни «Фініш» (або зупини трансляцію, або надішли /stop).\n\n"
              "Вага для розрахунку калорій: <b>{weight} кг</b>. Змінити: /weight 72\n"
              "Мої дані: /export, видалити все: /delete.",
        "en": "Hi! I record workouts: cycling, running and walking.\n\n"
              "<b>How to start a workout:</b>\n"
              "1. Tap 📎 → “Location” → “Share My Live Location” and pick a period with time to spare.\n"
              "2. Go! You can put your phone in your pocket. Watch the road, not the screen.\n"
              "3. At the end tap “Finish” (or stop sharing, or send /stop).\n\n"
              "Weight for calorie estimates: <b>{weight} kg</b>. Change it: /weight 72\n"
              "My data: /export, delete everything: /delete.",
    },

    # --- типы тренировок ---
    "kind_ride": {"ru": "Велосипед", "uk": "Велосипед", "en": "Cycling"},
    "kind_run": {"ru": "Бег", "uk": "Біг", "en": "Running"},
    "kind_walk": {"ru": "Ходьба", "uk": "Ходьба", "en": "Walking"},

    # --- тренировка в чате ---
    "not_live": {
        "ru": "Это обычная геопозиция, по ней маршрут не построить. Нужна именно «Трансляция геопозиции» 📡",
        "uk": "Це звичайна геопозиція, за нею маршрут не побудувати. Потрібна саме «Трансляція геопозиції» 📡",
        "en": "This is a regular location, I can't build a route from it. I need “Share My Live Location” 📡",
    },
    "started": {
        "ru": "Тренировка началась, записываю маршрут! Тип можно поменять кнопками ниже.\n"
              "Чтобы закончить, нажми «Финиш», останови трансляцию или отправь /stop.",
        "uk": "Тренування почалося, записую маршрут! Тип можна змінити кнопками нижче.\n"
              "Щоб завершити, натисни «Фініш», зупини трансляцію або надішли /stop.",
        "en": "Workout started, recording your route! You can change the type with the buttons below.\n"
              "To finish, tap “Finish”, stop sharing your location or send /stop.",
    },
    "btn_live": {"ru": "📍 Смотреть в реальном времени", "uk": "📍 Дивитися в реальному часі", "en": "📍 Watch live"},
    "btn_finish": {"ru": "🏁 Финиш", "uk": "🏁 Фініш", "en": "🏁 Finish"},
    "btn_open": {"ru": "🗺 Открыть трекер", "uk": "🗺 Відкрити трекер", "en": "🗺 Open tracker"},
    "btn_route": {"ru": "🗺 Маршрут на карте", "uk": "🗺 Маршрут на мапі", "en": "🗺 Route on the map"},
    "btn_share": {"ru": "📤 Карточка для друзей", "uk": "📤 Картка для друзів", "en": "📤 Card for friends"},
    "btn_card_open": {"ru": "Открыть трекер", "uk": "Відкрити трекер", "en": "Open tracker"},
    "no_active": {
        "ru": "Сейчас нет активной тренировки.", "uk": "Зараз немає активного тренування.",
        "en": "There's no active workout right now.",
    },
    "ride_not_found": {"ru": "Тренировка не найдена", "uk": "Тренування не знайдено", "en": "Workout not found"},
    "live_warning": {
        "ru": "⚠️ Трансляция геопозиции ещё идёт, но больше не записывается. "
              "Останови её: крестик на плашке трансляции вверху чата.",
        "uk": "⚠️ Трансляція геопозиції ще триває, але більше не записується. "
              "Зупини її: хрестик на плашці трансляції вгорі чату.",
        "en": "⚠️ Your live location is still being shared, but it's no longer recorded. "
              "Stop it with the ✕ on the live location bar at the top of the chat.",
    },
    "speed_line": {
        "ru": "Средняя: {avg} км/ч, макс: {max} км/ч",
        "uk": "Середня: {avg} км/год, макс: {max} км/год",
        "en": "Average: {avg} km/h, max: {max} km/h",
    },
    "summary": {
        "ru": "🏁 <b>{kind}: тренировка завершена</b>\n\n"
              "📏 Дистанция: <b>{km} км</b>\n"
              "⏱ Время: {duration} (в движении {moving})\n"
              "⚡ {speed}\n"
              "🔥 Калории: ≈ <b>{kcal} ккал</b>",
        "uk": "🏁 <b>{kind}: тренування завершено</b>\n\n"
              "📏 Дистанція: <b>{km} км</b>\n"
              "⏱ Час: {duration} (у русі {moving})\n"
              "⚡ {speed}\n"
              "🔥 Калорії: ≈ <b>{kcal} ккал</b>",
        "en": "🏁 <b>{kind}: workout finished</b>\n\n"
              "📏 Distance: <b>{km} km</b>\n"
              "⏱ Time: {duration} (moving {moving})\n"
              "⚡ {speed}\n"
              "🔥 Calories: ≈ <b>{kcal} kcal</b>",
    },
    "summary_elevation": {
        "ru": "⛰ Набор высоты: {m} м", "uk": "⛰ Набір висоти: {m} м", "en": "⛰ Elevation gain: {m} m",
    },
    "new_record": {"ru": "Новый рекорд: {what}", "uk": "Новий рекорд: {what}", "en": "New record: {what}"},
    "rec_distance_km": {"ru": "самая длинная тренировка", "uk": "найдовше тренування", "en": "longest workout"},
    "rec_max_speed_kmh": {"ru": "максимальная скорость", "uk": "максимальна швидкість", "en": "top speed"},
    "rec_best_1k_s": {"ru": "лучший 1 км", "uk": "найкращий 1 км", "en": "best 1 km"},
    "rec_best_5k_s": {"ru": "лучшие 5 км", "uk": "найкращі 5 км", "en": "best 5 km"},
    "card_caption": {
        "ru": "{kind}: {km} км за {moving} · {speed}\n{app}",
        "uk": "{kind}: {km} км за {moving} · {speed}\n{app}",
        "en": "{kind}: {km} km in {moving} · {speed}\n{app}",
    },
    "card_saved_caption": {
        "ru": "Карточка тренировки: перешли её или сохрани в галерею.",
        "uk": "Картка тренування: перешли її або збережи в галерею.",
        "en": "Your workout card: forward it or save it to your gallery.",
    },
    "story_caption": {
        "ru": "Для Instagram: открой картинку, сохрани в галерею и добавь в историю.",
        "uk": "Для Instagram: відкрий картинку, збережи в галерею та додай в історію.",
        "en": "For Instagram: open the image, save it to your gallery and add it to your story.",
    },

    # --- данные пользователя ---
    "weight_help": {
        "ru": "Напиши вес в килограммах, например: /weight 72",
        "uk": "Напиши вагу в кілограмах, наприклад: /weight 72",
        "en": "Send your weight in kilograms, for example: /weight 72",
    },
    "weight_saved": {"ru": "Готово, вес: {w} кг ✅", "uk": "Готово, вага: {w} кг ✅", "en": "Done, weight: {w} kg ✅"},
    "privacy": {
        "ru": "Условия использования и политика конфиденциальности — по кнопке ниже.\n"
              "Выгрузить свои данные: /export. Удалить всё: /delete.",
        "uk": "Умови використання та політика конфіденційності — за кнопкою нижче.\n"
              "Вивантажити свої дані: /export. Видалити все: /delete.",
        "en": "Terms of use and the privacy policy are behind the button below.\n"
              "Export your data: /export. Delete everything: /delete.",
    },
    "export_caption": {
        "ru": "Все данные о тебе, которые хранит бот (время — unix timestamp).",
        "uk": "Усі дані про тебе, які зберігає бот (час — unix timestamp).",
        "en": "All the data the bot stores about you (times are unix timestamps).",
    },
    "delete_confirm": {
        "ru": "Удалить все твои данные: тренировки, маршруты, вес, участие в сессиях и рейтингах, "
              "согласие с условиями? Отменить это нельзя. Если нужна копия — сначала /export.",
        "uk": "Видалити всі твої дані: тренування, маршрути, вагу, участь у сесіях і рейтингах, "
              "згоду з умовами? Скасувати це неможливо. Якщо потрібна копія — спершу /export.",
        "en": "Delete all your data: workouts, routes, weight, joint sessions and leaderboards, your consent? "
              "This can't be undone. If you need a copy, run /export first.",
    },
    "btn_delete_yes": {"ru": "🗑 Да, удалить всё", "uk": "🗑 Так, видалити все", "en": "🗑 Yes, delete everything"},
    "btn_cancel": {"ru": "Отмена", "uk": "Скасувати", "en": "Cancel"},
    "delete_cancelled": {
        "ru": "Отменено, данные на месте.", "uk": "Скасовано, дані на місці.",
        "en": "Cancelled, your data is still here.",
    },
    "deleted": {
        "ru": "🗑 Все твои данные удалены. Из резервных копий они исчезнут в течение 7 дней.\n"
              "Если трансляция геопозиции ещё идёт — останови её в Telegram. "
              "Сообщения в этом чате удаляются средствами Telegram.\n"
              "Чтобы снова пользоваться ботом — /start.",
        "uk": "🗑 Усі твої дані видалено. З резервних копій вони зникнуть протягом 7 днів.\n"
              "Якщо трансляція геопозиції ще триває — зупини її в Telegram. "
              "Повідомлення в цьому чаті видаляються засобами Telegram.\n"
              "Щоб знову користуватися ботом — /start.",
        "en": "🗑 All your data has been deleted. It will disappear from backups within 7 days.\n"
              "If your live location is still being shared, stop it in Telegram. "
              "Messages in this chat are deleted with Telegram's own tools.\n"
              "To use the bot again: /start.",
    },

    # --- SOS ---
    "sos_own_link": {
        "ru": "Это твоя ссылка — отправь её близкому человеку.",
        "uk": "Це твоє посилання — надішли його близькій людині.",
        "en": "This is your own link: send it to someone close to you.",
    },
    "sos_bad_link": {
        "ru": "Ссылка недействительна: попроси прислать новую.",
        "uk": "Посилання недійсне: попроси надіслати нове.",
        "en": "This link is no longer valid: ask for a new one.",
    },
    "sos_invite": {
        "ru": "{name} просит стать экстренным контактом в {app}.\n\n"
              "Если во время тренировки {name} нажмёт SOS, я пришлю тебе последнюю геопозицию. "
              "О тебе сохраняется только Telegram ID.",
        "uk": "{name} просить стати екстреним контактом у {app}.\n\n"
              "Якщо під час тренування {name} натисне SOS, я надішлю тобі останню геопозицію. "
              "Про тебе зберігається лише Telegram ID.",
        "en": "{name} asks you to be an emergency contact in {app}.\n\n"
              "If {name} presses SOS during a workout, I'll send you their last location. "
              "Only your Telegram ID is stored.",
    },
    "btn_sos_yes": {"ru": "Да, стать контактом", "uk": "Так, стати контактом", "en": "Yes, be a contact"},
    "btn_sos_no": {"ru": "Нет", "uk": "Ні", "en": "No"},
    "btn_sos_off": {"ru": "Отказаться", "uk": "Відмовитися", "en": "Opt out"},
    "sos_declined": {
        "ru": "Хорошо, ты не экстренный контакт.", "uk": "Добре, ти не екстрений контакт.",
        "en": "OK, you're not an emergency contact.",
    },
    "sos_left": {
        "ru": "Ты больше не экстренный контакт для {name}.", "uk": "Ти більше не екстрений контакт для {name}.",
        "en": "You're no longer an emergency contact for {name}.",
    },
    "sos_accepted": {
        "ru": "Готово: ты экстренный контакт для {name}.", "uk": "Готово: ти екстрений контакт для {name}.",
        "en": "Done: you're now an emergency contact for {name}.",
    },
    "sos_owner_notice": {
        "ru": "{name} теперь твой экстренный контакт. Список и удаление — в профиле Mini App.",
        "uk": "{name} тепер твій екстрений контакт. Список і видалення — у профілі Mini App.",
        "en": "{name} is now your emergency contact. See or remove contacts in the Mini App profile.",
    },
    "ref_notice": {
        "ru": "🎉 {name} присоединяется к {app} по твоей ссылке. Приглашено друзей: {n}. Список — в профиле Mini App.",
        "uk": "🎉 {name} приєднується до {app} за твоїм посиланням. Запрошено друзів: {n}. Список — у профілі Mini App.",
        "en": "🎉 {name} joined {app} via your link. Friends invited: {n}. See the list in the Mini App profile.",
    },
    "invites_title": {
        "ru": "Пользователей: {users}, по приглашению: {invited}.\n",
        "uk": "Користувачів: {users}, за запрошенням: {invited}.\n",
        "en": "Users: {users}, invited: {invited}.\n",
    },
    "invites_none": {"ru": "Пока никто никого не пригласил.", "uk": "Поки ніхто нікого не запросив.",
                     "en": "Nobody has invited anyone yet."},
    "sos_alert": {
        "ru": "🆘 SOS от {name} во время тренировки.\n"
              "Последняя геопозиция — ниже, получена {min} мин назад.\n"
              "Свяжись как можно скорее. Если ответа нет — звони 112.",
        "uk": "🆘 SOS від {name} під час тренування.\n"
              "Остання геопозиція — нижче, отримана {min} хв тому.\n"
              "Зв'яжися якнайшвидше. Якщо відповіді немає — телефонуй 112.",
        "en": "🆘 SOS from {name} during a workout.\n"
              "Their last location is below, received {min} min ago.\n"
              "Get in touch as soon as possible. If there's no answer, call 112.",
    },
    "participant": {"ru": "Участник", "uk": "Учасник", "en": "Participant"},

    # --- рейтинг в группах ---
    "rating_week": {"ru": "🏆 Рейтинг недели", "uk": "🏆 Рейтинг тижня", "en": "🏆 Weekly leaderboard"},
    "rating_results": {"ru": "🏆 Итоги недели", "uk": "🏆 Підсумки тижня", "en": "🏆 Week results"},
    "rating_place": {"ru": "{n}. {name} — {km} км", "uk": "{n}. {name} — {km} км", "en": "{n}. {name} — {km} km"},
    "rating_empty": {
        "ru": "Пока никто из участников не тренировался.", "uk": "Поки ніхто з учасників не тренувався.",
        "en": "No one has trained yet.",
    },
    "rating_footer": {
        "ru": "В рейтинге только те, кто нажал «Участвовать». Тренировки записывает бот в личном чате.",
        "uk": "У рейтингу лише ті, хто натиснув «Брати участь». Тренування записує бот в особистому чаті.",
        "en": "Only those who tapped “Join” are on the leaderboard. Workouts are recorded by the bot in a private chat.",
    },
    "btn_join": {"ru": "Участвовать", "uk": "Брати участь", "en": "Join"},
    "btn_leave": {"ru": "Выйти", "uk": "Вийти", "en": "Leave"},
    "top_private": {
        "ru": "Рейтинг работает в группах: добавь меня в групповой чат (например, чат клуба) и отправь там /top.",
        "uk": "Рейтинг працює в групах: додай мене в груповий чат (наприклад, чат клубу) і надішли там /top.",
        "en": "The leaderboard works in groups: add me to a group chat (for example, your club's chat) "
              "and send /top there.",
    },
    "rating_consent_first": {
        "ru": "Сначала открой @{bot}, нажми «Старт» и прими условия.",
        "uk": "Спершу відкрий @{bot}, натисни «Старт» і прийми умови.",
        "en": "First open @{bot}, tap “Start” and accept the terms.",
    },
    "rating_joined": {
        "ru": "Ты в рейтинге этой группы.", "uk": "Ти в рейтингу цієї групи.",
        "en": "You're on this group's leaderboard.",
    },
    "rating_left": {
        "ru": "Ты больше не в рейтинге этой группы.", "uk": "Ти більше не в рейтингу цієї групи.",
        "en": "You're no longer on this group's leaderboard.",
    },

    # --- ошибки API (видны в Mini App) ---
    "err_open_in_tg": {"ru": "Откройте приложение из Telegram", "uk": "Відкрийте застосунок із Telegram",
                       "en": "Open the app from Telegram"},
    "err_photo": {
        "ru": "Не получилось открыть фото — выбери другое (JPEG или PNG).",
        "uk": "Не вдалося відкрити фото — вибери інше (JPEG або PNG).",
        "en": "Couldn't open the photo. Pick another one (JPEG or PNG).",
    },
    "err_consent": {
        "ru": "Сначала прими условия в чате с ботом: /start", "uk": "Спершу прийми умови в чаті з ботом: /start",
        "en": "First accept the terms in the chat with the bot: /start",
    },
    "err_session_gone": {
        "ru": "Совместная тренировка не найдена или уже закончилась",
        "uk": "Спільне тренування не знайдено або воно вже закінчилося",
        "en": "Joint workout not found or already over",
    },
    "err_session": {
        "ru": "Совместная тренировка не найдена", "uk": "Спільне тренування не знайдено",
        "en": "Joint workout not found",
    },
    "err_sos_no_location": {
        "ru": "Нет геопозиции: SOS работает во время тренировки с трансляцией",
        "uk": "Немає геопозиції: SOS працює під час тренування з трансляцією",
        "en": "No location: SOS works during a workout with live location on",
    },
    "err_sos_no_contacts": {
        "ru": "Сначала добавь экстренный контакт в профиле", "uk": "Спершу додай екстрений контакт у профілі",
        "en": "Add an emergency contact in your profile first",
    },
    "err_weight": {
        "ru": "Вес должен быть от 30 до 250 кг", "uk": "Вага має бути від 30 до 250 кг",
        "en": "Weight must be between 30 and 250 kg",
    },

    # --- карточки ---
    "card_km": {"ru": "км", "uk": "км", "en": "km"},
    "card_moving": {"ru": "в движении", "uk": "у русі", "en": "moving"},
    "card_kmh": {"ru": "км/ч", "uk": "км/год", "en": "km/h"},
    "card_kcal": {"ru": "ккал", "uk": "ккал", "en": "kcal"},
    "card_avg": {"ru": "км/ч средняя", "uk": "км/год середня", "en": "km/h average"},
    "card_max": {"ru": "км/ч макс.", "uk": "км/год макс.", "en": "km/h max"},
    "card_elevation": {"ru": "набор высоты", "uk": "набір висоти", "en": "elevation gain"},
    "card_meters": {"ru": "{m} м", "uk": "{m} м", "en": "{m} m"},
    "card_total": {"ru": "всего", "uk": "загалом", "en": "total"},
    "osm": {"ru": "© участники OpenStreetMap", "uk": "© учасники OpenStreetMap", "en": "© OpenStreetMap contributors"},
    "watermark": {"ru": "@beoutbot от lilaxy.com", "uk": "@beoutbot від lilaxy.com", "en": "@beoutbot by lilaxy.com"},
}
