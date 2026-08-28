import os
from dotenv import load_dotenv

# Tải biến môi trường từ .env
load_dotenv()

# --- Telegram Credentials (dự phòng, hiện đang dùng Discord Webhook chính) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Binance Config ---
SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper()
BINANCE_FUTURES_REST_URL = "https://fapi.binance.com/fapi/v1"
BINANCE_WS_URL = "wss://fstream.binance.com/market/ws"

# --- Khung thời gian cho BB Squeeze / FVG / OB (giữ nguyên yêu cầu gốc) ---
TIMEFRAMES = ["15m", "1h", "4h", "1d"]

# --- Khung thời gian cho Phân kỳ/Hội tụ (Divergence) RSI + MACD ---
DIVERGENCE_TFS = ["5m", "15m", "30m", "1h", "2h", "4h", "12h", "1d", "1w"]

# Thứ tự thời lượng (phút) của từng khung - dùng để sắp xếp ALL_TFS đúng thứ tự,
# và để state_manager tính "mốc nến hiện tại" (dedupe cảnh báo theo từng cây nến)
TF_TO_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080,
}

# Toàn bộ khung thời gian bot cần tải nến (hợp nhất TIMEFRAMES + DIVERGENCE_TFS,
# vì DIVERGENCE_TFS đã bao trùm cả 4 khung gốc nên hiện ALL_TFS == DIVERGENCE_TFS,
# nhưng tính hợp nhất tường minh để an toàn nếu sau này 2 danh sách lệch nhau)
ALL_TFS = sorted(set(TIMEFRAMES) | set(DIVERGENCE_TFS), key=lambda tf: TF_TO_MINUTES[tf])

# Số lượng nến lịch sử tối đa lưu trong RAM cho mỗi khung
MAX_KLINES_PER_TF = 500

# --- Bollinger Bands & Squeeze (nén giá) ---
BB_LENGTH = 20
BB_MULT = 2.0
BB_SQUEEZE_TFS = ["15m", "1h", "4h", "1d"]   # Các khung phát hiện nén giá (Squeeze)
BB_SQUEEZE_LOOKBACK = 100                     # Số nến gần nhất để tính percentile Bandwidth
BB_SQUEEZE_PERCENTILE = 20.0                  # Bandwidth hiện tại <= percentile này (%) => đang nén

# --- Fair Value Gap (FVG) ---
# LƯU Ý: KHÔNG bật cho "15m" theo yêu cầu -> khung 15m chỉ nhận cảnh báo Bollinger Squeeze.
FVG_TFS = ["1h", "4h", "1d"]
# ĐÃ SỬA: 0.05 -> 0.5 để khớp đúng "Filter Gaps by %" = 0.5 trên chart TradingView BigBeluga.
FVG_FILTER_PCT = float(os.getenv("FVG_FILTER_PCT", "0.5"))  # % tối thiểu độ lớn gap để tính là FVG hợp lệ
FVG_MAX_ZONES = 8   # số vùng FVG gần nhất theo dõi mỗi khung

# --- Order Block (OB) - khớp logic "FVG Order Blocks [BigBeluga]" ---
# CẬP NHẬT: đã BẬT lại cho "15m" theo yêu cầu mới (cần cảnh báo chạm OB + gợi ý buy/sell
# trên m15/h1/h4/D). Chỉ FVG (cảnh báo "giá chạm FVG" thông thường) là vẫn KHÔNG bật cho 15m.
OB_TFS = ["15m", "1h", "4h", "1d"]
OB_ATR_LENGTH = 200          # độ dài ATR đo chiều cao Order Block (khớp Pine: ta.atr(200))
# ĐÃ SỬA: 0.05 -> 0.5 để khớp đúng "Filter Gaps by %" = 0.5 trên chart TradingView BigBeluga.
OB_FILTER_PCT = float(os.getenv("OB_FILTER_PCT", "0.5"))  # % tối thiểu độ lớn gap để tạo OB
OB_MAX_ZONES = 6             # số Order Block gần nhất theo dõi mỗi khung (khớp "Blocks Amount" mặc định)

# --- EMA200 (dùng để kiểm tra HỢP LƯU với vùng OB khi giá chạm -> Super Buy/Sell) ---
EMA_TFS = OB_TFS  # tính EMA200 cho đúng các khung có theo dõi OB
EMA_LENGTH = int(os.getenv("EMA_LENGTH", "200"))

# --- Market Structure: CHoCH (Change of Character) + Setup Buy/Sell theo FVG hồi về ---
# Áp dụng cho m15/h1/h4 (không dùng D vì cấu trúc/CHoCH khung ngày quá chậm, ít setup).
CHOCH_TFS = ["15m", "1h", "4h"]
# Số nến bên trái/phải để xác nhận 1 đỉnh/đáy CẤU TRÚC GIÁ (swing high/low) - dùng
# lookback ngắn hơn Divergence vì cấu trúc SMC thường dùng pivot nhạy hơn (3-5 nến).
SWING_LEFT = int(os.getenv("SWING_LEFT", "3"))
SWING_RIGHT = int(os.getenv("SWING_RIGHT", "3"))
# Khoảng cách tối đa (số nến) giữa cây nến xác nhận CHoCH và cây nến tạo FVG để được
# tính là FVG được tạo NGAY bởi CHÍNH nến displacement (siết chặt: 2 nến, trước là 5)
# -> đảm bảo đúng là FVG của cú phá cấu trúc, không phải 1 FVG ngẫu nhiên gần đó.
CHOCH_FVG_LOOKBACK_BARS = int(os.getenv("CHOCH_FVG_LOOKBACK_BARS", "2"))
# Số nến tối đa CHỜ giá hồi về đúng vùng FVG trước khi setup tự hết hạn (huỷ chờ).
CHOCH_SETUP_MAX_WAIT_BARS = int(os.getenv("CHOCH_SETUP_MAX_WAIT_BARS", "60"))

