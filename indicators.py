import numpy as np
import pandas as pd


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's Smoothing (RMA) tương đương ta.rma() trong Pine Script."""
    alpha = 1.0 / length
    return series.ewm(alpha=alpha, adjust=False).mean()


def calc_sma(series: pd.Series, length: int) -> pd.Series:
    """Tính Simple Moving Average tương đương ta.sma()."""
    return series.rolling(window=length).mean()


def calc_bollinger_bands(series: pd.Series, length: int = 20, mult: float = 2.0):
    """Tính Bollinger Bands tương đương Pine Script."""
    basis = calc_sma(series, length)
    dev = mult * series.rolling(window=length).std(ddof=0)
    upper = basis + dev
    lower = basis - dev
    return basis, upper, lower


def calc_bb_squeeze(series: pd.Series, length: int = 20, mult: float = 2.0,
                     lookback: int = 100, percentile_threshold: float = 20.0):
    """
    Tính Bollinger Bands + Bandwidth (%) và xác định trạng thái NÉN GIÁ (Squeeze).

    Cách xác định nén: so sánh Bandwidth hiện tại với phân phối Bandwidth trong
    `lookback` nến gần nhất. Nếu Bandwidth hiện tại nằm trong nhóm thấp nhất
    (<= percentile_threshold %) của lịch sử gần đây => coi là đang nén (biến động thấp
    bất thường, thường báo hiệu sắp có breakout).

    Trả về:
      basis, upper, lower: các đường Bollinger Band (Series)
      bandwidth_pct: Series % độ rộng band = (upper - lower) / basis * 100
      is_squeeze: bool - True nếu nến cuối cùng đang trong trạng thái nén
    """
    basis, upper, lower = calc_bollinger_bands(series, length, mult)
    bandwidth_pct = (upper - lower) / basis.replace(0, np.nan) * 100.0

    recent = bandwidth_pct.dropna().iloc[-lookback:]
    is_squeeze = False
    if len(recent) >= max(20, length):
        threshold_val = np.percentile(recent, percentile_threshold)
        last_bw = bandwidth_pct.iloc[-1]
        if not np.isnan(last_bw):
            is_squeeze = bool(last_bw <= threshold_val)

    return basis, upper, lower, bandwidth_pct, is_squeeze


def calc_ema(series: pd.Series, length: int = 200) -> pd.Series:
    """EMA chuẩn (Exponential Moving Average) - khớp ta.ema() trong Pine Script."""
    return series.ewm(span=length, adjust=False).mean()


def calc_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Tính RSI (Relative Strength Index) chuẩn Wilder, tương đương ta.rsi() Pine Script."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Khi avg_loss = 0 (chuỗi toàn tăng liên tục) -> RSI = 100 (khớp quy ước Pine)
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Tính MACD (line, signal, histogram) tương đương ta.macd() Pine Script."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def find_confirmed_pivots(values: np.ndarray, left: int = 5, right: int = 5):
    """
    Tìm các đỉnh (pivot high) và đáy (pivot low) ĐÃ ĐƯỢC XÁC NHẬN trong 1 mảng giá trị
    (giá hoặc chỉ báo dao động). Một điểm tại vị trí i được coi là pivot nếu nó là
    giá trị lớn nhất/nhỏ nhất trong cửa sổ [i-left, i+right]. Vì cần đủ `right` giá trị
    PHÍA SAU để xác nhận, các pivot trả về luôn thuộc về nến ĐÃ ĐÓNG từ lâu, không bao
    giờ dùng nến đang hình thành - khớp cách các chỉ báo Divergence trên TradingView
    chỉ vẽ nhãn sau khi đỉnh/đáy đã được xác nhận.

    Trả về (pivot_highs, pivot_lows) - mỗi phần tử là tuple (index, value).
    """
    n = len(values)
    highs, lows = [], []
    for i in range(left, n - right):
        center = values[i]
        if np.isnan(center):
            continue
        window = values[i - left:i + right + 1]
        if np.all(np.isnan(window)):
            continue
        if center >= np.nanmax(window):
            highs.append((i, float(center)))
        if center <= np.nanmin(window):
            lows.append((i, float(center)))
    return highs, lows


