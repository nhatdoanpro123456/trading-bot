#!/bin/bash

PLIST_NAME="com.cryptobot.btc.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$TARGET_DIR"
cp "$PLIST_NAME" "$TARGET_DIR/"

echo " Đang đăng ký dịch vụ chạy ngầm 24/7 với macOS Launchd..."
launchctl unload "$TARGET_DIR/$PLIST_NAME" 2>/dev/null
launchctl load "$TARGET_DIR/$PLIST_NAME"

echo "✅ Bot đã được kích hoạt chạy ngầm 24/7!"
echo "📌 Bot sẽ tự động chạy khi mở máy và tự khởi động lại nếu có lỗi."
echo "📜 Để xem log thời gian thực: tail -f bot.log"
echo "⏹ Để dừng dịch vụ: launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
