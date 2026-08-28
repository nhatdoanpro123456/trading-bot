import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
import aiohttp
import pandas as pd

from config import (
    SYMBOL, TIMEFRAMES, ALL_TFS, MAX_KLINES_PER_TF,
    BINANCE_FUTURES_REST_URL, BINANCE_WS_URL,
    BB_LENGTH, BB_MULT, BB_SQUEEZE_TFS, BB_SQUEEZE_LOOKBACK, BB_SQUEEZE_PERCENTILE,
    FVG_TFS, FVG_FILTER_PCT, FVG_MAX_ZONES,
    OB_TFS, OB_ATR_LENGTH, OB_FILTER_PCT, OB_MAX_ZONES,
    DIVERGENCE_TFS, RSI_LENGTH, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    DIVERGENCE_PIVOT_LEFT, DIVERGENCE_PIVOT_RIGHT,
    DIVERGENCE_RANGE_LOWER, DIVERGENCE_RANGE_UPPER,
    CHOCH_TFS, SWING_LEFT, SWING_RIGHT,
    HTF_BIAS_TF_HIGH, HTF_BIAS_TF_LOW,
    DISPLACEMENT_BODY_LOOKBACK, DISPLACEMENT_BODY_MULT,
    DISPLACEMENT_ATR_LENGTH, DISPLACEMENT_BREAK_ATR_MULT,
    EMA_TFS, EMA_LENGTH,
    ROUND_NUMBER_STEP,
)
from indicators import (
    calc_bb_squeeze, calc_fvg_zones, calc_order_blocks,
    calc_rsi, calc_macd, detect_new_divergences,
    detect_choch_events, calc_structure_trend, calc_premium_discount_zone,
    calc_ema,
)
from state_manager import StateManager
from discord_notifier import DiscordNotifier

# Khung cần tính FVG: hợp nhất khung có cảnh báo "chạm FVG" thông thường (FVG_TFS) với
# khung cần FVG để ghép setup CHoCH+FVG (CHOCH_TFS, gồm cả 15m dù 15m không có touch-alert)
FVG_COMPUTE_TFS = set(FVG_TFS) | set(CHOCH_TFS)

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CryptoBotMTF")