def detect_new_divergences(df: pd.DataFrame, osc: pd.Series, indicator_name: str,
                            left: int = 5, right: int = 5,
                            range_lower: int = 5, range_upper: int = 60):
    """
    Phát hiện Phân kỳ/Hội tụ (Divergence) giữa giá và 1 chỉ báo dao động (RSI hoặc
    MACD Histogram), dựa trên 2 pivot GẦN NHẤT đã được XÁC NHẬN - khớp cách hoạt động
    chuẩn của các chỉ báo Divergence phổ biến trên TradingView (vd "Divergence for
    Many Indicators [LonesomeTheBlue]"), với đủ 4 loại tín hiệu:

      - PHÂN KỲ THƯỜNG TĂNG (regular_bull): giá đáy sau THẤP HƠN đáy trước, chỉ báo
        đáy sau CAO HƠN đáy trước -> khả năng ĐẢO CHIỀU TĂNG.
      - PHÂN KỲ ẨN TĂNG (hidden_bull): giá đáy sau CAO HƠN đáy trước, chỉ báo đáy sau
        THẤP HƠN đáy trước -> khả năng TIẾP DIỄN xu hướng TĂNG (hội tụ theo trend).
      - PHÂN KỲ THƯỜNG GIẢM (regular_bear): giá đỉnh sau CAO HƠN đỉnh trước, chỉ báo
        đỉnh sau THẤP HƠN đỉnh trước -> khả năng ĐẢO CHIỀU GIẢM.
      - PHÂN KỲ ẨN GIẢM (hidden_bear): giá đỉnh sau THẤP HƠN đỉnh trước, chỉ báo đỉnh
        sau CAO HƠN đỉnh trước -> khả năng TIẾP DIỄN xu hướng GIẢM.

    Chỉ so sánh 2 pivot có khoảng cách (số nến) nằm trong [range_lower, range_upper]
    (khớp mặc định Range Lower=5 / Range Upper=60 của script gốc). Hàm này chỉ trả về
    tín hiệu ứng với CẶP PIVOT GẦN NHẤT hiện có (tối đa 1 tín hiệu từ cặp đáy + 1 tín
    hiệu từ cặp đỉnh) - phía gọi (state_manager) tự khử trùng lặp theo bar_time để chỉ
    báo 1 lần duy nhất cho mỗi pivot mới.

    Trả về list dict: {type, indicator, direction, bar_time, prev_bar_time,
                        price, prev_price, osc_value, prev_osc_value, bars_between}
    """
    n = len(df)
    signals = []
    min_needed = left + right + range_lower + 2
    if n < min_needed:
        return signals

    osc_values = osc.values
    highs, lows = find_confirmed_pivots(osc_values, left, right)
    price_high = df["high"].values
    price_low = df["low"].values
    times = df["open_time"].values

    if len(lows) >= 2:
        i1, o1 = lows[-2]
        i2, o2 = lows[-1]
        bars_between = i2 - i1
        if range_lower <= bars_between <= range_upper:
            p1, p2 = price_low[i1], price_low[i2]
            sig_type = None
            if o2 > o1 and p2 < p1:
                sig_type = "regular_bull"
            elif o2 < o1 and p2 > p1:
                sig_type = "hidden_bull"
            if sig_type:
                signals.append({
                    "type": sig_type, "indicator": indicator_name, "direction": "low",
                    "bar_time": times[i2], "prev_bar_time": times[i1],
                    "price": p2, "prev_price": p1,
                    "osc_value": o2, "prev_osc_value": o1,
                    "bars_between": int(bars_between),
                })

    if len(highs) >= 2:
        i1, o1 = highs[-2]
        i2, o2 = highs[-1]
        bars_between = i2 - i1
        if range_lower <= bars_between <= range_upper:
            p1, p2 = price_high[i1], price_high[i2]
            sig_type = None
            if o2 < o1 and p2 > p1:
                sig_type = "regular_bear"
            elif o2 > o1 and p2 < p1:
                sig_type = "hidden_bear"
            if sig_type:
                signals.append({
                    "type": sig_type, "indicator": indicator_name, "direction": "high",
                    "bar_time": times[i2], "prev_bar_time": times[i1],
                    "price": p2, "prev_price": p1,
                    "osc_value": o2, "prev_osc_value": o1,
                    "bars_between": int(bars_between),
                })

    return signals


