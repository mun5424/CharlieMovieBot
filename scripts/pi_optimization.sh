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
echo -e "${YELLOW}[1/6] Checking current memory status...${NC}"
echo ""
free -h
echo ""

# 2. Check swap configuration
echo -e "${YELLOW}[2/6] Checking swap configuration...${NC}"
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

# 3. Set up zram (compressed RAM swap)
echo -e "${YELLOW}[3/6] Setting up zram swap...${NC}"
echo ""
echo "zram provides compressed RAM-based swap, which is:"
echo "  - Much faster than SD card swap"
echo "  - No SD card wear"
echo "  - ~2-3x compression ratio"
echo ""

if command -v zramctl &> /dev/null && zramctl | grep -q "/dev/zram"; then
    echo -e "${GREEN}zram is already configured:${NC}"
    zramctl
else
    read -p "Install and configure zram swap? (recommended) [Y/n]: " setup_zram
    if [[ ! "$setup_zram" =~ ^[Nn]$ ]]; then
        echo "Installing zram-tools..."
        sudo apt-get update -qq
        sudo apt-get install -y zram-tools

        # Configure zram (50% of RAM = ~256MB compressed)
        echo "Configuring zram..."
        sudo tee /etc/default/zramswap > /dev/null << 'ZRAMEOF'
# Compression algorithm (lz4 is fast, good for Pi)
ALGO=lz4
# Use 50% of RAM for zram
PERCENT=50
ZRAMEOF

        sudo systemctl enable zramswap
        sudo systemctl restart zramswap
        echo -e "${GREEN}zram configured successfully!${NC}"
        zramctl
    else
        echo "Skipped zram setup."
    fi
fi
echo ""

# 4. Reduce GPU memory allocation
echo -e "${YELLOW}[4/6] Checking GPU memory allocation...${NC}"
echo ""

BOOT_CONFIG="/boot/config.txt"
# Also check new location for Bookworm
if [ -f "/boot/firmware/config.txt" ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
fi

if grep -q "^gpu_mem=" "$BOOT_CONFIG" 2>/dev/null; then
    current_gpu=$(grep "^gpu_mem=" "$BOOT_CONFIG" | cut -d= -f2)
    echo "Current GPU memory: ${current_gpu}MB"
    if [ "$current_gpu" -le 16 ]; then
        echo -e "${GREEN}GPU memory already optimized.${NC}"
    else
        read -p "Reduce GPU memory to 16MB? (saves ~48MB RAM, requires reboot) [Y/n]: " reduce_gpu
        if [[ ! "$reduce_gpu" =~ ^[Nn]$ ]]; then
            sudo sed -i 's/^gpu_mem=.*/gpu_mem=16/' "$BOOT_CONFIG"
            echo -e "${GREEN}GPU memory set to 16MB. Reboot required.${NC}"
        fi
    fi
else
    echo "GPU memory not explicitly set (default: 64MB)"
    read -p "Set GPU memory to 16MB? (saves ~48MB RAM, requires reboot) [Y/n]: " set_gpu
    if [[ ! "$set_gpu" =~ ^[Nn]$ ]]; then
        echo "gpu_mem=16" | sudo tee -a "$BOOT_CONFIG" > /dev/null
        echo -e "${GREEN}GPU memory set to 16MB. Reboot required.${NC}"
    fi
fi
echo ""

# 5. Set up daily restart cron job
echo -e "${YELLOW}[5/6] Setting up daily restart cron job...${NC}"
echo ""

# Detect how the bot is started
BOT_DIR="$HOME/CharlieMovieBot"
START_SCRIPT=""
STOP_SCRIPT=""

# Check for start/stop scripts (screen-based setup)
if [ -f "$BOT_DIR/start_bot.sh" ] && [ -f "$BOT_DIR/stop_bot.sh" ]; then
    START_SCRIPT="$BOT_DIR/start_bot.sh"
    STOP_SCRIPT="$BOT_DIR/stop_bot.sh"
    echo -e "${GREEN}Found screen-based bot scripts:${NC}"
    echo "  Start: $START_SCRIPT"
    echo "  Stop:  $STOP_SCRIPT"
    # Use stop/start scripts for clean restart
    CRON_CMD="0 4 * * * cd $BOT_DIR && ./stop_bot.sh ; sleep 3 ; ./start_bot.sh >> /tmp/bot_restart.log 2>&1"
elif [ -f "$BOT_DIR/.start_bot.sh" ]; then
    START_SCRIPT="$BOT_DIR/.start_bot.sh"
    echo "Found start script: $START_SCRIPT"
    CRON_CMD="0 4 * * * pkill -f 'python.*bot' ; sleep 2 ; cd $BOT_DIR && $START_SCRIPT >> /tmp/bot_restart.log 2>&1 &"
else
    echo "Could not auto-detect bot startup method."
    echo ""
    read -p "Enter full path to start script (or press Enter to skip): " START_SCRIPT
    if [ -n "$START_SCRIPT" ]; then
        BOT_DIR=$(dirname "$START_SCRIPT")
        CRON_CMD="0 4 * * * pkill -f 'python.*bot' ; sleep 2 ; cd $BOT_DIR && $START_SCRIPT >> /tmp/bot_restart.log 2>&1 &"
    else
        CRON_CMD=""
    fi
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

# 6. Show final status
echo -e "${YELLOW}[6/6] Final Status${NC}"
echo "=========================================="
echo ""
echo "Memory:"
free -h | head -2
echo ""
echo "Swap/zram:"
if zramctl 2>/dev/null | grep -q "/dev/zram"; then
    echo -e "${GREEN}zram enabled:${NC}"
    zramctl
elif swapon --show | grep -q .; then
    echo -e "${YELLOW}SD card swap (not recommended):${NC}"
    swapon --show
else
    echo -e "${YELLOW}No swap configured${NC}"
fi
echo ""
echo "GPU Memory:"
if grep -q "^gpu_mem=" "$BOOT_CONFIG" 2>/dev/null; then
    echo "$(grep "^gpu_mem=" "$BOOT_CONFIG")"
else
    echo "Default (64MB)"
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
echo "  - Check zram usage with: zramctl"
echo "  - Check bot logs in screen: screen -r charlie"
echo "  - If GPU memory was changed, reboot is required"
echo ""
