#!/bin/bash
cd "$(dirname "$0")" || exit 1

# Kiểm tra nếu bot đã đang chạy
if pgrep -f "python.*bot.py" > /dev/null; then
    echo "⚠️ Bot đang chạy rồi! (PID: $(pgrep -f "python.*bot.py"))"
    exit 0
fi

nohup ./venv/bin/python3 -u bot.py >> bot.log 2>> bot_error.log &
sleep 2

if pgrep -f "python.*bot.py" > /dev/null; then
    echo "✅ Bot đã khởi động thành công! (PID: $(pgrep -f "python.*bot.py"))"
    echo "📜 Xem log thời gian thực: tail -f bot_error.log"
else
    echo "❌ Có lỗi khi khởi động bot. Kiểm tra file bot_error.log để biết chi tiết."
fi
