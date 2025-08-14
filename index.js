require('dotenv').config();
const fs = require('fs');
const path = require('path');
const TelegramBot = require('node-telegram-bot-api');

// Загрузка токена
const BOT_TOKEN = process.env.BOT_TOKEN;
if (!BOT_TOKEN) {
    console.error('ERROR: Укажи BOT_TOKEN в .env');
    process.exit(1);
}

const CONFIG_FILE = path.resolve(__dirname, 'config.json');

// Подключаем списки действий и участников
const { PEOPLE, ACTIONS_ONE } = require('./actions/people');
const { ACTIONS_PLURAL, WEIGHTS } = require('./actions/group');

function readConfig() {
    try {
        return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
    } catch {
        return {};
    }
}

function writeConfig(cfg) {
    try {
        fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2), 'utf8');
    } catch (e) {
        console.error('Ошибка записи config.json', e);
    }
}

const bot = new TelegramBot(BOT_TOKEN, { polling: true });

function weightedChoice(weights) {
    const sum = weights.reduce((a, b) => a + b, 0);
    let r = Math.random() * sum;
    for (let i = 0; i < weights.length; i++) {
        if (r < weights[i]) return i;
        r -= weights[i];
    }
    return weights.length - 1;
}

function pickUnique(arr, k) {
    if (k >= arr.length) return arr.slice();
    const copy = arr.slice();
    const res = [];
    for (let i = 0; i < k; i++) {
        const idx = Math.floor(Math.random() * copy.length);
        res.push(copy.splice(idx, 1)[0]);
    }
    return res;
}

function formatNames(list) {
    const names = list.map(x => x.name);
    if (names.length === 1) return names[0];
    if (names.length === 2) return `${names[0]} и ${names[1]}`;
    const last = names.pop();
    return `${names.join(', ')} и ${last}`;
}

function randomFrom(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

function generateEvent() {
    const actionType = weightedChoice(WEIGHTS); // 0 = одиночный, 1 = plural

    if (actionType === 0) {
        // одиночное действие
        const person = pickUnique(PEOPLE, 1)[0];
        const gender = person.gender === 'female' ? 'female' : 'male';
        const tpl = randomFrom(ACTIONS_ONE[gender]);
        return tpl.replace('{name}', person.name);
    } else {
        // групповое действие
        const count = Math.floor(Math.random() * 3) + 2; // 2, 3 или 4
        const chosen = pickUnique(PEOPLE, count);
        const namesStr = formatNames(chosen);
        let tpl = randomFrom(ACTIONS_PLURAL);

        if (tpl.includes('{names}')) {
            return tpl.replace('{names}', namesStr);
        } else {
            return `${namesStr} ${tpl}`;
        }
    }
}

function getTargetChatId() {
    if (process.env.CHAT_ID) return process.env.CHAT_ID;
    const cfg = readConfig();
    return cfg.chatId;
}

async function sendEventToTarget() {
    const chatId = getTargetChatId();
    if (!chatId) {
        console.warn('Чат не задан — используй /register или укажи CHAT_ID в .env');
        return;
    }
    const text = generateEvent();
    try {
        await bot.sendMessage(chatId, text);
        console.log('Отправлено событие:', text);
    } catch (err) {
        console.error('Ошибка отправки:', err?.response?.body || err);
    }
}

// Команды
bot.onText(/\/start/, (msg) => {
    const chatId = msg.chat.id;
    bot.sendMessage(chatId, 'Привет! Я бот-генератор событий для ПВЗ.\n\n/generate — сгенерировать событие\n/register — включить автопостинг\n/unregister — выключить автопостинг', {
        reply_markup: {
            inline_keyboard: [[{ text: 'Сгенерировать действие', callback_data: 'generate' }]]
        }
    }).catch(console.error);
});

bot.onText(/\/generate/, (msg) => {
    const chatId = msg.chat.id;
    bot.sendMessage(chatId, generateEvent()).catch(console.error);
});

bot.onText(/\/register/, (msg) => {
    const chatId = msg.chat.id;
    const cfg = readConfig();
    cfg.chatId = chatId;
    writeConfig(cfg);
    bot.sendMessage(chatId, 'Этот чат зарегистрирован для автопостинга.');
});

bot.onText(/\/unregister/, (msg) => {
    const chatId = msg.chat.id;
    const cfg = readConfig();
    if (cfg.chatId && String(cfg.chatId) === String(chatId)) {
        delete cfg.chatId;
        writeConfig(cfg);
        bot.sendMessage(chatId, 'Автопостинг отключён.');
    } else {
        bot.sendMessage(chatId, 'Этот чат не был зарегистрирован.');
    }
});

bot.on('callback_query', (query) => {
    if (query.data === 'generate') {
        const chatId = query.message.chat.id;
        bot.sendMessage(chatId, generateEvent())
            .then(() => bot.answerCallbackQuery(query.id, { text: 'Сгенерировано' }))
            .catch(err => {
                console.error(err);
                bot.answerCallbackQuery(query.id, { text: 'Ошибка' });
            });
    } else {
        bot.answerCallbackQuery(query.id, { text: 'Неизвестная команда' });
    }
});

bot.on('polling_error', (err) => {
    console.error('Polling error', err);
});

// Автопостинг
let scheduler = null;
function startScheduler() {
    const minutes = parseFloat(process.env.INTERVAL_MINUTES);
    const hours = parseFloat(process.env.INTERVAL_HOURS);

    let intervalMs = null;
    if (Number.isFinite(minutes) && minutes > 0) {
        intervalMs = minutes * 60 * 1000;
        console.log(`Интервал: ${minutes} минут`);
    } else if (Number.isFinite(hours) && hours > 0) {
        intervalMs = hours * 60 * 60 * 1000;
        console.log(`Интервал: ${hours} часов`);
    } else {
        intervalMs = 12 * 60 * 60 * 1000;
        console.log('Интервал не задан — по умолчанию 12 часов');
    }

    if (intervalMs <= 0) {
        console.log('Автопостинг отключён.');
        return;
    }

    if (['1', 'true', 'yes'].includes(String(process.env.SEND_ON_START).toLowerCase())) {
        sendEventToTarget().catch(console.error);
    }

    scheduler = setInterval(() => {
        sendEventToTarget().catch(console.error);
    }, intervalMs);
}

function stopScheduler() {
    if (scheduler) {
        clearInterval(scheduler);
        scheduler = null;
    }
}

process.on('SIGINT', () => {
    console.log('Остановка бота...');
    stopScheduler();
    process.exit(0);
});
process.on('SIGTERM', () => {
    console.log('Остановка бота...');
    stopScheduler();
    process.exit(0);
});

startScheduler();
console.log('Bot started. Use /start, /generate, /register.');