def calc_swing_points(df: pd.DataFrame, left: int = 3, right: int = 3):
    """
    Xác định các đỉnh/đáy CẤU TRÚC GIÁ (swing high / swing low) ĐÃ XÁC NHẬN, dùng
    high/low RIÊNG BIỆT (khác với find_confirmed_pivots vốn dùng chung 1 mảng cho
    chỉ báo dao động) - đúng chuẩn xác định Market Structure trong Price Action/
    Smart Money Concepts (SMC):
      - Swing High tại i: high[i] là GTLN trong cửa sổ [i-left, i+right] của cột 'high'.
      - Swing Low tại i: low[i] là GTNN trong cửa sổ [i-left, i+right] của cột 'low'.
    Chỉ trả về các điểm ĐÃ ĐỦ `right` nến đóng phía sau để xác nhận (không dùng nến
    đang hình thành).

    Trả về (swing_highs, swing_lows) - mỗi phần tử là tuple (index, price).
    """
    n = len(df)
    swing_highs, swing_lows = [], []
    if n < left + right + 1:
        return swing_highs, swing_lows

    highs = df["high"].values
    lows = df["low"].values

    for i in range(left, n - right):
        hwin = highs[i - left:i + right + 1]
        if highs[i] >= np.nanmax(hwin):
            swing_highs.append((i, float(highs[i])))
        lwin = lows[i - left:i + right + 1]
        if lows[i] <= np.nanmin(lwin):
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def calc_structure_trend(df: pd.DataFrame, left: int = 3, right: int = 3):
    """
    Trả về xu hướng CẤU TRÚC THỊ TRƯỜNG hiện tại ("up"/"down"/None) tính đến nến cuối
    cùng của `df` - dùng để xác định BIAS khung lớn (H4/H1) cho bộ lọc Setup CHoCH+FVG.
    Cùng nguyên lý xác định trend như detect_choch_events (CẢ BOS lẫn CHoCH đều cập
    nhật trend - chỉ khác CHoCH mới được coi là "sự kiện" đáng liệt kê), nhưng hàm này
    chỉ cần trạng thái CUỐI CÙNG, không cần liệt kê từng sự kiện.
    """
    n = len(df)
    if n < left + right + 2:
        return None
    swing_highs, swing_lows = calc_swing_points(df, left, right)
    sh_map = {i: v for i, v in swing_highs}
    sl_map = {i: v for i, v in swing_lows}
    closes = df["close"].values

    trend = None
    cur_high = None
    cur_low = None
    for i in range(n):
        if i in sh_map and (cur_high is None or sh_map[i] > cur_high):
            cur_high = sh_map[i]
        if i in sl_map and (cur_low is None or sl_map[i] < cur_low):
            cur_low = sl_map[i]
        if cur_high is not None and closes[i] > cur_high:
            trend = "up"
            cur_high = None
        if cur_low is not None and closes[i] < cur_low:
            trend = "down"
            cur_low = None
    return trend


def calc_premium_discount_zone(df: pd.DataFrame, left: int = 3, right: int = 3):
    """
    Xác định vùng Premium/Discount (Smart Money Concepts) dựa trên "dealing range" =
    khoảng giữa swing high GẦN NHẤT và swing low GẦN NHẤT đã xác nhận:
      - equilibrium (mốc 50%) = (range_high + range_low) / 2
      - Premium  = phía TRÊN equilibrium (vùng "đắt" - ưu tiên SELL)
      - Discount = phía DƯỚI equilibrium (vùng "rẻ" - ưu tiên BUY)

    Trả về (range_high, range_low, equilibrium), hoặc (None, None, None) nếu chưa đủ
    dữ liệu (thiếu swing high hoặc swing low đã xác nhận, hoặc range không hợp lệ).
    """
    swing_highs, swing_lows = calc_swing_points(df, left, right)
    if not swing_highs or not swing_lows:
        return None, None, None
    range_high = swing_highs[-1][1]
    range_low = swing_lows[-1][1]
    if range_high <= range_low:
        return None, None, None
    equilibrium = (range_high + range_low) / 2.0
    return range_high, range_low, equilibrium


def _has_displacement(i: int, level: float, closes: np.ndarray, body: np.ndarray,
                       avg_body: np.ndarray, atr_v: np.ndarray,
                       body_mult: float, break_atr_mult: float) -> bool:
    """Kiểm tra nến tại index i có đủ điều kiện 'displacement' (Rule 2) hay không:
    thân nến đủ lớn so với trung bình gần đây + đóng cửa vượt hẳn qua mức cấu trúc."""
    if i >= len(avg_body) or np.isnan(avg_body[i]) or avg_body[i] <= 0:
        return False
    if body[i] < body_mult * avg_body[i]:
        return False
    if i >= len(atr_v) or np.isnan(atr_v[i]) or atr_v[i] <= 0:
        return False
    if abs(closes[i] - level) < break_atr_mult * atr_v[i]:
        return False
    return True


