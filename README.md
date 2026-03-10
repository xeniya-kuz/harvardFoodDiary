# Food Diary Telegram Bot

A Telegram bot for tracking meals and analyzing nutrition using AI. The bot analyzes food photos or text descriptions, evaluates meals against the Harvard Healthy Eating Plate model, and provides personalized daily recommendations.

## Features

- **Photo analysis** — send a food photo and get an instant AI-powered breakdown
- **Text input** — describe your meal in text if no photo is available
- **Harvard Plate scoring** — each meal is evaluated on fiber (50%), slow carbs (25%), and protein (25%) balance
- **Hunger & satiety tracking** — log how hungry you were before and how full you felt after
- **Meal history** — browse past meals by day using a calendar view
- **Daily advice** — get personalized nutrition tips based on everything you ate that day
- **Data export** — export your meal history as a JSON file

## Tech Stack

- **Python 3.11+**
- **python-telegram-bot 20** — async Telegram bot framework
- **Google Gemini API** (`gemini-2.5-flash`) — multimodal AI for food recognition and analysis
- **SQLite** — local meal storage
- **Pillow** — image preprocessing before sending to AI

## Project Structure

```
bot.py          — Telegram bot, conversation flow, command handlers
ai_analyzer.py  — Gemini API integration, photo and text analysis prompts
database.py     — SQLite schema and data access functions
config.py       — environment variables and model configuration
```

## Setup

1. Clone the repository
2. Create a virtual environment and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your credentials:
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token
   GEMINI_API_KEY=your_gemini_api_key
   ```
4. Run the bot:
   ```bash
   python bot.py
   ```

## Environment Variables

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `GEMINI_API_KEY` | API key from [Google AI Studio](https://aistudio.google.com) |
| `DB_PATH` | Path to SQLite database file (default: `food_diary.db`) |

## Deployment

The bot is designed to run as a background worker (long polling). It can be deployed to any platform that supports Python:

**Railway:**
1. Push the repository to GitHub
2. Create a new project on [railway.app](https://railway.app) from the GitHub repo
3. Add environment variables in the Railway dashboard
4. Set the start command to `python bot.py`

## AI Model Fallback

The bot automatically switches between Gemini models if one is rate-limited or unavailable:

```
gemini-2.5-flash → gemini-2.5-pro → gemini-2.0-flash
```

If all models are temporarily overloaded, the bot waits 25 seconds and retries automatically.