class BinanceFuturesMTFBot:
    def __init__(self):
        self.symbol = SYMBOL
        # ALL_TFS = hợp nhất TIMEFRAMES (BB/FVG/OB) + DIVERGENCE_TFS (Phân kỳ RSI/MACD)
        # -> hiện là 9 khung: 5m, 15m, 30m, 1h, 2h, 4h, 12h, 1d, 1w
        self.timeframes = ALL_TFS
        self.state_mgr = StateManager(self.timeframes)
        self.session: aiohttp.ClientSession | None = None
        self.notifier = DiscordNotifier()

        # Lưu trữ lịch sử nến cho từng khung
        self.klines_data: dict[str, pd.DataFrame] = {}
        self.current_price = 0.0
        self.is_running = True

    async def get_session(self) -> aiohttp.ClientSession:
        """Khởi tạo hoặc tái sử dụng session HTTP duy nhất."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
            self.notifier.session = self.session
            self.notifier._owns_session = False
        return self.session

    async def fetch_historical_klines(self, tf: str) -> pd.DataFrame:
        """Lấy nến lịch sử từ Binance Futures REST API.
        Cả 9 khung (5m/15m/30m/1h/2h/4h/12h/1d/1w) đều là interval gốc của Binance nên không cần resample.

        QUAN TRỌNG: Binance REST /klines thường trả về CẢ nến đang hình thành (chưa đóng)
        ở vị trí cuối cùng của mảng kết quả. calc_fvg_zones()/calc_order_blocks() dùng
        chính nến cuối (index i) làm mốc XÁC NHẬN gap/OB, nên nếu không lọc bỏ nến chưa
        đóng này, FVG/OB sẽ bị xác nhận SỚM (sai), không khớp logic gốc BigBeluga (chỉ
        xác nhận sau khi nến điều kiện đã đóng hẳn). Vì vậy ta luôn loại bỏ mọi nến có
        close_time > thời điểm hiện tại trước khi trả về dữ liệu."""
        session = await self.get_session()
        url = f"{BINANCE_FUTURES_REST_URL}/klines"
        params = {
            "symbol": self.symbol,
            "interval": tf,
            # +1 để bù lại khả năng phải loại bỏ 1 nến cuối do chưa đóng (xem lọc bên dưới)
            "limit": MAX_KLINES_PER_TF + 1
        }
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    df = pd.DataFrame(data, columns=[
                        "open_time", "open", "high", "low", "close", "volume",
                        "close_time", "quote_volume", "trades", "taker_base_vol",
                        "taker_quote_vol", "ignore"
                    ])
                    df["open"] = df["open"].astype(float)
                    df["high"] = df["high"].astype(float)
                    df["low"] = df["low"].astype(float)
                    df["close"] = df["close"].astype(float)
                    df["volume"] = df["volume"].astype(float)
                    df["close_time"] = df["close_time"].astype("int64")

                    # --- CHỈ giữ nến ĐÃ ĐÓNG: bỏ nến cuối nếu close_time > thời điểm hiện tại ---
                    now_ms = int(time.time() * 1000)
                    closed_df = df[df["close_time"] <= now_ms].copy()
                    dropped = len(df) - len(closed_df)
                    if dropped > 0:
                        logger.debug(f"[{tf}] Đã loại {dropped} nến chưa đóng khỏi dữ liệu REST.")

                    closed_df["open_time"] = pd.to_datetime(closed_df["open_time"], unit="ms")
                    clean_df = closed_df[["open_time", "open", "high", "low", "close", "volume"]]
                    return clean_df.iloc[-MAX_KLINES_PER_TF:]
                else:
                    logger.error(f"Lỗi tải nến {tf} (Status {resp.status}) từ Binance")
        except Exception as e:
            logger.error(f"Lỗi kết nối REST API cho khung {tf}: {e}")
        return pd.DataFrame()

    async def warm_up_indicators(self):
        """Tải dữ liệu ban đầu cho toàn bộ khung thời gian (BB/FVG/OB + Divergence) để kích hoạt chỉ báo."""
        logger.info(f"Đang tải dữ liệu lịch sử cho {len(self.timeframes)} khung thời gian ({self.symbol})...")
        tasks = [self.fetch_historical_klines(tf) for tf in self.timeframes]
        results = await asyncio.gather(*tasks)

        for tf, df in zip(self.timeframes, results):
            if not df.empty:
                self.klines_data[tf] = df
                logger.info(f"-> Đã nạp {len(df)} nến cho khung {tf}")

        if "15m" in self.klines_data and not self.klines_data["15m"].empty:
            self.current_price = float(self.klines_data["15m"]["close"].iloc[-1])

        # Tính toán chỉ báo lần đầu (is_initial=True -> không gửi cảnh báo cho dữ liệu warm-up)
        self.recalculate_all_indicators(is_initial=True)
        logger.info(f"✅ Đã làm nóng toàn bộ chỉ báo thành công! Giá khởi tạo: ${self.current_price:,.1f}")

    def recalculate_all_indicators(self, is_initial: bool = False):
        """Tính lại BB-Squeeze, FVG, Order Block, Phân kỳ RSI/MACD cho từng khung khi có nến vừa đóng."""
        all_alerts = []
        min_divergence_len = (
            DIVERGENCE_PIVOT_LEFT + DIVERGENCE_PIVOT_RIGHT + DIVERGENCE_RANGE_UPPER
            + MACD_SLOW + MACD_SIGNAL + 10
        )
        for tf in self.timeframes:
            df = self.klines_data.get(tf)
            min_len = max(BB_SQUEEZE_LOOKBACK, OB_ATR_LENGTH, min_divergence_len) + 5
            if df is None or len(df) < min_len:
                continue

            # 1. Bollinger Bands + Squeeze
            basis_s, upper_s, lower_s, bw_s, is_squeeze = calc_bb_squeeze(
                df["close"], length=BB_LENGTH, mult=BB_MULT,
                lookback=BB_SQUEEZE_LOOKBACK, percentile_threshold=BB_SQUEEZE_PERCENTILE
            )
            bb_basis = float(basis_s.iloc[-1]) if not pd.isna(basis_s.iloc[-1]) else 0.0
            bb_upper = float(upper_s.iloc[-1]) if not pd.isna(upper_s.iloc[-1]) else 0.0
            bb_lower = float(lower_s.iloc[-1]) if not pd.isna(lower_s.iloc[-1]) else 0.0
            bb_bandwidth = float(bw_s.iloc[-1]) if not pd.isna(bw_s.iloc[-1]) else 0.0
            bb_squeeze = bool(is_squeeze) if tf in BB_SQUEEZE_TFS else False

            # 2. Fair Value Gap (FVG) - tính cho FVG_TFS (cảnh báo chạm) + CHOCH_TFS (ghép setup)
            fvg_bull, fvg_bear = [], []
            if tf in FVG_COMPUTE_TFS:
                fvg_bull, fvg_bear = calc_fvg_zones(df, filter_pct=FVG_FILTER_PCT, max_zones=FVG_MAX_ZONES)

            # 3. Order Block (OB)
            ob_bull, ob_bear = [], []
            if tf in OB_TFS:
                ob_bull, ob_bear = calc_order_blocks(
                    df, atr_length=OB_ATR_LENGTH, filter_pct=OB_FILTER_PCT, max_zones=OB_MAX_ZONES
                )

            # 3b. EMA200 (dùng để check HỢP LƯU với vùng OB -> Super Buy/Sell)
            ema200 = 0.0
            if tf in EMA_TFS:
                ema_s = calc_ema(df["close"], length=EMA_LENGTH)
                ema200 = float(ema_s.iloc[-1]) if not pd.isna(ema_s.iloc[-1]) else 0.0

            # 4. Phân kỳ/Hội tụ (Divergence) - RSI + MACD Histogram, đủ 4 loại tín hiệu
            divergence_signals = []
            if tf in DIVERGENCE_TFS:
                rsi_series = calc_rsi(df["close"], length=RSI_LENGTH)
                _, _, macd_hist = calc_macd(df["close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
                divergence_signals += detect_new_divergences(
                    df, rsi_series, "RSI",
                    left=DIVERGENCE_PIVOT_LEFT, right=DIVERGENCE_PIVOT_RIGHT,
                    range_lower=DIVERGENCE_RANGE_LOWER, range_upper=DIVERGENCE_RANGE_UPPER,
                )
                divergence_signals += detect_new_divergences(
                    df, macd_hist, "MACD",
                    left=DIVERGENCE_PIVOT_LEFT, right=DIVERGENCE_PIVOT_RIGHT,
                    range_lower=DIVERGENCE_RANGE_LOWER, range_upper=DIVERGENCE_RANGE_UPPER,
                )

            # 5. Market Structure - CHoCH (Change of Character) kèm displacement, chỉ CHOCH_TFS
            choch_events = []
            htf_bias = None
            premium_discount = (None, None, None)
            if tf in CHOCH_TFS:
                choch_events = detect_choch_events(
                    df, left=SWING_LEFT, right=SWING_RIGHT,
                    body_lookback=DISPLACEMENT_BODY_LOOKBACK, body_mult=DISPLACEMENT_BODY_MULT,
                    atr_length=DISPLACEMENT_ATR_LENGTH, break_atr_mult=DISPLACEMENT_BREAK_ATR_MULT,
                )

                # Bias khung lớn (Rule 1): dùng dữ liệu H4/H1 ĐANG CÓ trong bộ nhớ (không
                # cần đúng lúc H4/H1 vừa đóng nến - lấy trạng thái cấu trúc mới nhất hiện có).
                h4_df = self.klines_data.get(HTF_BIAS_TF_HIGH)
                h1_df = self.klines_data.get(HTF_BIAS_TF_LOW)
                htf_bias = {
                    "h4": calc_structure_trend(h4_df, left=SWING_LEFT, right=SWING_RIGHT)
                    if h4_df is not None and not h4_df.empty else None,
                    "h1": calc_structure_trend(h1_df, left=SWING_LEFT, right=SWING_RIGHT)
                    if h1_df is not None and not h1_df.empty else None,
                }

                # Premium/Discount (Rule 5): tính trên chính khung `tf` đang xét.
                premium_discount = calc_premium_discount_zone(df, left=SWING_LEFT, right=SWING_RIGHT)

            last_close = float(df["close"].iloc[-1])
            last_high = float(df["high"].iloc[-1])
            last_low = float(df["low"].iloc[-1])

            data = {
                "close": last_close,
                "last_high": last_high,
                "last_low": last_low,
                "bb_upper": bb_upper,
                "bb_lower": bb_lower,
                "bb_basis": bb_basis,
                "bb_bandwidth": bb_bandwidth,
                "bb_squeeze": bb_squeeze,
                "fvg_bull": fvg_bull,
                "fvg_bear": fvg_bear,
                "ob_bull": ob_bull,
                "ob_bear": ob_bear,
                "ema200": ema200,
                "divergence_signals": divergence_signals,
                "choch_events": choch_events,
                "htf_bias": htf_bias,
                "premium_discount": premium_discount,
            }

            alerts = self.state_mgr.update_zones_on_close(tf, data)
            all_alerts.extend(alerts)

        # Nếu có cảnh báo (và không phải lần chạy đầu tiên warm-up), gửi tin nhắn
        if all_alerts and not is_initial:
            for msg in all_alerts:
                asyncio.create_task(self.notifier.send_message(msg))

    async def handle_candle_close(self, close_time_ms: int):
        """
        Được gọi ngay khi nến 1m đóng.
        Xác định các khung nào (trong 9 khung 5m/15m/30m/1h/2h/4h/12h/1d/1w) vừa đóng
        nến tại mốc thời gian này. Khung tuần (1w) trên Binance luôn đóng vào lúc
        00:00 UTC thứ Hai (weekday() == 0).
        """
        canonical_ts = (close_time_ms + 1) / 1000.0
        dt = datetime.fromtimestamp(canonical_ts, tz=timezone.utc)
        minute = dt.minute
        hour = dt.hour
        weekday = dt.weekday()  # Thứ Hai = 0

        tfs_to_update = []
        if minute % 5 == 0:
            tfs_to_update.append("5m")
        if minute % 15 == 0:
            tfs_to_update.append("15m")
        if minute % 30 == 0:
            tfs_to_update.append("30m")
        if minute == 0:
            tfs_to_update.append("1h")
            if hour % 2 == 0:
                tfs_to_update.append("2h")
            if hour % 4 == 0:
                tfs_to_update.append("4h")
            if hour % 12 == 0:
                tfs_to_update.append("12h")
            if hour == 0:
                tfs_to_update.append("1d")
                if weekday == 0:
                    tfs_to_update.append("1w")

        if tfs_to_update:
            logger.info(f"⚡ Đóng nến mốc {minute:02d}m. Cập nhật tức thì khung: {', '.join(tfs_to_update)}")
            tasks = [self.fetch_historical_klines(tf) for tf in tfs_to_update]
            results = await asyncio.gather(*tasks)
            for tf, df in zip(tfs_to_update, results):
                if not df.empty:
                    self.klines_data[tf] = df

            self.recalculate_all_indicators()

    def get_dashboard_text(self) -> str:
        """Trả về bảng tổng hợp MTF (BB Squeeze / OB / FVG gần giá nhất) - dùng cho gửi định kỳ qua Discord."""
        return self.state_mgr.build_dashboard_text(self.symbol, self.current_price)

    async def stream_binance_websocket(self):
        """Lắng nghe luồng WebSocket thời gian thực (Cập nhật giá theo mili-giây)."""
        stream_name = f"{self.symbol.lower()}@kline_1m"
        ws_url = f"{BINANCE_WS_URL}/{stream_name}"
        session = await self.get_session()

        while self.is_running:
            try:
                logger.info(f"Đang kết nối Binance WebSocket: {ws_url}")
                async with session.ws_connect(ws_url, heartbeat=20) as ws:
                    logger.info("⚡ Kết nối WebSocket Realtime thành công! Bắt đầu giám sát 24/7...")
                    async for msg in ws:
                        if not self.is_running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            kline = payload.get("k", {})
                            if not kline:
                                continue

                            close_price = float(kline.get("c", 0))
                            is_closed = kline.get("x", False)
                            self.current_price = close_price

                            # 1. Giá chạm mức giá chẵn (VD 70000, 71000...)
                            round_alerts = self.state_mgr.check_round_number_touch(self.current_price)
                            for alert in round_alerts:
                                asyncio.create_task(self.notifier.send_message(alert))

                            # 2. Giá chạm vùng FVG (tăng/giảm) kèm số lần chạm - khung 1h/4h/1d
                            fvg_alerts = self.state_mgr.check_fvg_touch(self.current_price)
                            for alert in fvg_alerts:
                                asyncio.create_task(self.notifier.send_message(alert))

                            # 3 & 4. Giá sắp chạm / đã chạm vùng OB kèm số lần chạm - khung m15/1h/4h/1d
                            ob_alerts = self.state_mgr.check_ob_touch_and_approach(self.current_price)
                            for alert in ob_alerts:
                                asyncio.create_task(self.notifier.send_message(alert))

                            # 5. Setup Buy/Sell tỷ lệ thắng cao: giá vừa hồi về đúng vùng FVG
                            # của 1 setup CHoCH+FVG đang chờ (m15/h1/h4)
                            setup_alerts = self.state_mgr.check_pending_setup_retest(self.current_price)
                            for alert in setup_alerts:
                                asyncio.create_task(self.notifier.send_message(alert))

                            # 5, 6 & 7: khi nến 1m đóng -> xác định khung nào (trong 9 khung) vừa
                            # đóng nến để tính lại FVG/OB/BB-squeeze/Phân kỳ RSI+MACD tương ứng
                            # (phát cảnh báo giữ/thủng OB + squeeze + phân kỳ mới)
                            if is_closed:
                                close_time_ms = int(kline.get("T", 0))
                                asyncio.create_task(self.handle_candle_close(close_time_ms))

                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("WebSocket bị đóng hoặc lỗi, đang kết nối lại...")
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Lỗi kết nối WebSocket: {e}. Thử lại sau 5 giây...")
                await asyncio.sleep(5)

    async def periodic_sync_task(self):
        """Định kỳ 10 phút đồng bộ lại toàn bộ khung thời gian để đảm bảo dữ liệu luôn chuẩn."""
        while self.is_running:
            await asyncio.sleep(600)
            try:
                logger.info(f"Định kỳ đồng bộ lại dữ liệu {len(self.timeframes)} khung ({', '.join(self.timeframes)})...")
                tasks = [self.fetch_historical_klines(tf) for tf in self.timeframes]
                results = await asyncio.gather(*tasks)
                for tf, df in zip(self.timeframes, results):
                    if not df.empty:
                        self.klines_data[tf] = df
                self.recalculate_all_indicators()
            except Exception as e:
                logger.error(f"Lỗi khi định kỳ đồng bộ: {e}")

    async def run(self):
        """Khởi chạy toàn bộ hệ thống bot."""
        # 1. Warm-up
        await self.get_session()
        await self.warm_up_indicators()

        # Gửi thông báo khởi động qua Discord
        start_msg = (
            f"🚀 <b>BOT {self.symbol} MTF ĐÃ CẬP NHẬT HOÀN TẤT!</b>\n\n"
            f"💵 <b>Giá hiện tại:</b> <code>${self.current_price:,.1f}</code>\n"
            f"📊 <b>Giám sát BB/FVG/OB:</b> <code>{', '.join(TIMEFRAMES)}</code>\n"
            f"📈 <b>Giám sát Phân kỳ (RSI+MACD):</b> <code>{', '.join(DIVERGENCE_TFS)}</code>\n"
            f"🔀 <b>Giám sát CHoCH + Setup:</b> <code>{', '.join(CHOCH_TFS)}</code>\n"
            f"⚡ <b>Độ trễ:</b> Real-time (Phản hồi đóng nến &lt; 1s)\n"
            f"🔔 <b>Cảnh báo đang bật:</b>\n"
            f"• Giá chạm mốc giá chẵn (bước {ROUND_NUMBER_STEP:,.0f})\n"
            f"• [1h/4h/1D] Giá chạm vùng FVG (tăng/giảm) kèm số lần chạm\n"
            f"• [{', '.join(OB_TFS)}] Giá sắp chạm / đã chạm vùng OB kèm gợi ý SETUP BUY/SELL "
            f"tiềm năng (SL/TP tham khảo) - mỗi cây nến chỉ báo 1 lần\n"
            f"• [{', '.join(OB_TFS)}] Nến đóng cửa giữ vùng OB / OB đã bị thủng (trên hoặc dưới)\n"
            f"• [15m/1h/4h/1D] Bollinger Band đang nén (Squeeze)\n"
            f"• [5m/15m/30m/1h/2h/4h/12h/1D/1W] Phân kỳ/Hội tụ RSI &amp; MACD "
            f"(thường + ẩn, tăng + giảm) - chỉ báo khi nến ĐÓNG\n"
            f"• [{', '.join(CHOCH_TFS)}] CHoCH (đổi cấu trúc thị trường) khi nến ĐÓNG\n"
            f"• [{', '.join(CHOCH_TFS)}] ⭐ SETUP BUY/SELL tỷ lệ thắng cao: CHoCH kèm FVG mới "
            f"cùng hướng → chờ giá hồi về đúng vùng FVG mới báo tín hiệu vào lệnh\n\n"
            f"👉 Dashboard trạng thái MTF sẽ tự động gửi định kỳ qua Discord."
        )
        await self.notifier.send_message(start_msg)


        # 2. Tạo các Task chạy song song trên 1 Single Event Loop (Asyncio)
        tasks = [
            asyncio.create_task(self.stream_binance_websocket()),
            asyncio.create_task(self.notifier.start_polling_commands(self.get_dashboard_text)),
            asyncio.create_task(self.periodic_sync_task())
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Dừng bot theo yêu cầu...")
        finally:
            if self.session and not self.session.closed:
                await self.session.close()
            await self.notifier.close()


def main():
    bot = BinanceFuturesMTFBot()

    def shutdown_handler(sig, frame):
        logger.info("Nhận tín hiệu dừng (SIGINT/SIGTERM). Đang tắt an toàn...")
        bot.is_running = False
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