def detect_choch_events(df: pd.DataFrame, left: int = 3, right: int = 3,
                         body_lookback: int = 20, body_mult: float = 1.3,
                         atr_length: int = 14, break_atr_mult: float = 0.15):
    """
    Quét toàn bộ lịch sử trong `df` để xác định các sự kiện CHoCH (Change of
    Character - đổi tính chất cấu trúc thị trường), theo đúng khái niệm Market
    Structure trong Smart Money Concepts (SMC):

      - Duy trì 1 "đỉnh cấu trúc" (swing high) và 1 "đáy cấu trúc" (swing low) gần
        nhất CHƯA bị phá vỡ, cùng xu hướng hiện tại (uptrend/downtrend/chưa rõ).
      - Khi giá ĐÓNG CỬA vượt lên trên đỉnh cấu trúc đang giữ trong lúc xu hướng
        KHÔNG PHẢI đang tăng -> CHoCH TĂNG (đảo chiều sang tăng).
      - Khi giá ĐÓNG CỬA phá xuống dưới đáy cấu trúc đang giữ trong lúc xu hướng
        KHÔNG PHẢI đang giảm -> CHoCH GIẢM (đảo chiều sang giảm).
      - Nếu phá vỡ CÙNG hướng xu hướng hiện tại -> đây là BOS (Break of Structure,
        tiếp diễn xu hướng cũ) - KHÔNG tính là CHoCH, không đưa vào kết quả trả về.

    MỖI sự kiện còn được đánh giá có "DISPLACEMENT" hay không (Rule 2 - không phải
    CHoCH nào cũng đáng trade):
      - Thân nến (|close-open|) tại bar phá cấu trúc >= body_mult lần thân nến TRUNG
        BÌNH của body_lookback nến gần nhất trước đó.
      - Giá đóng cửa vượt qua mức cấu trúc (level) ít nhất break_atr_mult * ATR(atr_length)
        (đảm bảo "đóng cửa vượt HẲN", không chỉ vừa đủ chạm/wick xuyên rồi đóng lại).
    Sự kiện KHÔNG đạt 2 điều kiện trên vẫn được trả về (để vẫn báo CHoCH thông
    thường) nhưng có "displacement": False - tầng gọi (state_manager) dựa vào cờ này
    để quyết định có đủ điều kiện tạo SETUP Buy/Sell hay không.

    Đây là hàm TÍNH LẠI TOÀN BỘ (stateless) mỗi lần gọi (giống cách detect_new_
    divergences hoạt động) - phía gọi (state_manager) tự khử trùng lặp bằng cách so
    sánh bar_time của sự kiện CUỐI CÙNG với lần đã báo trước đó.

    Trả về list dict theo thứ tự thời gian, MỖI PHẦN TỬ:
      {bar_index, bar_time, direction ("bull"/"bear"), level, displacement (bool)}
    """
    n = len(df)
    events = []
    if n < left + right + 2:
        return events

    swing_highs, swing_lows = calc_swing_points(df, left, right)
    sh_map = {i: v for i, v in swing_highs}
    sl_map = {i: v for i, v in swing_lows}

    closes = df["close"].values
    opens = df["open"].values
    times = df["open_time"].values

    body = np.abs(closes - opens)
    avg_body = pd.Series(body).rolling(window=body_lookback, min_periods=5).mean().shift(1).values
    atr_v = calc_atr(df, atr_length).values

    trend = None      # "up" | "down" | None (chưa xác định)
    cur_high = None    # đỉnh cấu trúc đang giữ (chưa bị phá)
    cur_low = None     # đáy cấu trúc đang giữ (chưa bị phá)

    for i in range(n):
        if i in sh_map and (cur_high is None or sh_map[i] > cur_high):
            cur_high = sh_map[i]
        if i in sl_map and (cur_low is None or sl_map[i] < cur_low):
            cur_low = sl_map[i]

        if cur_high is not None and closes[i] > cur_high:
            if trend != "up":
                disp = _has_displacement(i, cur_high, closes, body, avg_body, atr_v, body_mult, break_atr_mult)
                events.append({
                    "bar_index": i, "bar_time": times[i],
                    "direction": "bull", "level": cur_high,
                    "displacement": disp,
                })
            trend = "up"
            cur_high = None  # đã phá vỡ - chờ swing high mới hình thành làm mốc tiếp theo

        if cur_low is not None and closes[i] < cur_low:
            if trend != "down":
                disp = _has_displacement(i, cur_low, closes, body, avg_body, atr_v, body_mult, break_atr_mult)
                events.append({
                    "bar_index": i, "bar_time": times[i],
                    "direction": "bear", "level": cur_low,
                    "displacement": disp,
                })
            trend = "down"
            cur_low = None

    return events


