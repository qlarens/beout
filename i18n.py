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
              "Что нового: бег и ходьба тоже привязываются к пешеходным дорожкам (OSRM), а путь между редкими точками "
              "восстанавливается по дорогам — трек больше не срезает углы прямыми. Раньше — активности в группах: лента "
              "тренировок (только если включишь «Публиковать»), челленджи, сборы, дуэли, битвы клубов, номинации недели "
              "и часовой пояс телефона для времени сборов.\n\n"
              "Пока ты не примешь новые условия, я не записываю тренировки.",
        "uk": "📄 <b>Умови використання оновилися.</b>\n\n"
              "Що нового: біг і ходьба теж прив'язуються до пішохідних доріжок (OSRM), а шлях між рідкими точками "
              "відновлюється дорогами — трек більше не зрізає кути прямими. Раніше — активності в групах: стрічка "
              "тренувань (лише якщо ввімкнеш «Публікувати»), челенджі, збори, дуелі, битви клубів, номінації тижня та "
              "часовий пояс телефона для часу зборів.\n\n"
              "Поки ти не приймеш нові умови, я не записую тренування.",
        "en": "📄 <b>The terms of use have been updated.</b>\n\n"
              "What's new: runs and walks are snapped to footpaths too (OSRM), and the path between sparse points is "
              "restored along them — the track no longer cuts corners with straight lines. Earlier: group activities — "
              "a workout feed (only if you turn on “Post”), challenges, meetups, duels, club battles, weekly awards and "
              "your phone's time zone for meetup times.\n\n"
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
    "kind_locked": {
        "ru": "Тренировка уже завершена — её тип не меняется",
        "uk": "Тренування вже завершене — його тип не змінюється",
        "en": "This workout has finished, so its type can't be changed",
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

# --- активности в группах: челленджи, лента, сборы, дуэли, битва клубов, номинации, напоминания, inline ---
TEXTS.update({
    "cmd_challenge": {"ru": "Цель группы: /challenge 300 км", "uk": "Ціль групи: /challenge 300 км",
                      "en": "Group goal: /challenge 300 km"},
    "cmd_run": {"ru": "Сбор: /run сб 9:00 парк", "uk": "Збір: /run сб 9:00 парк", "en": "Meetup: /run sat 9:00 park"},
    "cmd_duel": {"ru": "Дуэль: ответом на сообщение соперника", "uk": "Дуель: відповіддю на повідомлення суперника",
                 "en": "Duel: as a reply to your rival's message"},
    "cmd_clubwar": {"ru": "Битва клубов с другой группой", "uk": "Битва клубів з іншою групою",
                    "en": "Club battle with another group"},
    "cmd_reminders": {"ru": "Напоминания группе: on / off", "uk": "Нагадування групі: on / off",
                      "en": "Group reminders: on / off"},
    "cmd_help": {"ru": "Что я умею в группе", "uk": "Що я вмію в групі", "en": "What I can do in a group"},
    "group_only": {
        "ru": "Эта команда работает в группах: добавь меня в групповой чат и отправь её там.",
        "uk": "Ця команда працює в групах: додай мене в груповий чат і надішли її там.",
        "en": "This command works in groups: add me to a group chat and send it there.",
    },
    "only_admins": {
        "ru": "Это могут сделать админы группы или автор.", "uk": "Це можуть зробити адміни групи або автор.",
        "en": "Only the group's admins or the author can do this.",
    },
    "group_help": {
        "ru": "Что можно делать в группе:\n"
              "/top — рейтинг недели, месяца и серий; «Участвовать» — попасть в него, "
              "«Публиковать» — показывать здесь свои тренировки\n"
              "/challenge 300 км — общая цель на неделю, /challenge каждый 20 км — личная каждому\n"
              "/run сб 9:00 парк — позвать на тренировку\n"
              "/duel — ответом на сообщение соперника: кто больше км за неделю\n"
              "/clubwar — битва с другой группой\n"
              "/reminders off — не напоминать группе о тренировках\n\n"
              "Тренировки записывает бот в личном чате: @{bot}",
        "uk": "Що можна робити в групі:\n"
              "/top — рейтинг тижня, місяця й серій; «Брати участь» — потрапити в нього, "
              "«Публікувати» — показувати тут свої тренування\n"
              "/challenge 300 км — спільна ціль на тиждень, /challenge кожен 20 км — особиста кожному\n"
              "/run сб 9:00 парк — покликати на тренування\n"
              "/duel — відповіддю на повідомлення суперника: хто більше км за тиждень\n"
              "/clubwar — битва з іншою групою\n"
              "/reminders off — не нагадувати групі про тренування\n\n"
              "Тренування записує бот в особистому чаті: @{bot}",
        "en": "What you can do in a group:\n"
              "/top — weekly, monthly and streak leaderboards; “Join” to get on it, "
              "“Post” to share your workouts here\n"
              "/challenge 300 km — a shared weekly goal, /challenge each 20 km — a personal one for everyone\n"
              "/run sat 9:00 park — invite people to a workout\n"
              "/duel — as a reply to your rival's message: who covers more km in a week\n"
              "/clubwar — a battle with another group\n"
              "/reminders off — don't remind the group to train\n\n"
              "Workouts are recorded by the bot in a private chat: @{bot}",
    },
    # рейтинг
    "btn_tab_week": {"ru": "Неделя", "uk": "Тиждень", "en": "Week"},
    "btn_tab_month": {"ru": "Месяц", "uk": "Місяць", "en": "Month"},
    "btn_tab_streak": {"ru": "Серии", "uk": "Серії", "en": "Streaks"},
    "rating_month": {"ru": "🏆 Рейтинг месяца", "uk": "🏆 Рейтинг місяця", "en": "🏆 Monthly leaderboard"},
    "rating_streak": {"ru": "🔥 Серии: дней подряд с тренировкой", "uk": "🔥 Серії: днів поспіль із тренуванням",
                      "en": "🔥 Streaks: days in a row with a workout"},
    "streak_place": {"ru": "{n}. {name} — {days} дн. · {weeks} нед.", "uk": "{n}. {name} — {days} дн. · {weeks} тиж.",
                     "en": "{n}. {name} — {days} d · {weeks} wk"},
    "btn_feed_on": {"ru": "📣 Публиковать мои тренировки", "uk": "📣 Публікувати мої тренування",
                    "en": "📣 Post my workouts"},
    "btn_feed_off": {"ru": "🔕 Не публиковать", "uk": "🔕 Не публікувати", "en": "🔕 Stop posting"},
    "feed_on": {
        "ru": "Твои тренировки будут появляться в этой группе карточкой, и ты в рейтинге.",
        "uk": "Твої тренування з'являтимуться в цій групі карткою, і ти в рейтингу.",
        "en": "Your workouts will appear in this group as a card, and you're on the leaderboard.",
    },
    "feed_off": {"ru": "Больше не публикую твои тренировки здесь.", "uk": "Більше не публікую твої тренування тут.",
                 "en": "I won't post your workouts here anymore."},
    "feed_caption": {"ru": "{name}: {kind}, {km} км за {moving} · {speed}",
                     "uk": "{name}: {kind}, {km} км за {moving} · {speed}",
                     "en": "{name}: {kind}, {km} km in {moving} · {speed}"},
    # номинации и картинка итогов
    "nom_title": {"ru": "🏅 Номинации недели", "uk": "🏅 Номінації тижня", "en": "🏅 Awards of the week"},
    "nom_longest": {"ru": "Самая длинная тренировка — {name}, {value} км",
                    "uk": "Найдовше тренування — {name}, {value} км", "en": "Longest workout — {name}, {value} km"},
    "nom_early": {"ru": "Ранняя пташка — {name}, старт в {value}", "uk": "Рання пташка — {name}, старт о {value}",
                  "en": "Early bird — {name}, started at {value}"},
    "nom_steady": {"ru": "Стабильность — {name}, {value} дн. с тренировками",
                   "uk": "Стабільність — {name}, {value} дн. з тренуваннями",
                   "en": "Consistency — {name}, {value} days with workouts"},
    "nom_progress": {"ru": "Прогресс — {name}, +{value} км к прошлой неделе",
                     "uk": "Прогрес — {name}, +{value} км до минулого тижня",
                     "en": "Progress — {name}, +{value} km vs last week"},
    "nom_short_longest": {"ru": "Самая длинная", "uk": "Найдовше", "en": "Longest"},
    "nom_short_early": {"ru": "Ранняя пташка", "uk": "Рання пташка", "en": "Early bird"},
    "nom_short_steady": {"ru": "Стабильность", "uk": "Стабільність", "en": "Consistency"},
    "nom_short_progress": {"ru": "Прогресс", "uk": "Прогрес", "en": "Progress"},
    "nom_value_longest": {"ru": "{value} км", "uk": "{value} км", "en": "{value} km"},
    "nom_value_early": {"ru": "старт {value}", "uk": "старт {value}", "en": "start {value}"},
    "nom_value_steady": {"ru": "{value} дн.", "uk": "{value} дн.", "en": "{value} days"},
    "nom_value_progress": {"ru": "+{value} км", "uk": "+{value} км", "en": "+{value} km"},
    "week_card_total": {"ru": "км вместе", "uk": "км разом", "en": "km together"},
    "week_card_people": {"ru": "участников тренировались", "uk": "учасників тренувалися", "en": "members trained"},
    # челленджи
    "challenge_usage": {
        "ru": "Как задать цель:\n/challenge 300 км — вместе за 7 дней\n/challenge каждый 20 км бег — каждому\n"
              "/challenge 100 км 14 дней\nЗавершить: /challenge stop",
        "uk": "Як задати ціль:\n/challenge 300 км — разом за 7 днів\n/challenge кожен 20 км біг — кожному\n"
              "/challenge 100 км 14 днів\nЗавершити: /challenge stop",
        "en": "How to set a goal:\n/challenge 300 km — together in 7 days\n/challenge each 20 km run — for everyone\n"
              "/challenge 100 km 14 days\nStop: /challenge stop",
    },
    "challenge_exists": {"ru": "В группе уже идёт челлендж. Завершить его: /challenge stop",
                         "uk": "У групі вже триває челендж. Завершити його: /challenge stop",
                         "en": "A challenge is already running here. To stop it: /challenge stop"},
    "challenge_none": {"ru": "Сейчас челленджа нет.", "uk": "Зараз челенджу немає.", "en": "There's no challenge now."},
    "challenge_title": {"ru": "🎯 Челлендж: {goal} км вместе{kind} до {until}",
                        "uk": "🎯 Челендж: {goal} км разом{kind} до {until}",
                        "en": "🎯 Challenge: {goal} km together{kind} by {until}"},
    "challenge_title_each": {"ru": "🎯 Челлендж: каждому по {goal} км{kind} до {until}",
                             "uk": "🎯 Челендж: кожному по {goal} км{kind} до {until}",
                             "en": "🎯 Challenge: {goal} km each{kind} by {until}"},
    "challenge_done": {"ru": "{done} из {goal} км", "uk": "{done} з {goal} км", "en": "{done} of {goal} km"},
    "challenge_line": {"ru": "{mark} {name} — {done} из {goal} км", "uk": "{mark} {name} — {done} з {goal} км",
                       "en": "{mark} {name} — {done} of {goal} km"},
    "challenge_footer": {
        "ru": "Считаются тренировки участников рейтинга (/top → «Участвовать»).",
        "uk": "Рахуються тренування учасників рейтингу (/top → «Брати участь»).",
        "en": "Workouts of leaderboard members count (/top → “Join”).",
    },
    "challenge_reached": {"ru": "🎉 Цель достигнута: {goal} км вместе!", "uk": "🎉 Ціль досягнуто: {goal} км разом!",
                          "en": "🎉 Goal reached: {goal} km together!"},
    "challenge_reached_each": {"ru": "🎉 {name} выполняет цель челленджа: {goal} км!",
                               "uk": "🎉 {name} виконує ціль челенджу: {goal} км!",
                               "en": "🎉 {name} has hit the challenge goal: {goal} km!"},
    "challenge_end_ok": {"ru": "🏁 Челлендж завершён, цель достигнута: {done} из {goal} км!",
                         "uk": "🏁 Челендж завершено, ціль досягнуто: {done} з {goal} км!",
                         "en": "🏁 Challenge over, goal reached: {done} of {goal} km!"},
    "challenge_end_fail": {"ru": "🏁 Челлендж завершён: {done} из {goal} км. В следующий раз получится!",
                           "uk": "🏁 Челендж завершено: {done} з {goal} км. Наступного разу вийде!",
                           "en": "🏁 Challenge over: {done} of {goal} km. Next time!"},
    "challenge_end_each": {"ru": "🏁 Челлендж завершён: цель выполнили {n} из {total}.",
                           "uk": "🏁 Челендж завершено: ціль виконали {n} з {total}.",
                           "en": "🏁 Challenge over: {n} of {total} hit the goal."},
    "challenge_stopped": {"ru": "Челлендж остановлен.", "uk": "Челендж зупинено.", "en": "Challenge stopped."},
    # сборы
    "event_usage": {
        "ru": "Как позвать на тренировку:\n/run сб 9:00 Парк Шевченко\n/run завтра 7:30 стадион, 10 км\n"
              "/run 29.11 18:00 набережная\n\nДата — сегодня, завтра, день недели или 29.11; время — 9:00.",
        "uk": "Як покликати на тренування:\n/run сб 9:00 Парк Шевченка\n/run завтра 7:30 стадіон, 10 км\n"
              "/run 29.11 18:00 набережна\n\nДата — сьогодні, завтра, день тижня або 29.11; час — 9:00.",
        "en": "How to invite people to a workout:\n/run sat 9:00 Central Park\n/run tomorrow 7:30 stadium, 10 km\n"
              "/run 29.11 18:00 riverside\n\nDate: today, tomorrow, a weekday or 29.11; time: 9:00.",
    },
    "event_tz_hint": {
        "ru": "Время — по {tz}. Чтобы я знал твой часовой пояс, открой трекер в чате с ботом.",
        "uk": "Час — за {tz}. Щоб я знав твій часовий пояс, відкрий трекер у чаті з ботом.",
        "en": "Times are in {tz}. Open the tracker in the chat with the bot so I know your time zone.",
    },
    "event_title": {"ru": "📍 Сбор: {place}\n🕒 {when}", "uk": "📍 Збір: {place}\n🕒 {when}",
                    "en": "📍 Meetup: {place}\n🕒 {when}"},
    "event_going": {"ru": "✅ Идут ({n}): {names}", "uk": "✅ Ідуть ({n}): {names}", "en": "✅ Going ({n}): {names}"},
    "event_maybe": {"ru": "🤔 Может быть ({n}): {names}", "uk": "🤔 Можливо ({n}): {names}",
                    "en": "🤔 Maybe ({n}): {names}"},
    "event_nobody": {"ru": "Пока никто не отметился.", "uk": "Поки ніхто не відмітився.", "en": "No one has replied yet."},
    "event_footer": {
        "ru": "За час напомню, а в начале открою совместную тренировку: все увидят друг друга на карте.",
        "uk": "За годину нагадаю, а на початку відкрию спільне тренування: всі побачать одне одного на мапі.",
        "en": "I'll remind you an hour before and open a joint workout at the start, so everyone is on one map.",
    },
    "btn_go": {"ru": "✅ Иду", "uk": "✅ Іду", "en": "✅ Going"},
    "btn_maybe": {"ru": "🤔 Может быть", "uk": "🤔 Можливо", "en": "🤔 Maybe"},
    "btn_no": {"ru": "Не иду", "uk": "Не йду", "en": "Can't"},
    "btn_cancel_event": {"ru": "Отменить сбор", "uk": "Скасувати збір", "en": "Cancel meetup"},
    "rsvp_go": {"ru": "Отметил: идёшь 💪", "uk": "Відмітив: ідеш 💪", "en": "Got it: you're going 💪"},
    "rsvp_maybe": {"ru": "Отметил: может быть", "uk": "Відмітив: можливо", "en": "Got it: maybe"},
    "rsvp_no": {"ru": "Отметил: не идёшь", "uk": "Відмітив: не йдеш", "en": "Got it: not going"},
    "event_cancelled": {"ru": "❌ Сбор отменён.", "uk": "❌ Збір скасовано.", "en": "❌ Meetup cancelled."},
    "event_closed": {"ru": "Сбор уже прошёл или отменён.", "uk": "Збір уже минув або скасований.",
                     "en": "This meetup is over or cancelled."},
    "event_reminder": {"ru": "⏰ Через час: {place}, в {time}. Идут: {names}.",
                       "uk": "⏰ За годину: {place}, о {time}. Ідуть: {names}.",
                       "en": "⏰ In an hour: {place} at {time}. Going: {names}."},
    "event_start": {
        "ru": "🏁 Начинаем: {place}! Нажми кнопку и запусти трансляцию геопозиции в чате с ботом — "
              "все будут на одной карте.",
        "uk": "🏁 Починаємо: {place}! Натисни кнопку й запусти трансляцію геопозиції в чаті з ботом — "
              "усі будуть на одній мапі.",
        "en": "🏁 Let's go: {place}! Tap the button and share your live location in the chat with the bot — "
              "everyone will be on one map.",
    },
    "btn_event_join": {"ru": "🗺 Совместная тренировка", "uk": "🗺 Спільне тренування", "en": "🗺 Joint workout"},
    "event_summary": {"ru": "📊 Итоги сбора «{place}»: {n} чел., вместе {km} км",
                      "uk": "📊 Підсумки збору «{place}»: {n} ос., разом {km} км",
                      "en": "📊 Meetup “{place}”: {n} people, {km} km together"},
    "event_summary_line": {"ru": "{name} — {km} км", "uk": "{name} — {km} км", "en": "{name} — {km} km"},
    # дуэли
    "duel_call": {"ru": "⚔️ {a} вызывает {b} на дуэль: кто больше км за {days} дн.{kind}",
                  "uk": "⚔️ {a} викликає {b} на дуель: хто більше км за {days} дн.{kind}",
                  "en": "⚔️ {a} challenges {b} to a duel: who covers more km in {days} days{kind}"},
    "duel_call_open": {"ru": "⚔️ {a} вызывает на дуэль: кто больше км за {days} дн.{kind}. Кто примет вызов?",
                       "uk": "⚔️ {a} викликає на дуель: хто більше км за {days} дн.{kind}. Хто прийме виклик?",
                       "en": "⚔️ {a} is looking for a duel: who covers more km in {days} days{kind}. Who's in?"},
    "btn_duel_accept": {"ru": "⚔️ Принять вызов", "uk": "⚔️ Прийняти виклик", "en": "⚔️ Accept"},
    "btn_duel_decline": {"ru": "Отказаться", "uk": "Відмовитися", "en": "Decline"},
    "duel_active": {"ru": "⚔️ Дуэль{kind} до {until}\n{a} — {a_km} км\n{b} — {b_km} км",
                    "uk": "⚔️ Дуель{kind} до {until}\n{a} — {a_km} км\n{b} — {b_km} км",
                    "en": "⚔️ Duel{kind} until {until}\n{a} — {a_km} km\n{b} — {b_km} km"},
    "duel_win": {"ru": "🏆 Дуэль окончена: побеждает {winner} — {w_km} км против {l_km}!",
                 "uk": "🏆 Дуель завершено: перемагає {winner} — {w_km} км проти {l_km}!",
                 "en": "🏆 Duel over: {winner} wins — {w_km} km vs {l_km}!"},
    "duel_draw": {"ru": "🤝 Дуэль окончена вничью: по {km} км.", "uk": "🤝 Дуель завершено внічию: по {km} км.",
                  "en": "🤝 The duel is a draw: {km} km each."},
    "duel_declined": {"ru": "Вызов отклонён.", "uk": "Виклик відхилено.", "en": "The challenge was declined."},
    "duel_expired": {"ru": "Вызов никто не принял.", "uk": "Виклик ніхто не прийняв.", "en": "No one took the challenge."},
    "duel_not_you": {"ru": "Этот вызов не тебе.", "uk": "Цей виклик не тобі.", "en": "This challenge isn't for you."},
    "duel_self": {"ru": "Нельзя принять свой же вызов.", "uk": "Не можна прийняти власний виклик.",
                  "en": "You can't accept your own challenge."},
    "duel_closed": {"ru": "Этот вызов уже не действует.", "uk": "Цей виклик уже не діє.",
                    "en": "This challenge is no longer open."},
    # битва клубов
    "war_created": {
        "ru": "⚔️ Битва клубов на {days} дн.! Пусть админ другой группы отправит там:\n/clubwar {code}\n"
              "Код действует сутки. Считаются км участников рейтинга обеих групп.",
        "uk": "⚔️ Битва клубів на {days} дн.! Хай адмін іншої групи надішле там:\n/clubwar {code}\n"
              "Код діє добу. Рахуються км учасників рейтингу обох груп.",
        "en": "⚔️ A club battle for {days} days! Ask an admin of the other group to send there:\n/clubwar {code}\n"
              "The code is valid for a day. Leaderboard members' km count for both groups.",
    },
    "war_active": {"ru": "⚔️ Битва клубов до {until}\n{a} — {a_km} км\n{b} — {b_km} км",
                   "uk": "⚔️ Битва клубів до {until}\n{a} — {a_km} км\n{b} — {b_km} км",
                   "en": "⚔️ Club battle until {until}\n{a} — {a_km} km\n{b} — {b_km} km"},
    "war_win": {"ru": "🏆 Битва клубов окончена: побеждает «{winner}» — {w_km} км против {l_km}!",
                "uk": "🏆 Битву клубів завершено: перемагає «{winner}» — {w_km} км проти {l_km}!",
                "en": "🏆 Club battle over: “{winner}” wins — {w_km} km vs {l_km}!"},
    "war_draw": {"ru": "🤝 Битва клубов окончена вничью: по {km} км.", "uk": "🤝 Битву клубів завершено внічию: по {km} км.",
                 "en": "🤝 The club battle is a draw: {km} km each."},
    "war_bad_code": {"ru": "Код не найден или устарел.", "uk": "Код не знайдено або він застарів.",
                     "en": "Code not found or expired."},
    "war_same": {"ru": "Это код вашей же группы — отправьте его в другую.", "uk": "Це код вашої ж групи — надішліть його в іншу.",
                 "en": "That's your own group's code: send it to the other group."},
    "group_untitled": {"ru": "Группа", "uk": "Група", "en": "Group"},
    # напоминания
    "nudge_idle": {"ru": "👟 Группа не тренировалась уже {days} дн. Кто сегодня?",
                   "uk": "👟 Група не тренувалася вже {days} дн. Хто сьогодні?",
                   "en": "👟 Nobody here has trained for {days} days. Who's going today?"},
    "nudge_streak": {"ru": "🔥 Серия клуба — {days} дн. подряд, а сегодня ещё никто не тренировался. Сохраним?",
                     "uk": "🔥 Серія клубу — {days} дн. поспіль, а сьогодні ще ніхто не тренувався. Збережемо?",
                     "en": "🔥 The club streak is {days} days, but nobody has trained today. Shall we keep it?"},
    "reminders_on": {"ru": "Напоминания группе включены.", "uk": "Нагадування групі ввімкнено.",
                     "en": "Group reminders are on."},
    "reminders_off": {"ru": "Напоминания группе выключены.", "uk": "Нагадування групі вимкнено.",
                      "en": "Group reminders are off."},
    "reminders_usage": {"ru": "/reminders on или /reminders off", "uk": "/reminders on або /reminders off",
                        "en": "/reminders on or /reminders off"},
    # inline
    "inline_start": {"ru": "Открыть {app}", "uk": "Відкрити {app}", "en": "Open {app}"},
    "inline_last": {"ru": "Моя последняя тренировка", "uk": "Моє останнє тренування", "en": "My last workout"},
    "inline_week": {"ru": "Моя неделя", "uk": "Мій тиждень", "en": "My week"},
    "inline_records": {"ru": "Мои рекорды", "uk": "Мої рекорди", "en": "My records"},
    "inline_invite": {"ru": "Позвать в {app}", "uk": "Покликати в {app}", "en": "Invite to {app}"},
    "inline_week_text": {"ru": "📅 Моя неделя в {app}: {n} трен., {km} км", "uk": "📅 Мій тиждень у {app}: {n} трен., {km} км",
                         "en": "📅 My week in {app}: {n} workouts, {km} km"},
    "inline_kind_line": {"ru": "• {kind}: {km} км", "uk": "• {kind}: {km} км", "en": "• {kind}: {km} km"},
    "inline_records_text": {"ru": "🏆 Мои рекорды в {app}:", "uk": "🏆 Мої рекорди в {app}:", "en": "🏆 My records in {app}:"},
    "inline_record_line": {"ru": "• {kind} — {what}: {value}", "uk": "• {kind} — {what}: {value}",
                           "en": "• {kind} — {what}: {value}"},
    "inline_invite_text": {"ru": "Записываю тренировки в {app} прямо в Telegram — присоединяйся: {link}",
                           "uk": "Записую тренування в {app} просто в Telegram — приєднуйся: {link}",
                           "en": "I track my workouts with {app} right in Telegram. Join me: {link}"},
    "weekday_0": {"ru": "пн", "uk": "пн", "en": "Mon"},
    "weekday_1": {"ru": "вт", "uk": "вт", "en": "Tue"},
    "weekday_2": {"ru": "ср", "uk": "ср", "en": "Wed"},
    "weekday_3": {"ru": "чт", "uk": "чт", "en": "Thu"},
    "weekday_4": {"ru": "пт", "uk": "пт", "en": "Fri"},
    "weekday_5": {"ru": "сб", "uk": "сб", "en": "Sat"},
    "weekday_6": {"ru": "вс", "uk": "нд", "en": "Sun"},
})
