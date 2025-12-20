#!/bin/bash
# Pi Optimization Script for CharlieMovieBot
# Run this on your Raspberry Pi to prevent freezes

set -e

echo "=========================================="
echo "CharlieMovieBot Pi Optimization Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Check current memory and swap status
echo -e "${YELLOW}[1/4] Checking current memory status...${NC}"
echo ""
free -h
echo ""

# 2. Check swap configuration
echo -e "${YELLOW}[2/4] Checking swap configuration...${NC}"
if swapon --show | grep -q .; then
    echo -e "${RED}Swap is ENABLED:${NC}"
    swapon --show
    echo ""
    echo "Swap on SD card causes system freezes when memory is low."
    echo ""
    read -p "Do you want to DISABLE swap? (recommended) [y/N]: " disable_swap

    if [[ "$disable_swap" =~ ^[Yy]$ ]]; then
        echo "Disabling swap..."
        sudo dphys-swapfile swapoff 2>/dev/null || sudo swapoff -a
        sudo systemctl disable dphys-swapfile 2>/dev/null || true
        echo -e "${GREEN}Swap disabled successfully!${NC}"
    else
        echo "Swap left enabled."
    fi
else
    echo -e "${GREEN}Swap is already disabled. Good!${NC}"
fi
echo ""

# 3. Set up daily restart cron job
echo -e "${YELLOW}[3/4] Setting up daily restart cron job...${NC}"
echo ""

# Detect how the bot is started
BOT_DIR="$HOME/CharlieMovieBot"
START_SCRIPT=""

# Check for common start script names
if [ -f "$BOT_DIR/.start_bot.sh" ]; then
    START_SCRIPT="$BOT_DIR/.start_bot.sh"
elif [ -f "$BOT_DIR/start_bot.sh" ]; then
    START_SCRIPT="$BOT_DIR/start_bot.sh"
elif [ -f "$HOME/.start_bot.sh" ]; then
    START_SCRIPT="$HOME/.start_bot.sh"
fi

# Check for systemd service
SERVICE_NAME=""
if systemctl list-units --type=service 2>/dev/null | grep -q "charliebot"; then
    SERVICE_NAME="charliebot"
elif systemctl list-units --type=service 2>/dev/null | grep -q "charlie"; then
    SERVICE_NAME="charlie"
fi

if [ -n "$SERVICE_NAME" ]; then
    echo "Found systemd service: $SERVICE_NAME"
    CRON_CMD="0 4 * * * /usr/bin/systemctl restart ${SERVICE_NAME}"
elif [ -n "$START_SCRIPT" ]; then
    echo "Found start script: $START_SCRIPT"
    # For shell script: kill python bot process and restart
    CRON_CMD="0 4 * * * pkill -f 'python.*bot' ; sleep 2 ; cd $BOT_DIR && $START_SCRIPT >> /tmp/bot_restart.log 2>&1 &"
else
    echo "Could not auto-detect bot startup method."
    echo ""
    echo "Options:"
    echo "  1) Enter systemd service name"
    echo "  2) Enter path to start script"
    echo "  3) Skip"
    read -p "Choice [1/2/3]: " choice

    case $choice in
        1)
            read -p "Service name: " SERVICE_NAME
            CRON_CMD="0 4 * * * /usr/bin/systemctl restart ${SERVICE_NAME}"
            ;;
        2)
            read -p "Full path to start script: " START_SCRIPT
            BOT_DIR=$(dirname "$START_SCRIPT")
            CRON_CMD="0 4 * * * pkill -f 'python.*bot' ; sleep 2 ; cd $BOT_DIR && $START_SCRIPT >> /tmp/bot_restart.log 2>&1 &"
            ;;
        *)
            CRON_CMD=""
            ;;
    esac
fi

if [ -n "$CRON_CMD" ]; then
    # Check if cron job already exists
    if crontab -l 2>/dev/null | grep -q "4 \* \* \*.*bot"; then
        echo -e "${GREEN}Daily restart cron job already exists.${NC}"
        crontab -l | grep "bot"
    else
        echo ""
        echo "Proposed cron job:"
        echo "  $CRON_CMD"
        echo ""
        read -p "Add daily restart at 4 AM? [Y/n]: " add_cron
        if [[ ! "$add_cron" =~ ^[Nn]$ ]]; then
            (crontab -l 2>/dev/null || true; echo "$CRON_CMD") | crontab -
            echo -e "${GREEN}Added cron job successfully!${NC}"
        else
            echo "Skipped cron job setup."
        fi
    fi
else
    echo "Skipped cron job setup."
fi
echo ""

# 4. Show final status
echo -e "${YELLOW}[4/4] Final Status${NC}"
echo "=========================================="
echo ""
echo "Memory:"
free -h | head -2
echo ""
echo "Swap:"
if swapon --show | grep -q .; then
    swapon --show
else
    echo -e "${GREEN}Disabled (good for Pi Zero 2 W)${NC}"
fi
echo ""
echo "Cron jobs:"
crontab -l 2>/dev/null | grep -E "(charlie|bot)" || echo "No bot-related cron jobs"
echo ""
echo "=========================================="
echo -e "${GREEN}Optimization complete!${NC}"
echo ""
echo "Recommendations:"
echo "  - Monitor memory with: watch -n 5 free -h"
echo "  - Check bot logs with: journalctl -u ${SERVICE_NAME:-charliebot} -f"
echo "  - If freezes continue, consider reducing features or upgrading to Pi 4"
echo ""
