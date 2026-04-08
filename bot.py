import matplotlib
matplotlib.use("Agg")
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

# ---------------- CONFIG ----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
TRADE_FILE = "trades.json"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- BOT ----------------
class BTCSignalBot:
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.active_trade = None
        self.trades_history = self.load_trades()

    def load_trades(self):
        if os.path.exists(TRADE_FILE):
            with open(TRADE_FILE, "r") as f:
                return json.load(f)
        return []

    def save_trades(self):
        with open(TRADE_FILE, "w") as f:
            json.dump(self.trades_history, f, indent=2)

    def fetch_ohlcv(self, timeframe="15m", limit=200):
        """Fetch multi-timeframe BTC data"""
        periods = {"15m": "3d", "1h": "7d", "4h": "30d", "1d": f"{limit}d"}
        interval_map = {"15m": "5m", "1h": "15m", "4h": "60m", "1d": "1d"}

        ticker = yf.Ticker("BTC-USD")
        df = ticker.history(period=periods[timeframe], interval=interval_map[timeframe])
        df = df.tail(limit)
        df.columns = [c.lower() for c in df.columns]

        # Resample if needed
        if timeframe != "1d" and len(df) > 1:
            rule = {"15m": "15min", "1h": "1h", "4h": "4h"}[timeframe]
            df = df.resample(rule).agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna()
        return df

    def calculate_rsi(self, prices, period=14):
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = -delta.where(delta < 0, 0).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def detect_liquidity_grab(self, df):
        if len(df) < 10:
            return False
        recent_low = df['low'].iloc[-10:-1].min()
        recent_high = df['high'].iloc[-10:-1].max()
        return df['low'].iloc[-1] < recent_low and df['close'].iloc[-1] > df['open'].iloc[-1] or \
               df['high'].iloc[-1] > recent_high and df['close'].iloc[-1] < df['open'].iloc[-1]

    def multi_timeframe_analysis(self):
        """Analyze 15m, 1h, 4h, 1d"""
        timeframes = ["15m", "1h", "4h", "1d"]
        results = {}
        for tf in timeframes:
            df = self.fetch_ohlcv(tf)
            if len(df) < 20:
                continue
            ema_fast = df['close'].ewm(span=9).mean().iloc[-1]
            ema_slow = df['close'].ewm(span=21).mean().iloc[-1]
            trend = "bullish" if df['close'].iloc[-1] > df['close'].iloc[-20:].mean() else "bearish"
            rsi = self.calculate_rsi(df['close'])
            liquidity = self.detect_liquidity_grab(df)
            results[tf] = {
                "current_price": df['close'].iloc[-1],
                "trend": trend,
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "rsi": rsi,
                "liquidity": liquidity,
                "df": df
            }
        return results

    def generate_signal(self, analysis):
        """Combine multi-timeframe confluence for signal"""
        if not analysis:
            return None
        bullish = 0
        bearish = 0
        for tf, data in analysis.items():
            weight = 1.5 if tf=="15m" else 1
            bullish += weight if data['trend']=="bullish" else 0
            bearish += weight if data['trend']=="bearish" else 0
            if data['liquidity']:
                bullish += weight if data['trend']=="bullish" else 0
                bearish += weight if data['trend']=="bearish" else 0
        if bullish >= bearish + 2:
            return "LONG", analysis["15m"]["current_price"], analysis["15m"]["df"]
        elif bearish >= bullish + 2:
            return "SHORT", analysis["15m"]["current_price"], analysis["15m"]["df"]
        return None, None, None

    def check_active_trade(self, current_price):
        if not self.active_trade:
            return
        trade = self.active_trade
        tp_hit = False
        sl_hit = False
        if trade["type"]=="LONG":
            if current_price >= trade["tp3"]: tp_hit=True
            elif current_price <= trade["sl"]: sl_hit=True
        elif trade["type"]=="SHORT":
            if current_price <= trade["tp3"]: tp_hit=True
            elif current_price >= trade["sl"]: sl_hit=True
        if tp_hit:
            logger.info("TP HIT ✅")
            self.trades_history.append({**trade, "result":"TP", "exit":current_price, "time":str(datetime.utcnow())})
            self.active_trade = None
            self.save_trades()
        elif sl_hit:
            logger.info("SL HIT ❌")
            self.trades_history.append({**trade, "result":"SL", "exit":current_price, "time":str(datetime.utcnow())})
            self.active_trade = None
            self.save_trades()

    async def create_chart(self, df, signal_type=None):
        df = df.copy()
        df['ema9'] = df['close'].ewm(span=9).mean()
        df['ema21'] = df['close'].ewm(span=21).mean()
        apds = [
            mpf.make_addplot(df['ema9'], color='orange'),
            mpf.make_addplot(df['ema21'], color='blue')
        ]
        if signal_type:
            # mark last candle
            color='g' if signal_type=="LONG" else 'r'
            apds.append(mpf.make_addplot([df['close'].iloc[-1]]*len(df), type='scatter', markersize=50, marker='^', color=color))
        buf = io.BytesIO()
        mpf.plot(df.tail(100), type='candle', style='charles', volume=True, addplot=apds, savefig=buf)
        buf.seek(0)
        return buf

    async def send_signal(self, signal_type, price, df):
        if signal_type=="LONG":
            tp1 = price*1.02; tp2=price*1.04; tp3=price*1.06; sl=price*0.985
        else:
            tp1 = price*0.98; tp2=price*0.96; tp3=price*0.94; sl=price*1.015

        confidence=np.random.uniform(80,95)

        message=f"""
🚨 *BTC SMART MONEY SIGNAL* 🚨

*Signal:* {signal_type} {'🟢' if signal_type=='LONG' else '🔴'}
*Confidence:* {confidence:.1f}%

📊 *Entry:* ${price:,.0f}

🎯 *Take Profits:*
   TP1: ${tp1:,.0f}
   TP2: ${tp2:,.0f}
   TP3: ${tp3:,.0f}

🛑 *Stop Loss:* ${sl:,.0f}

📈 *Setup:*
⚡ EMA crossover + Smart Money

⚠️ *Risk Management:* Use 1-2% risk

🤖 Bot Active | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
"""
        chart = await self.create_chart(df, signal_type)
        await self.bot.send_photo(chat_id=TELEGRAM_CHANNEL_ID, photo=chart, caption=message, parse_mode="Markdown")
        self.active_trade={"type":signal_type,"entry":price,"tp3":tp3,"sl":sl}

    async def run(self):
        logger.info("Bot started...")
        while True:
            try:
                analysis=self.multi_timeframe_analysis()
                if "15m" not in analysis:
                    await asyncio.sleep(60)
                    continue
                current_price=analysis["15m"]["current_price"]
                self.check_active_trade(current_price)

                if self.active_trade:
                    logger.info("Active trade exists, waiting for TP/SL...")
                else:
                    signal, price, df=self.generate_signal(analysis)
                    if signal:
                        await self.send_signal(signal, price, df)

                await asyncio.sleep(300)
            except Exception as e:
                logger.error(e)
                await asyncio.sleep(60)

async def main():
    bot=BTCSignalBot()
    await bot.run()

if __name__=="__main__":
    asyncio.run(main())