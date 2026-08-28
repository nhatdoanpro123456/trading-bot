#!/bin/bash
if pgrep -f "python.*bot.py" > /dev/null; then
    pkill -f "python.*bot.py"
    echo "⏹ Đã dừng bot thành công!"
else
    echo "ℹ️ Bot hiện không chạy."
fi
