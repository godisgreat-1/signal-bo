
import matplotlib
matplotlib.use('Agg')

import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from telegram import Bot
import mplfinance as mpf
import io
import logging
import json
import os

# ------------------------------
# CONFIG (ENV VARIABLES)
# ------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sent_trades_file = "sent_trades.json"
if os.path.exists(sent_trades_file):
    with open(sent_trades_file, "r") as f:
        sent_trades = json.load(f)
else:
    sent_trades = {}


class BTCSignalBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.last_signal_time = None

    def fetch_data(self):
        df = yf.Ticker("BTC-USD").history(period="3d", interval="15m")
        df.columns = [c.lower() for c in df.columns]
        return df

    def analyze(self, df):
        df['ema9'] = df['close'].ewm(span=9).mean()
        df['ema21'] = df['close'].ewm(span=21).mean()

        last = df.iloc[-1]

        if last['ema9'] > last['ema21']:
            return "LONG", last['close']
        elif last['ema9'] < last['ema21']:
            return "SHORT", last['close']
        return None, last['close']

    async def create_chart(self, df):
        buffer = io.BytesIO()
        mpf.plot(df.tail(100), type='candle', volume=True, savefig=buffer)
        buffer.seek(0)
        return buffer

    async def send_signal(self, signal_type, price, df):
        message = f"""
🚨 BTC SIGNAL 🚨

Type: {signal_type}
Entry: ${price:,.0f}

TP1: ${price * 1.02:,.0f}
TP2: ${price * 1.04:,.0f}
SL: ${price * 0.98:,.0f}

Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
        """

        chart = await self.create_chart(df)

        await self.bot.send_photo(
            chat_id=TELEGRAM_CHANNEL_ID,
            photo=chart,
            caption=message
        )

    async def run(self):
        logger.info("Bot started...")

        while True:
            try:
                df = self.fetch_data()
                signal, price = self.analyze(df)

                if signal:
                    await self.send_signal(signal, price, df)

                await asyncio.sleep(300)

            except Exception as e:
                logger.error(e)
                await asyncio.sleep(60)


async def main():
    bot = BTCSignalBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())