# --- Bộ lọc chất lượng nâng cao cho Setup CHoCH + FVG (SMC) ---
# 1) Bias khung lớn BẮT BUỘC: BUY cần H4 VÀ H1 cùng đang cấu trúc TĂNG (HH+HL); SELL
#    cần cả 2 cùng đang cấu trúc GIẢM (LH+LL). Vì biến trend được cập nhật trên MỌI cú
#    phá cấu trúc (kể cả BOS, không chỉ CHoCH), việc này tự động loại luôn trường hợp
#    "H4 vừa có BOS/CHoCH giảm rõ" khi đòi BUY (lúc đó trend H4 đã là "down").
HTF_BIAS_TF_HIGH = os.getenv("HTF_BIAS_TF_HIGH", "4h")
HTF_BIAS_TF_LOW = os.getenv("HTF_BIAS_TF_LOW", "1h")

# 2) Displacement: nến phá cấu trúc (tạo CHoCH) phải có thân nến đủ LỚN so với trung
#    bình gần đây VÀ đóng cửa vượt hẳn qua mức cấu trúc (không chỉ chớm/wick) mới được
#    coi là CHoCH "có displacement" - đủ điều kiện tạo Setup Buy/Sell.
DISPLACEMENT_BODY_LOOKBACK = int(os.getenv("DISPLACEMENT_BODY_LOOKBACK", "20"))
DISPLACEMENT_BODY_MULT = float(os.getenv("DISPLACEMENT_BODY_MULT", "1.3"))
DISPLACEMENT_ATR_LENGTH = int(os.getenv("DISPLACEMENT_ATR_LENGTH", "14"))
DISPLACEMENT_BREAK_ATR_MULT = float(os.getenv("DISPLACEMENT_BREAK_ATR_MULT", "0.15"))

# --- Phân kỳ / Hội tụ (Divergence) đa khung: RSI + MACD Histogram ---
# Áp dụng cho toàn bộ 9 khung trong DIVERGENCE_TFS, CHỈ kiểm tra khi nến khung đó ĐÓNG.
# Đủ 4 loại tín hiệu: Phân kỳ thường Tăng/Giảm (đảo chiều) + Phân kỳ ẩn Tăng/Giảm (tiếp diễn).
RSI_LENGTH = int(os.getenv("RSI_LENGTH", "14"))
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
# Số nến bên trái/phải để xác nhận 1 đỉnh/đáy (pivot) - khớp mặc định lbL/lbR = 5/5
# của các chỉ báo Divergence phổ biến trên TradingView. Pivot chỉ được coi là "xác nhận"
# sau khi đã có đủ DIVERGENCE_PIVOT_RIGHT cây nến ĐÃ ĐÓNG kế tiếp nó.
DIVERGENCE_PIVOT_LEFT = int(os.getenv("DIVERGENCE_PIVOT_LEFT", "5"))
DIVERGENCE_PIVOT_RIGHT = int(os.getenv("DIVERGENCE_PIVOT_RIGHT", "5"))
# Khoảng cách (số nến) tối thiểu/tối đa giữa 2 đỉnh (hoặc 2 đáy) pivot liên tiếp để
# được tính là 1 cặp phân kỳ hợp lệ - khớp mặc định Range Lower=5 / Range Upper=60.
DIVERGENCE_RANGE_LOWER = int(os.getenv("DIVERGENCE_RANGE_LOWER", "5"))
DIVERGENCE_RANGE_UPPER = int(os.getenv("DIVERGENCE_RANGE_UPPER", "60"))

# --- Cảnh báo giá chạm mức giá chẵn (Round Number) ---
ROUND_NUMBER_STEP = float(os.getenv("ROUND_NUMBER_STEP", "1000"))          # bước giá chẵn: 70000, 71000, 72000...
ROUND_NUMBER_PROXIMITY_USD = float(os.getenv("ROUND_NUMBER_PROXIMITY_USD", "15"))  # sai số ($) coi là "chạm" mốc chẵn

# --- Tham số Cảnh báo & Chống Spam ---
# Ngưỡng khoảng cách (%) tính là giá "sắp chạm" vùng OB
PROXIMITY_ALERT_PCT = float(os.getenv("PROXIMITY_PCT", "0.15"))

# Thời gian Cooldown (giây) chống spam mặc định cho cùng 1 mức giá / tín hiệu
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "1200"))

# --- Discord Webhook Config (thay thế Telegram khi bị chặn) ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
# Discord Webhook chỉ GỬI được, không nhận lệnh /status như Telegram.
# Bot sẽ tự động gửi Dashboard định kỳ mỗi X phút thay thế. Đặt 0 để tắt.
DISCORD_STATUS_INTERVAL_MINUTES = int(os.getenv("DISCORD_STATUS_INTERVAL_MINUTES", "60"))
