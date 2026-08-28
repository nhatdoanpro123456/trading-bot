import time
import logging
from typing import Dict, List

from config import (
    ROUND_NUMBER_STEP, ROUND_NUMBER_PROXIMITY_USD,
    PROXIMITY_ALERT_PCT, COOLDOWN_SECONDS,
    FVG_TFS, OB_TFS, BB_SQUEEZE_TFS, DIVERGENCE_TFS, EMA_TFS,
    CHOCH_TFS, CHOCH_FVG_LOOKBACK_BARS, CHOCH_SETUP_MAX_WAIT_BARS,
    TF_TO_MINUTES,
)

logger = logging.getLogger("StateManager")

_SETUP_DISCLAIMER = "(Setup tham khảo kỹ thuật, không phải khuyến nghị đầu tư — tự quản lý rủi ro)"

# Nhãn hiển thị cho từng loại tín hiệu Phân kỳ/Hội tụ (Divergence)
_DIVERGENCE_LABELS = {
    "regular_bull": ("🟢", "PHÂN KỲ THƯỜNG TĂNG",
                      "Giá tạo đáy THẤP HƠN nhưng chỉ báo tạo đáy CAO HƠN → khả năng ĐẢO CHIỀU TĂNG."),
    "hidden_bull": ("🟩", "PHÂN KỲ ẨN TĂNG",
                     "Giá tạo đáy CAO HƠN, chỉ báo tạo đáy THẤP HƠN → khả năng TIẾP DIỄN xu hướng TĂNG."),
    "regular_bear": ("🔴", "PHÂN KỲ THƯỜNG GIẢM",
                      "Giá tạo đỉnh CAO HƠN nhưng chỉ báo tạo đỉnh THẤP HƠN → khả năng ĐẢO CHIỀU GIẢM."),
    "hidden_bear": ("🟥", "PHÂN KỲ ẨN GIẢM",
                     "Giá tạo đỉnh THẤP HƠN, chỉ báo tạo đỉnh CAO HƠN → khả năng TIẾP DIỄN xu hướng GIẢM."),
}