def calc_atr(df: pd.DataFrame, length: int = 10) -> pd.Series:
    """Tính True Range & ATR bằng RMA tương đương ta.atr() trong Pine Script."""
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return rma(tr, length)


def calc_fvg_zones(df: pd.DataFrame, filter_pct: float = 0.05, max_zones: int = 8):
    """
    Phát hiện Fair Value Gap (FVG) - khớp CHÍNH XÁC logic isBull_gap / isBear_gap của
    "FVG Order Blocks [BigBeluga]":

      isBull_gap = high[2] < low AND high[2] < high[1] AND low[2] < low AND filt_up > filter
      isBear_gap = low[2] > high AND low[2] > low[1] AND high[2] > high AND filt_dn > filter

      filt_up = (low - high[2]) / low * 100
      filt_dn = (low[2] - high) / low[2] * 100

    (trong đó high/low/high[1]/low[1]/high[2]/low[2] tương ứng nến hiện tại, 1 và 2 cây
    trước nó). Vùng gap: bull = [bottom=high[2], top=low] ; bear = [bottom=high, top=low[2]].

    Một vùng được coi là ĐÃ LẤP ĐẦY (filled) và bị loại khỏi kết quả nếu có nến sau đó
    giao dịch xuyên hết qua vùng gap (đây là phần bổ sung để bot không tích luỹ vô hạn
    vùng cũ - bản gốc Pine Script không giới hạn số box gap hiển thị theo giá).

    Trả về (bull_zones, bear_zones) - mỗi phần tử là dict:
      {"top": float, "bottom": float, "bar_index": int, "gap_pct": float}
    """
    n = len(df)
    bull_zones, bear_zones = [], []
    if n < 4:
        return bull_zones, bear_zones

    highs = df['high'].values
    lows = df['low'].values

    for i in range(2, n):
        h2, l2 = highs[i - 2], lows[i - 2]
        h1, l1 = highs[i - 1], lows[i - 1]
        h0, l0 = highs[i], lows[i]

        # Bullish gap: đủ CẢ 3 điều kiện đúng như Pine Script (isBull_gap)
        if h2 < l0 and h2 < h1 and l2 < l0:
            gap_pct = (l0 - h2) / l0 * 100.0 if l0 else 0.0
            if gap_pct > filter_pct:
                top, bottom = l0, h2
                filled = any(lows[j] <= bottom for j in range(i + 1, n))
                if not filled:
                    bull_zones.append({"top": top, "bottom": bottom, "bar_index": i, "gap_pct": gap_pct})

        # Bearish gap: đủ CẢ 3 điều kiện đúng như Pine Script (isBear_gap)
        if l2 > h0 and l2 > l1 and h2 > h0:
            gap_pct = (l2 - h0) / l2 * 100.0 if l2 else 0.0
            if gap_pct > filter_pct:
                top, bottom = l2, h0
                filled = any(highs[j] >= top for j in range(i + 1, n))
                if not filled:
                    bear_zones.append({"top": top, "bottom": bottom, "bar_index": i, "gap_pct": gap_pct})

    return bull_zones[-max_zones:], bear_zones[-max_zones:]


