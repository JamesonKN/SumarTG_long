# 🤖 Telegram Bot pentru Rezumate LUNGI de Știri

Bot care primește link-uri către articole și returnează rezumate de **850-950 caractere** în română, formatate pentru newsletter.

## Exemplu output

```
🏥 **Onioptic Medical a deschis** un nou ambulatoriu în Craiova, rezultat al unei investiții europene majore. Spitalul privat cu capital integral românesc, fondat în 1997, oferă servicii oftalmologice și imagistică medicală cu echipamente RMN 3 Tesla și CT de ultimă generație. În 2025, Onioptic a devenit singurul spital oftalmologic din România și Europa certificat ca Centru de Excelență de către Surgical Review Corporation.
```

---

## 🚀 Setup pas cu pas

### Pasul 1: Creează botul Telegram

1. Deschide Telegram și caută `@BotFather`
2. Trimite `/newbot`
3. Alege un nume (ex: "News Summary Long Bot")
4. Alege un username (ex: "dumitru_news_long_bot")
5. **Salvează TOKEN-ul** primit

### Pasul 2: Obține API Key Anthropic

1. Mergi la [console.anthropic.com](https://console.anthropic.com)
2. Creează cont sau loghează-te
3. În Settings → API Keys → Create Key
4. **Salvează cheia**

### Pasul 3: Deployment pe Railway

1. Creează cont pe [railway.app](https://railway.app)
2. Click "New Project" → "Deploy from GitHub repo"
3. Conectează-ți GitHub și urcă acest cod
4. În Settings → Variables, adaugă:
   - `TELEGRAM_TOKEN` = token-ul de la BotFather
   - `ANTHROPIC_API_KEY` = cheia de la Anthropic
5. Railway va porni automat botul

---

## 📁 Structura fișierelor

```
telegram-summary-bot-long/
├── bot.py              # Codul principal
├── requirements.txt    # Dependențe Python
├── runtime.txt         # Versiune Python
├── Procfile           # Comandă pentru Railway
└── README.md          # Acest fișier
```

---

## 🎯 Cum folosești botul

1. Deschide botul în Telegram
2. Apasă Start sau trimite `/start`
3. Forwardează sau trimite orice link către un articol
4. Primești rezumatul formatat în 5-10 secunde

---

## 💰 Costuri estimate

| Volum | Cost estimat |
|-------|--------------|
| 50 articole/zi | ~4-6 USD/lună |
| 100 articole/zi | ~8-12 USD/lună |
| 200 articole/zi | ~16-24 USD/lună |

*Notă: Rezumatele lungi consumă mai multe tokens decât cele scurte.*

---

## 🔧 Troubleshooting

**Botul nu răspunde:**
- Verifică dacă TOKEN-ul e corect
- Verifică logs în Railway

**"Nu am putut extrage conținutul":**
- Unele site-uri blochează scraping-ul
- Încearcă alt link sau lipește textul direct

**Rezumatul e prea lung/scurt:**
- Claude respectă aproximativ limita, ±50 caractere e normal