def _current_bar_floor_ts(tf: str) -> int:
    """Mốc thời gian (phút, kiểu epoch) của cây nến khung `tf` ĐANG CHẠY tại thời điểm
    gọi hàm - dùng để giới hạn 1 loại cảnh báo chỉ bắn 1 LẦN DUY NHẤT trong 1 cây nến,
    bất kể giá ra/vào vùng bao nhiêu lần hay vùng có xê dịch toạ độ nhẹ giữa các lần
    tính lại (tránh phụ thuộc vào key theo giá vốn có thể trôi nhẹ qua từng nến)."""
    tf_minutes = TF_TO_MINUTES.get(tf, 60)
    now_min = int(time.time() // 60)
    return (now_min // tf_minutes) * tf_minutes


def _nearest_target(st: "TimeframeState", kind: str, price: float):
    """Tìm mức giá mục tiêu (TP) hợp lý gần nhất: vùng OB/FVG đối ứng gần nhất theo
    đúng hướng lệnh (phía trên nếu BUY, phía dưới nếu SELL)."""
    if kind == "ob_bull":
        candidates = [z["bottom"] for z in st.ob_bear if z["bottom"] > price]
        candidates += [z["bottom"] for z in st.fvg_bear if z["bottom"] > price]
        return min(candidates) if candidates else None
    else:
        candidates = [z["top"] for z in st.ob_bull if z["top"] < price]
        candidates += [z["top"] for z in st.fvg_bull if z["top"] < price]
        return max(candidates) if candidates else None


def _ob_trade_suggestion(kind: str, top: float, bottom: float, st: "TimeframeState", price: float) -> str:
    """Gợi ý Buy/Sell tiềm năng khi giá chạm vùng OB - CHỈ mang tính tham khảo kỹ thuật."""
    tp = _nearest_target(st, kind, price)
    if kind == "ob_bull":
        tp_txt = f"quanh {tp:,.2f}" if tp else "vùng kháng cự/OB Giảm gần nhất tiếp theo"
        return (
            f"💡 Cân nhắc SETUP BUY quanh vùng này. SL gợi ý dưới đáy vùng (~{bottom:,.2f}). "
            f"TP hướng tới {tp_txt}.\n{_SETUP_DISCLAIMER}"
        )
    else:
        tp_txt = f"quanh {tp:,.2f}" if tp else "vùng hỗ trợ/OB Tăng gần nhất tiếp theo"
        return (
            f"💡 Cân nhắc SETUP SELL quanh vùng này. SL gợi ý trên đỉnh vùng (~{top:,.2f}). "
            f"TP hướng tới {tp_txt}.\n{_SETUP_DISCLAIMER}"
        )


def _ob_confluence_labels(st: "TimeframeState", top: float, bottom: float) -> List[str]:
    """
    Kiểm tra vùng OB [bottom, top] có đang HỢP LƯU (confluence) với BB Upper/Basis/
    Lower hoặc EMA200 hay không - tức mức giá HIỆN TẠI của chỉ báo đó rơi vào ĐÚNG
    bên trong vùng OB. Trả về danh sách tên các chỉ báo đang hợp lưu (rỗng nếu không).
    """
    hits = []
    if st.bb_upper and bottom <= st.bb_upper <= top:
        hits.append("BB Upper")
    if st.bb_basis and bottom <= st.bb_basis <= top:
        hits.append("BB Basis (giữa)")
    if st.bb_lower and bottom <= st.bb_lower <= top:
        hits.append("BB Lower")
    if st.ema200 and bottom <= st.ema200 <= top:
        hits.append("EMA200")
    return hits


def _zone_key(tf: str, kind: str, top: float, bottom: float) -> str:
    """Khoá định danh ổn định cho 1 vùng FVG/OB, dựa trên giá (làm tròn) chứ không
    dựa vào bar_index (vì bar_index dịch chuyển liên tục khi có nến mới)."""
    return f"{tf}:{kind}:{round(top, 1)}:{round(bottom, 1)}"


class TimeframeState:
    """Lưu trạng thái hiện tại (đã tính toán từ nến đã ĐÓNG) của 1 khung thời gian."""

    def __init__(self, tf: str):
        self.tf = tf
        self.last_close: float = 0.0

        # Bollinger squeeze
        self.bb_upper: float = 0.0
        self.bb_lower: float = 0.0
        self.bb_basis: float = 0.0
        self.bb_bandwidth: float = 0.0
        self.bb_squeeze: bool = False

        # Vùng FVG / OB đang active (tính lại mỗi lần nến đóng)
        self.fvg_bull: List[dict] = []
        self.fvg_bear: List[dict] = []
        self.ob_bull: List[dict] = []
        self.ob_bear: List[dict] = []

        # Đếm số lần CHẠM (realtime) cho từng vùng, key hoá theo giá
        self.fvg_touch_counts: Dict[str, int] = {}
        self.ob_touch_counts: Dict[str, int] = {}
        self.ob_inside: Dict[str, bool] = {}

        # FVG: mốc "nến hiện tại" (epoch phút, floor theo khung) của LẦN CHẠM đã báo
        # gần nhất cho từng vùng - ĐÃ SỬA BUG SPAM: trước đây dùng cờ fvg_touched_this_bar
        # được reset trong update_zones_on_close(), nhưng hàm đó bị gọi cho MỌI khung mỗi
        # khi recalculate_all_indicators() chạy (tức mỗi khi BẤT KỲ khung nào khác đóng
        # nến, không chỉ đúng khung này) -> cờ bị xoá sớm liên tục, gây spam lại cảnh báo
        # trước khi nến thật sự đóng. Giờ dùng CHÍNH cơ chế mốc-nến-tự-tính giống OB bên
        # dưới (độc lập, không phụ thuộc hàm nào có được gọi hay không) -> mỗi cây nến chỉ
        # báo "GIÁ CHẠM VÙNG FVG" ĐÚNG 1 LẦN, bất kể giá ra/vào vùng bao nhiêu lần.
        self.fvg_touch_last_bar: Dict[str, int] = {}

        # OB: mốc "nến hiện tại" (epoch phút, floor theo khung) của LẦN CHẠM đã báo gần
        # nhất cho từng vùng - đảm bảo mỗi cây nến chỉ báo "GIÁ CHẠM VÙNG OB" ĐÚNG 1 LẦN
        # (khớp yêu cầu "không thông báo liên tục"), không phụ thuộc việc giá ra/vào
        # vùng bao nhiêu lần hay toạ độ vùng có xê dịch nhẹ giữa các lần tính lại.
        self.ob_touch_last_bar: Dict[str, int] = {}

        # OB: mốc "nến hiện tại" của LẦN CẢNH BÁO "SẮP CHẠM" (proximity) gần nhất cho
        # từng vùng - cùng cơ chế như ob_touch_last_bar ở trên nhưng áp dụng riêng cho
        # cảnh báo "sắp chạm", đảm bảo mỗi cây nến CŨNG chỉ báo "SẮP CHẠM VÙNG OB" ĐÚNG
        # 1 LẦN duy nhất (trước đây dùng cooldown theo giây nên vẫn có thể bắn lặp lại
        # nhiều lần trong cùng 1 cây nến nếu giá dao động ra/vào ngưỡng proximity).
        self.ob_approach_last_bar: Dict[str, int] = {}

        # Trạng thái "đã cảnh báo giữ vùng" theo lần test gần nhất (tránh spam mỗi nến)
        self.ob_hold_alerted: Dict[str, bool] = {}

        # EMA200 hiện tại (dùng để kiểm tra HỢP LƯU với vùng OB -> Super Buy/Sell)
        self.ema200: float = 0.0

        # Phân kỳ/Hội tụ (Divergence): key = "{indicator}:{direction}" (vd "RSI:low"),
        # value = bar_time (dạng str) của pivot GẦN NHẤT đã báo -> chỉ báo lại khi có
        # pivot MỚI xuất hiện (bar_time thay đổi), tránh lặp lại cùng 1 tín hiệu mỗi
        # khi hàm tính lại chỉ báo chạy (mỗi nến đóng) trong lúc pivot chưa đổi.
        self.divergence_alerted: Dict[str, str] = {}

        # Market Structure (CHoCH) - chỉ dùng cho tf trong CHOCH_TFS (15m/1h/4h):
        # bar_time (str) của sự kiện CHoCH GẦN NHẤT đã báo -> khử trùng lặp.
        self.choch_alerted_bar: str = ""
        # Các setup "CHoCH + FVG" đang chờ giá hồi về đúng vùng FVG để vào lệnh.
        self.pending_setups: List[dict] = []
        # Khoá các setup ĐÃ kích hoạt (đã báo Buy/Sell) - không báo lại cùng 1 setup.
        self.triggered_setup_keys: set = set()

        # Thời điểm cảnh báo gần nhất theo từng key (chống spam / cooldown)
        self._last_alert_ts: Dict[str, float] = {}

    def _cooldown_ok(self, key: str, cooldown: int = COOLDOWN_SECONDS) -> bool:
        now = time.time()
        last = self._last_alert_ts.get(key, 0)
        if now - last >= cooldown:
            self._last_alert_ts[key] = now
            return True
        return False


class StateManager:
    """
    Quản lý trạng thái toàn bộ các khung thời gian, phát hiện và sinh cảnh báo cho
    đúng 9 nhóm tín hiệu:
      1. Giá chạm mức giá chẵn (round number)
      2. Giá chạm vùng FVG (tăng/giảm) kèm số lần chạm
      3. Giá sắp chạm vùng OB
      4. Giá chạm vùng OB (m15/h1/h4/D) kèm số lần chạm + gợi ý SETUP BUY/SELL tiềm năng
         (SL/TP tham khảo) - mỗi cây nến chỉ báo ĐÚNG 1 LẦN cho mỗi vùng.
      5. Nến đóng cửa trên/dưới OB (giữ vùng) / đã thủng OB
      6. Bollinger đang nén (squeeze)
      7. Phân kỳ/Hội tụ (Divergence) RSI + MACD - đủ 4 loại (thường/ẩn, tăng/giảm) -
         trên 9 khung m5/m15/m30/h1/h2/h4/h12/D/W, chỉ kiểm tra khi nến khung đó ĐÓNG.
      8. CHoCH (Change of Character - đổi cấu trúc thị trường) trên m15/h1/h4, chỉ
         kiểm tra khi nến khung đó ĐÓNG.
      9. Setup Buy/Sell CHẤT LƯỢNG CAO (CHoCH + Displacement + FVG + Bias + Premium/
         Discount): chỉ tạo setup khi CHoCH có displacement mạnh, bias H4+H1 cùng
         hướng, có FVG được tạo NGAY bởi chính nến displacement (còn fresh, chưa bị
         test), và vùng đó nằm đúng phía Discount (BUY) / Premium (SELL) của range
         hiện tại — sau đó chờ giá HỒI VỀ đúng vùng FVG mới báo tín hiệu vào lệnh.
    """

    def __init__(self, timeframes: List[str]):
        self.states: Dict[str, TimeframeState] = {tf: TimeframeState(tf) for tf in timeframes}
        self._global_last_alert_ts: Dict[str, float] = {}

    def _global_cooldown_ok(self, key: str, cooldown: int = COOLDOWN_SECONDS) -> bool:
        now = time.time()
        last = self._global_last_alert_ts.get(key, 0)
        if now - last >= cooldown:
            self._global_last_alert_ts[key] = now
            return True
        return False

    def get_state(self, tf: str) -> TimeframeState:
        return self.states[tf]

    # ------------------------------------------------------------------ #
    # 1) NẾN ĐÓNG CỬA: cập nhật vùng FVG/OB/BB-squeeze + cảnh báo giữ/thủng OB
    # ------------------------------------------------------------------ #
    def update_zones_on_close(self, tf: str, data: dict) -> List[str]:
        """
        Gọi khi 1 cây nến của khung `tf` vừa đóng. `data` gồm:
          close, bb_upper, bb_lower, bb_basis, bb_bandwidth, bb_squeeze,
          fvg_bull, fvg_bear, ob_bull, ob_bear (list các vùng mới tính lại),
          last_high, last_low (high/low của chính cây nến vừa đóng)
        Trả về danh sách chuỗi cảnh báo cần gửi.
        """
        st = self.states[tf]
        alerts: List[str] = []
        close = data["close"]
        last_high = data.get("last_high", close)
        last_low = data.get("last_low", close)

        # --- BB Squeeze (chỉ áp dụng 4 khung theo dõi) ---
        if tf in BB_SQUEEZE_TFS:
            new_squeeze = data["bb_squeeze"]
            if new_squeeze and not st.bb_squeeze:
                if st._cooldown_ok(f"{tf}:bb_squeeze_start"):
                    alerts.append(
                        f"🟣 <b>[{tf}] BOLLINGER ĐANG NÉN (SQUEEZE)</b>\n"
                        f"Bandwidth: {data['bb_bandwidth']:.3f}% (thấp bất thường)\n"
                        f"Giá đóng cửa: {close:,.2f} — khả năng sắp có breakout mạnh.\n"
                        f"-----------------"
                    )
            st.bb_squeeze = new_squeeze
            st.bb_upper = data["bb_upper"]
            st.bb_lower = data["bb_lower"]
            st.bb_basis = data["bb_basis"]
            st.bb_bandwidth = data["bb_bandwidth"]

        # --- EMA200 (dùng để check HỢP LƯU với vùng OB -> Super Buy/Sell) ---
        if tf in EMA_TFS:
            st.ema200 = data.get("ema200", 0.0)

        st.last_close = close

        # --- Phân kỳ/Hội tụ (Divergence) RSI + MACD: chỉ kiểm tra khi nến khung `tf` ĐÓNG ---
        if tf in DIVERGENCE_TFS:
            for sig in data.get("divergence_signals", []):
                dedup_key = f"{sig['indicator']}:{sig['direction']}"
                bar_time_str = str(sig["bar_time"])
                # Chỉ báo khi pivot này là MỚI (chưa từng báo cho đúng cây nến này)
                if st.divergence_alerted.get(dedup_key) != bar_time_str:
                    st.divergence_alerted[dedup_key] = bar_time_str
                    if st._cooldown_ok(f"{tf}:{dedup_key}:{sig['type']}", cooldown=60):
                        emoji, title, desc = _DIVERGENCE_LABELS[sig["type"]]
                        alerts.append(
                            f"{emoji} <b>[{tf}] {title} ({sig['indicator']})</b>\n"
                            f"{desc}\n"
                            f"Đỉnh/đáy trước: giá {sig['prev_price']:,.2f} | {sig['indicator']} {sig['prev_osc_value']:.2f}\n"
                            f"Đỉnh/đáy hiện tại: giá {sig['price']:,.2f} | {sig['indicator']} {sig['osc_value']:.2f}\n"
                            f"Khoảng cách: {sig['bars_between']} nến\n"
                            f"-----------------"
                        )

        # --- CHoCH (Change of Character) + tạo Setup chờ hồi về FVG: chỉ tf trong CHOCH_TFS ---
        if tf in CHOCH_TFS:
            choch_events = data.get("choch_events", [])
            if choch_events:
                last_event = choch_events[-1]
                bar_time_str = str(last_event["bar_time"])
                if st.choch_alerted_bar != bar_time_str:
                    st.choch_alerted_bar = bar_time_str
                    direction = last_event["direction"]
                    level = last_event["level"]
                    has_displacement = last_event.get("displacement", False)
                    disp_txt = (
                        " ⚡ (có DISPLACEMENT mạnh)" if has_displacement
                        else " (KHÔNG có displacement rõ — độ tin cậy thấp, không đủ điều kiện tạo Setup)"
                    )

                    if st._cooldown_ok(f"{tf}:choch:{direction}", cooldown=30):
                        if direction == "bull":
                            alerts.append(
                                f"🔄 <b>[{tf}] CHoCH TĂNG (đổi cấu trúc){disp_txt}</b>\n"
                                f"Giá đóng cửa vượt đỉnh cấu trúc gần nhất ({level:,.2f}) "
                                f"→ cấu trúc chuyển sang TĂNG.\n"
                                f"-----------------"
                            )
                        else:
                            alerts.append(
                                f"🔄 <b>[{tf}] CHoCH GIẢM (đổi cấu trúc){disp_txt}</b>\n"
                                f"Giá đóng cửa phá đáy cấu trúc gần nhất ({level:,.2f}) "
                                f"→ cấu trúc chuyển sang GIẢM.\n"
                                f"-----------------"
                            )

                    # CHoCH mới -> các setup cũ (dù cùng hay ngược hướng) coi như hết giá
                    # trị tham chiếu, xoá để tránh chồng chéo/nhiễu tín hiệu.
                    st.pending_setups = []

                    # ================= 5 BỘ LỌC CHẤT LƯỢNG TRƯỚC KHI TẠO SETUP =================
                    reject_reason = None

                    # Rule 2: phải có displacement
                    if not has_displacement:
                        reject_reason = "CHoCH không có displacement đủ mạnh"

                    # Rule 1: bias khung lớn H4 + H1 phải CÙNG hướng với setup
                    if reject_reason is None:
                        htf_bias = data.get("htf_bias") or {}
                        h4_trend, h1_trend = htf_bias.get("h4"), htf_bias.get("h1")
                        if h4_trend is None or h1_trend is None:
                            reject_reason = "chưa đủ dữ liệu cấu trúc H4/H1 để xác định bias"
                        elif direction == "bull" and not (h4_trend == "up" and h1_trend == "up"):
                            reject_reason = f"bias H4/H1 không cùng chiều TĂNG (H4={h4_trend}, H1={h1_trend})"
                        elif direction == "bear" and not (h4_trend == "down" and h1_trend == "down"):
                            reject_reason = f"bias H4/H1 không cùng chiều GIẢM (H4={h4_trend}, H1={h1_trend})"

                    # Rule 3: phải có FVG được tạo NGAY bởi chính nến displacement
                    matched = None
                    if reject_reason is None:
                        zones = data.get("fvg_bull", []) if direction == "bull" else data.get("fvg_bear", [])
                        best_dist = None
                        for z in zones:
                            dist = abs(z["bar_index"] - last_event["bar_index"])
                            if dist <= CHOCH_FVG_LOOKBACK_BARS and (best_dist is None or dist < best_dist):
                                matched, best_dist = z, dist
                        if matched is None:
                            reject_reason = "không có FVG được tạo ngay bởi nến displacement"

                    # Rule 4: FVG còn "fresh" - ĐÃ tự động đúng, vì calc_fvg_zones() chỉ trả
                    # về các vùng CHƯA bị lấp đầy (chưa mitigate), và matched vừa mới hình
                    # thành đồng thời với displacement (Rule 3) nên chưa từng bị test trước đó.

                    # Rule 5: Premium/Discount - BUY chỉ hợp lệ ở Discount, SELL ở Premium
                    if reject_reason is None:
                        range_high, range_low, equilibrium = data.get("premium_discount", (None, None, None))
                        if equilibrium is None:
                            reject_reason = "chưa đủ dữ liệu Premium/Discount (thiếu swing high/low)"
                        else:
                            zone_mid = (matched["top"] + matched["bottom"]) / 2.0
                            if direction == "bull" and zone_mid >= equilibrium:
                                reject_reason = (
                                    f"vùng FVG nằm ở Premium ({zone_mid:,.2f} ≥ EQ {equilibrium:,.2f}), "
                                    f"không phải Discount -> không BUY giữa/trên range"
                                )
                            elif direction == "bear" and zone_mid <= equilibrium:
                                reject_reason = (
                                    f"vùng FVG nằm ở Discount ({zone_mid:,.2f} ≤ EQ {equilibrium:,.2f}), "
                                    f"không phải Premium -> không SELL giữa/dưới range"
                                )

                    if reject_reason:
                        logger.debug(f"[{tf}] Bỏ qua Setup CHoCH+FVG ({direction}): {reject_reason}")
                    else:
                        setup_key = _zone_key(tf, f"choch_fvg_{direction}", matched["top"], matched["bottom"])
                        st.pending_setups.append({
                            "key": setup_key, "direction": direction,
                            "top": matched["top"], "bottom": matched["bottom"],
                            "choch_level": level, "bars_waited": 0,
                        })
                        kind_txt = "TĂNG" if direction == "bull" else "GIẢM"
                        side_txt = "🟢 BUY" if direction == "bull" else "🔴 SELL"
                        zone_txt = "Discount" if direction == "bull" else "Premium"
                        alerts.append(
                            f"⭐ <b>[Chờ {side_txt}] [{tf}] SETUP CHẤT LƯỢNG CAO: CHoCH + Displacement + FVG {kind_txt}</b>\n"
                            f"✅ Bias H4/H1 cùng chiều {kind_txt}\n"
                            f"✅ CHoCH có Displacement\n"
                            f"✅ FVG được tạo đúng bởi nhịp phá cấu trúc (còn fresh, chưa bị test)\n"
                            f"✅ Vùng nằm trong {zone_txt} (không phải giữa range)\n"
                            f"Vùng FVG: {matched['bottom']:,.2f} - {matched['top']:,.2f}\n"
                            f"Đang CHỜ giá hồi về đúng vùng này để vào lệnh {side_txt} theo "
                            f"hướng xu hướng mới. Sẽ báo riêng ngay khi giá hồi về đúng vùng.\n"
                            f"{_SETUP_DISCLAIMER}\n"
                            f"-----------------"
                        )

            # Setup nào chờ quá lâu (quá CHOCH_SETUP_MAX_WAIT_BARS nến) mà giá chưa hồi
            # về -> tự huỷ, tránh giữ mãi 1 setup đã nguội/không còn phù hợp bối cảnh.
            still_valid = []
            for setup in st.pending_setups:
                setup["bars_waited"] += 1
                if setup["bars_waited"] <= CHOCH_SETUP_MAX_WAIT_BARS:
                    still_valid.append(setup)
            st.pending_setups = still_valid

        # --- FVG: cập nhật danh sách vùng active (dùng cho cả touch-alert lẫn CHoCH combo) ---
        if tf in FVG_TFS or tf in CHOCH_TFS:
            new_fvg_bull = data.get("fvg_bull", [])
            new_fvg_bear = data.get("fvg_bear", [])
            if tf in FVG_TFS:
                new_keys = set()
                for z in new_fvg_bull:
                    new_keys.add(_zone_key(tf, "fvg_bull", z["top"], z["bottom"]))
                for z in new_fvg_bear:
                    new_keys.add(_zone_key(tf, "fvg_bear", z["top"], z["bottom"]))
                # Dọn key không còn active (vùng đã bị xoá/lấp đầy) khỏi trạng thái chạm
                stale_fvg_keys = set(st.fvg_touch_counts.keys()) | set(st.fvg_touch_last_bar.keys())
                for stale in stale_fvg_keys - new_keys:
                    st.fvg_touch_counts.pop(stale, None)
                    st.fvg_touch_last_bar.pop(stale, None)
            st.fvg_bull = new_fvg_bull
            st.fvg_bear = new_fvg_bear

        # --- OB: cập nhật danh sách + phát hiện THỦNG (vùng vừa biến mất do bị phá) ---
        if tf in OB_TFS:
            new_ob_bull = data.get("ob_bull", [])
            new_ob_bear = data.get("ob_bear", [])

            prev_bull_keys = {_zone_key(tf, "ob_bull", z["top"], z["bottom"]) for z in st.ob_bull}
            prev_bear_keys = {_zone_key(tf, "ob_bear", z["top"], z["bottom"]) for z in st.ob_bear}
            new_bull_keys = {_zone_key(tf, "ob_bull", z["top"], z["bottom"]) for z in new_ob_bull}
            new_bear_keys = {_zone_key(tf, "ob_bear", z["top"], z["bottom"]) for z in new_ob_bear}

            # Vùng bull OB biến mất & giá đóng cửa đã lọt xuống dưới bottom => THỦNG
            for z in st.ob_bull:
                key = _zone_key(tf, "ob_bull", z["top"], z["bottom"])
                if key in prev_bull_keys and key not in new_bull_keys and close < z["bottom"]:
                    if st._cooldown_ok(f"{key}:broken"):
                        alerts.append(
                            f"🔴 <b>[{tf}] OB TĂNG ĐÃ BỊ THỦNG</b>\n"
                            f"Vùng: {z['bottom']:,.2f} - {z['top']:,.2f}\n"
                            f"Đóng cửa: {close:,.2f} (đã xuyên xuống dưới vùng hỗ trợ).\n"
                            f"-----------------"
                        )
                    st.ob_touch_counts.pop(key, None)
                    st.ob_inside.pop(key, None)
                    st.ob_hold_alerted.pop(key, None)

            # Vùng bear OB biến mất & giá đóng cửa đã vượt lên trên top => THỦNG
            for z in st.ob_bear:
                key = _zone_key(tf, "ob_bear", z["top"], z["bottom"])
                if key in prev_bear_keys and key not in new_bear_keys and close > z["top"]:
                    if st._cooldown_ok(f"{key}:broken"):
                        alerts.append(
                            f"🟢 <b>[{tf}] OB GIẢM ĐÃ BỊ THỦNG</b>\n"
                            f"Vùng: {z['bottom']:,.2f} - {z['top']:,.2f}\n"
                            f"Đóng cửa: {close:,.2f} (đã xuyên lên trên vùng kháng cự).\n"
                            f"-----------------"
                        )
                    st.ob_touch_counts.pop(key, None)
                    st.ob_inside.pop(key, None)
                    st.ob_hold_alerted.pop(key, None)

            # Với các vùng còn ACTIVE và bị nến vừa đóng "test" (giá chạy xuyên qua) => cảnh báo GIỮ VÙNG
            for z in new_ob_bull:
                key = _zone_key(tf, "ob_bull", z["top"], z["bottom"])
                tested = last_low <= z["top"] and last_high >= z["bottom"]
                if tested and close >= z["bottom"]:
                    if not st.ob_hold_alerted.get(key, False):
                        if st._cooldown_ok(f"{key}:hold"):
                            alerts.append(
                                f"✅ <b>[{tf}] NẾN ĐÓNG CỬA TRÊN OB TĂNG (giữ vùng)</b>\n"
                                f"Vùng: {z['bottom']:,.2f} - {z['top']:,.2f}\n"
                                f"Đóng cửa: {close:,.2f} — hỗ trợ vẫn đang được giữ.\n"
                                f"-----------------"
                            )
                        st.ob_hold_alerted[key] = True
                elif not tested:
                    st.ob_hold_alerted[key] = False

            for z in new_ob_bear:
                key = _zone_key(tf, "ob_bear", z["top"], z["bottom"])
                tested = last_low <= z["top"] and last_high >= z["bottom"]
                if tested and close <= z["top"]:
                    if not st.ob_hold_alerted.get(key, False):
                        if st._cooldown_ok(f"{key}:hold"):
                            alerts.append(
                                f"✅ <b>[{tf}] NẾN ĐÓNG CỬA DƯỚI OB GIẢM (giữ vùng)</b>\n"
                                f"Vùng: {z['bottom']:,.2f} - {z['top']:,.2f}\n"
                                f"Đóng cửa: {close:,.2f} — kháng cự vẫn đang được giữ.\n"
                                f"-----------------"
                            )
                        st.ob_hold_alerted[key] = True
                elif not tested:
                    st.ob_hold_alerted[key] = False

            # Dọn các key không còn active (zone đã bị xoá/thủng/dedup) khỏi toàn bộ các
            # dict trạng thái theo key OB - kể cả những vùng chỉ từng có cảnh báo "sắp
            # chạm" mà chưa từng bị "chạm" hẳn (nên không chỉ dọn dựa trên ob_touch_counts).
            stale_ob_keys = (
                set(st.ob_touch_counts.keys())
                | set(st.ob_touch_last_bar.keys())
                | set(st.ob_approach_last_bar.keys())
            ) - new_bull_keys - new_bear_keys
            for stale in stale_ob_keys:
                st.ob_touch_counts.pop(stale, None)
                st.ob_inside.pop(stale, None)
                st.ob_touch_last_bar.pop(stale, None)
                st.ob_approach_last_bar.pop(stale, None)

            st.ob_bull = new_ob_bull
            st.ob_bear = new_ob_bear

        return alerts

    # ------------------------------------------------------------------ #
    # 2) REALTIME (mỗi tick giá): giá chẵn, chạm FVG, sắp chạm/chạm OB
    # ------------------------------------------------------------------ #
    def check_round_number_touch(self, price: float) -> List[str]:
        alerts = []
        nearest = round(price / ROUND_NUMBER_STEP) * ROUND_NUMBER_STEP
        distance = abs(price - nearest)
        if distance <= ROUND_NUMBER_PROXIMITY_USD:
            key = f"round:{nearest}"
            if self._global_cooldown_ok(key):
                alerts.append(
                    f"🎯 <b>GIÁ CHẠM MỐC CHẴN {nearest:,.0f}</b>\n"
                    f"Giá hiện tại: {price:,.2f} (lệch {distance:,.2f})\n"
                    f"-----------------"
                )
        return alerts

    def check_fvg_touch(self, price: float) -> List[str]:
        alerts = []
        for tf in FVG_TFS:
            st = self.states.get(tf)
            if not st:
                continue
            current_bar_ts = _current_bar_floor_ts(tf)
            for kind, zones in (("fvg_bull", st.fvg_bull), ("fvg_bear", st.fvg_bear)):
                label = "TĂNG" if kind == "fvg_bull" else "GIẢM"
                for z in zones:
                    key = _zone_key(tf, kind, z["top"], z["bottom"])
                    inside = z["bottom"] <= price <= z["top"]
                    if inside:
                        # Mỗi cây nến CHỈ báo ĐÚNG 1 LẦN cho mỗi vùng FVG, bất kể giá ra/
                        # vào vùng bao nhiêu lần trong cây nến đó - dùng mốc nến hệ thống
                        # (độc lập, không phụ thuộc hàm update_zones_on_close có chạy hay
                        # không) giống hệt cơ chế đã áp dụng cho OB touch.
                        if st.fvg_touch_last_bar.get(key) != current_bar_ts:
                            st.fvg_touch_last_bar[key] = current_bar_ts
                            st.fvg_touch_counts[key] = st.fvg_touch_counts.get(key, 0) + 1
                            count = st.fvg_touch_counts[key]
                            alerts.append(
                                f"🟡 <b>[{tf}] GIÁ CHẠM VÙNG FVG {label}</b>\n"
                                f"Vùng: {z['bottom']:,.2f} - {z['top']:,.2f}\n"
                                f"Giá hiện tại: {price:,.2f} — Số lần chạm: {count}\n"
                                f"-----------------"
                            )
        return alerts

    def check_ob_touch_and_approach(self, price: float) -> List[str]:
        alerts = []
        for tf in OB_TFS:
            st = self.states.get(tf)
            if not st:
                continue
            current_bar_ts = _current_bar_floor_ts(tf)
            for kind, zones in (("ob_bull", st.ob_bull), ("ob_bear", st.ob_bear)):
                label = "TĂNG (hỗ trợ)" if kind == "ob_bull" else "GIẢM (kháng cự)"
                for z in zones:
                    key = _zone_key(tf, kind, z["top"], z["bottom"])
                    top, bottom = z["top"], z["bottom"]
                    inside = bottom <= price <= top

                    side_txt = "🟢 BUY" if kind == "ob_bull" else "🔴 SELL"

                    if inside:
                        # Mỗi cây nến CHỈ báo ĐÚNG 1 LẦN cho mỗi vùng OB, bất kể giá ra/
                        # vào vùng bao nhiêu lần trong cây nến đó (khớp yêu cầu "không
                        # thông báo liên tục"). Dùng mốc nến theo giờ hệ thống thay vì
                        # phụ thuộc lần "chạm" trước, nên không bị ảnh hưởng nếu toạ độ
                        # vùng OB xê dịch nhẹ giữa 2 lần tính lại (đầu mỗi nến đóng).
                        if st.ob_touch_last_bar.get(key) != current_bar_ts:
                            st.ob_touch_last_bar[key] = current_bar_ts
                            st.ob_touch_counts[key] = st.ob_touch_counts.get(key, 0) + 1
                            count = st.ob_touch_counts[key]
                            suggestion = _ob_trade_suggestion(kind, top, bottom, st, price)

                            # HỢP LƯU với BB Upper/Basis/Lower hoặc EMA200 -> nâng cấp
                            # thành cảnh báo SUPER BUY/SELL (tín hiệu mạnh hơn bình thường).
                            confluence = _ob_confluence_labels(st, top, bottom)
                            if confluence:
                                confluence_txt = " + ".join(confluence)
                                super_side = "🚀 SUPER BUY" if kind == "ob_bull" else "🚀 SUPER SELL"
                                alerts.append(
                                    f"🌟 <b>[{super_side}] [{tf}] VÙNG OB {label} ĐANG HỢP LƯU: {confluence_txt}</b>\n"
                                    f"Vùng: {bottom:,.2f} - {top:,.2f}\n"
                                    f"Giá hiện tại: {price:,.2f} — Số lần chạm: {count}\n"
                                    f"{suggestion}\n"
                                    f"-----------------"
                                )
                            else:
                                alerts.append(
                                    f"🔶 <b>[{side_txt}] [{tf}] GIÁ CHẠM VÙNG OB {label}</b>\n"
                                    f"Vùng: {bottom:,.2f} - {top:,.2f}\n"
                                    f"Giá hiện tại: {price:,.2f} — Số lần chạm: {count}\n"
                                    f"{suggestion}\n"
                                    f"-----------------"
                                )
                    else:
                        # Sắp chạm: giá còn ngoài vùng nhưng cách biên gần nhất trong ngưỡng %
                        if price > top:
                            dist_pct = (price - top) / price * 100.0
                        else:
                            dist_pct = (bottom - price) / price * 100.0
                        if 0 < dist_pct <= PROXIMITY_ALERT_PCT:
                            # ĐÃ SỬA: trước đây dùng cooldown theo giây (180s) nên vẫn có thể
                            # bắn lặp lại NHIỀU LẦN trong cùng 1 cây nến nếu giá dao động ra/
                            # vào ngưỡng proximity nhiều lượt (VD nến 4h/1D cooldown 180s là
                            # quá ngắn so với thời gian 1 nến). Giờ đổi sang khớp đúng cơ chế
                            # "1 lần / nến" giống cảnh báo chạm hẳn vùng OB ở trên: dùng mốc
                            # nến hệ thống (current_bar_ts) làm khoá, chỉ báo lại khi sang
                            # nến mới, bất kể giá ra/vào ngưỡng bao nhiêu lần trong nến đó.
                            if st.ob_approach_last_bar.get(key) != current_bar_ts:
                                st.ob_approach_last_bar[key] = current_bar_ts
                                alerts.append(
                                    f"🔔 <b>[Chuẩn bị {side_txt}] [{tf}] GIÁ SẮP CHẠM VÙNG OB {label}</b>\n"
                                    f"Vùng: {bottom:,.2f} - {top:,.2f}\n"
                                    f"Giá hiện tại: {price:,.2f} (cách {dist_pct:.3f}%)\n"
                                    f"-----------------"
                                )
                    st.ob_inside[key] = inside
        return alerts

    def check_pending_setup_retest(self, price: float) -> List[str]:
        """
        Gọi ở MỖI TICK giá (giống check_ob_touch_and_approach): kiểm tra xem giá đã
        HỒI VỀ đúng vùng FVG của 1 setup "CHoCH + FVG" đang chờ hay chưa. Nếu có ->
        bắn tín hiệu Buy/Sell tỷ lệ thắng cao (chỉ 1 lần cho mỗi setup). Nếu giá đã
        đi quá xa theo hướng ngược lại (setup coi như hỏng) -> tự huỷ setup đó.
        """
        alerts = []
        for tf, st in self.states.items():
            if not st.pending_setups:
                continue
            still_pending = []
            for setup in st.pending_setups:
                top, bottom = setup["top"], setup["bottom"]
                direction = setup["direction"]
                height = max(top - bottom, 1e-9)
                inside = bottom <= price <= top

                # Vô hiệu hoá nếu giá đã vọt quá xa qua vùng theo hướng ngược lại (FVG
                # coi như bị "lấp đầy" hẳn trước khi kịp hồi về đúng nghĩa retest).
                invalidated = (
                    (direction == "bull" and price < bottom - height) or
                    (direction == "bear" and price > top + height)
                )
                if invalidated:
                    continue

                if inside and setup["key"] not in st.triggered_setup_keys:
                    st.triggered_setup_keys.add(setup["key"])
                    entry_txt = f"{bottom:,.2f} - {top:,.2f}"
                    if direction == "bull":
                        sl = bottom - height * 0.5
                        alerts.append(
                            f"🚀 <b>[{tf}] SETUP BUY TỶ LỆ THẮNG CAO (CHoCH + FVG hồi về)</b>\n"
                            f"Giá vừa hồi về đúng vùng FVG tăng sau CHoCH: {entry_txt}\n"
                            f"Entry: {price:,.2f} | SL gợi ý: ~{sl:,.2f}\n"
                            f"TP: theo xu hướng TĂNG mới, chốt dần theo kháng cự/OB Giảm tiếp theo.\n"
                            f"{_SETUP_DISCLAIMER}\n"
                            f"-----------------"
                        )
                    else:
                        sl = top + height * 0.5
                        alerts.append(
                            f"🚀 <b>[{tf}] SETUP SELL TỶ LỆ THẮNG CAO (CHoCH + FVG hồi về)</b>\n"
                            f"Giá vừa hồi về đúng vùng FVG giảm sau CHoCH: {entry_txt}\n"
                            f"Entry: {price:,.2f} | SL gợi ý: ~{sl:,.2f}\n"
                            f"TP: theo xu hướng GIẢM mới, chốt dần theo hỗ trợ/OB Tăng tiếp theo.\n"
                            f"{_SETUP_DISCLAIMER}\n"
                            f"-----------------"
                        )
                    continue  # setup đã kích hoạt xong - không giữ lại nữa

                still_pending.append(setup)
            st.pending_setups = still_pending
        return alerts

    def build_dashboard_text(self, symbol: str, current_price: float) -> str:
        lines = [f"📊 <b>DASHBOARD [{symbol}]</b>", f"Giá hiện tại: <b>{current_price:,.2f}</b>", ""]
        for tf, st in self.states.items():
            squeeze_txt = "🟣 ĐANG NÉN" if st.bb_squeeze else "—"
            lines.append(f"<b>[{tf}]</b> Đóng cửa: {st.last_close:,.2f} | BB Squeeze: {squeeze_txt}")
            if st.ob_bull:
                nearest_bull = min(st.ob_bull, key=lambda z: abs(current_price - (z['top'] + z['bottom']) / 2))
                lines.append(f"  OB Tăng gần nhất: {nearest_bull['bottom']:,.2f} - {nearest_bull['top']:,.2f}")
            if st.ob_bear:
                nearest_bear = min(st.ob_bear, key=lambda z: abs(current_price - (z['top'] + z['bottom']) / 2))
                lines.append(f"  OB Giảm gần nhất: {nearest_bear['bottom']:,.2f} - {nearest_bear['top']:,.2f}")
            if st.fvg_bull:
                nearest_fvg_b = min(st.fvg_bull, key=lambda z: abs(current_price - (z['top'] + z['bottom']) / 2))
                lines.append(f"  FVG Tăng gần nhất: {nearest_fvg_b['bottom']:,.2f} - {nearest_fvg_b['top']:,.2f}")
            if st.fvg_bear:
                nearest_fvg_s = min(st.fvg_bear, key=lambda z: abs(current_price - (z['top'] + z['bottom']) / 2))
                lines.append(f"  FVG Giảm gần nhất: {nearest_fvg_s['bottom']:,.2f} - {nearest_fvg_s['top']:,.2f}")
        return "\n".join(lines)