def calc_order_blocks(df: pd.DataFrame, atr_length: int = 200, filter_pct: float = 0.05, max_zones: int = 6):
    """
    Order Block - MÔ PHỎNG TUẦN TỰ TỪNG NẾN giống hệt vòng lặp bar-by-bar của Pine Script
    "FVG Order Blocks [BigBeluga]" (boxes1 = bull/hỗ trợ, boxes2 = bear/kháng cự):

    Mỗi bar xử lý theo đúng thứ tự của script gốc:
      1. Phát hiện gap mới (dùng cùng điều kiện isBull_gap/isBear_gap như calc_fvg_zones)
         -> tạo Order Block mới:
           Bull OB: top = high[2] ; bottom = high[2] - ATR(atr_length tại bar hiện tại)
           Bear OB: bottom = low[2] ; top = low[2] + ATR(atr_length tại bar hiện tại)
      2. Loại các OB đã "THỦNG" - dùng HIGH/LOW (không phải close) của nến hiện tại,
         khớp đúng Pine: `if high < box.get_bottom(box_id)` (bull) /
         `if low > box.get_top(box_id)` (bear).
      3. Dọn OB lồng nhau: nếu 1 OB khác có đỉnh nằm lọt hẳn bên trong 1 OB cũ hơn
         (top1 < top and top1 > bottom) thì xoá OB cũ hơn (khớp đoạn dedup trong Pine).
      4. Giới hạn số lượng OB tối đa = max_zones (FIFO - loại OB cũ nhất khi vượt hạn mức),
         khớp `if boxes1.size() >= box_amount: box.delete(boxes1.shift())`.

    Trả về (bull_obs, bear_obs) - mỗi phần tử là dict:
      {"top": float, "bottom": float, "bar_index": int}
    """
    n = len(df)
    bull_obs, bear_obs = [], []
    if n < atr_length + 5:
        return bull_obs, bear_obs

    atr_v = calc_atr(df, atr_length).values
    highs = df['high'].values
    lows = df['low'].values

    boxes1: list = []  # Bull OB (hỗ trợ) - theo thứ tự tạo, cũ nhất ở đầu
    boxes2: list = []  # Bear OB (kháng cự)

    def _dedup_nested(boxes: list) -> list:
        """Xoá box 'ngoài' nếu có box khác có đỉnh nằm lọt bên trong nó (khớp Pine)."""
        to_remove = set()
        for a_idx, a in enumerate(boxes):
            for b_idx, b in enumerate(boxes):
                if a_idx == b_idx:
                    continue
                if b["top"] < a["top"] and b["top"] > a["bottom"]:
                    to_remove.add(a_idx)
        return [b for idx, b in enumerate(boxes) if idx not in to_remove]

    for i in range(n):
        atr_i = atr_v[i]

        # --- 1. Phát hiện Order Block mới tại bar i (cần tối thiểu 2 nến trước đó) ---
        if i >= 2 and not np.isnan(atr_i) and atr_i > 0:
            h2, l2 = highs[i - 2], lows[i - 2]
            h1, l1 = highs[i - 1], lows[i - 1]
            h0, l0 = highs[i], lows[i]

            if h2 < l0 and h2 < h1 and l2 < l0:
                gap_pct = (l0 - h2) / l0 * 100.0 if l0 else 0.0
                if gap_pct > filter_pct:
                    boxes1.append({"top": h2, "bottom": h2 - atr_i, "bar_index": i - 1})

            if l2 > h0 and l2 > l1 and h2 > h0:
                gap_pct = (l2 - h0) / l2 * 100.0 if l2 else 0.0
                if gap_pct > filter_pct:
                    boxes2.append({"top": l2 + atr_i, "bottom": l2, "bar_index": i - 1})

        # --- 2. Loại OB đã THỦNG, dùng HIGH/LOW của nến hiện tại (khớp Pine) ---
        boxes1 = [b for b in boxes1 if not (highs[i] < b["bottom"])]
        boxes2 = [b for b in boxes2 if not (lows[i] > b["top"])]

        # --- 3. Dọn OB lồng nhau ---
        boxes1 = _dedup_nested(boxes1)
        boxes2 = _dedup_nested(boxes2)

        # --- 4. Giới hạn số lượng OB tối đa (khớp CHÍNH XÁC Pine):
        #     `if boxes1.size() >= box_amount: box.delete(boxes1.shift())`
        #     Đây là "if" (chỉ xoá 1 box, không phải while), chạy MỖI NẾN (không chỉ khi
        #     có box mới) -> số lượng box thực tế tối đa hiển thị luôn là (max_zones - 1),
        #     không phải max_zones. Giữ đúng "lỗi/tính năng" gốc để khớp indicator Pine.
        if len(boxes1) >= max_zones:
            boxes1.pop(0)
        if len(boxes2) >= max_zones:
            boxes2.pop(0)

    return boxes1, boxes2
