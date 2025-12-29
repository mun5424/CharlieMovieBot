#!/bin/bash
# Backup all data for CharlieMovieBot
# Usage: ./scripts/backup_databases.sh [backup_dir]

set -e

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Backup directory (default: backups/ with timestamp)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${1:-$PROJECT_DIR/backups/$TIMESTAMP}"

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "=== CharlieMovieBot Full Backup ==="
echo "Project: $PROJECT_DIR"
echo "Backup:  $BACKUP_DIR"
echo ""

# ============== SQLite Databases ==============
echo "--- SQLite Databases ---"

DATABASES=(
    "movie_data.db"           # Main bot data (watchlists, reviews, anime, games)
    "price_checker.db"        # Price checker data
    "data/sf6/sf6_trivia.db"  # SF6 trivia data
)

for DB in "${DATABASES[@]}"; do
    DB_PATH="$PROJECT_DIR/$DB"
    if [ -f "$DB_PATH" ]; then
        DB_BACKUP_DIR="$BACKUP_DIR/$(dirname "$DB")"
        mkdir -p "$DB_BACKUP_DIR"
        BACKUP_FILE="$BACKUP_DIR/$DB"

        if command -v sqlite3 &> /dev/null; then
            sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
            echo "✅ $DB ($(du -h "$BACKUP_FILE" | cut -f1))"
        else
            cp "$DB_PATH" "$BACKUP_FILE"
            echo "✅ $DB (copied, $(du -h "$BACKUP_FILE" | cut -f1))"
        fi
    else
        echo "⏭️  $DB (not found)"
    fi
done

# ============== Config Files ==============
echo ""
echo "--- Config Files ---"

CONFIG_FILES=(
    "config.py"               # API tokens, bot settings
)

for CFG in "${CONFIG_FILES[@]}"; do
    CFG_PATH="$PROJECT_DIR/$CFG"
    if [ -f "$CFG_PATH" ]; then
        cp "$CFG_PATH" "$BACKUP_DIR/$CFG"
        echo "✅ $CFG"
    else
        echo "⏭️  $CFG (not found)"
    fi
done

# ============== Trivia Data (JSON) ==============
echo ""
echo "--- Trivia Data ---"

# Server stats
if [ -d "$PROJECT_DIR/data/servers" ]; then
    mkdir -p "$BACKUP_DIR/data/servers"
    SERVER_COUNT=$(ls -1 "$PROJECT_DIR/data/servers/"*.json 2>/dev/null | wc -l)
    if [ "$SERVER_COUNT" -gt 0 ]; then
        cp "$PROJECT_DIR/data/servers/"*.json "$BACKUP_DIR/data/servers/"
        echo "✅ data/servers/ ($SERVER_COUNT server files)"
    else
        echo "⏭️  data/servers/ (empty)"
    fi
else
    echo "⏭️  data/servers/ (not found)"
fi

# Hall of fame
if [ -d "$PROJECT_DIR/data/hall_of_fame" ]; then
    mkdir -p "$BACKUP_DIR/data/hall_of_fame"
    HOF_COUNT=$(ls -1 "$PROJECT_DIR/data/hall_of_fame/"*.json 2>/dev/null | wc -l)
    if [ "$HOF_COUNT" -gt 0 ]; then
        cp "$PROJECT_DIR/data/hall_of_fame/"*.json "$BACKUP_DIR/data/hall_of_fame/"
        echo "✅ data/hall_of_fame/ ($HOF_COUNT files)"
    else
        echo "⏭️  data/hall_of_fame/ (empty)"
    fi
else
    echo "⏭️  data/hall_of_fame/ (not found)"
fi

# ============== Create Archive ==============
echo ""
echo "--- Creating Archive ---"

ARCHIVE_NAME="charlie_backup_$TIMESTAMP.tar.gz"
mkdir -p "$PROJECT_DIR/backups"
cd "$PROJECT_DIR/backups"
tar -czf "$ARCHIVE_NAME" "$TIMESTAMP"

echo "📦 Archive: backups/$ARCHIVE_NAME"
echo "📊 Size:    $(du -h "$ARCHIVE_NAME" | cut -f1)"

# ============== Summary ==============
echo ""
echo "=== Backup Complete ==="
echo ""
echo "Files backed up:"
find "$BACKUP_DIR" -type f | sed "s|$BACKUP_DIR/|  - |"
echo ""
echo "To transfer to new Pi:"
echo "  scp backups/$ARCHIVE_NAME pi@newpi:~/"
echo ""
echo "To restore on new Pi:"
echo "  cd ~/CharlieMovieBot"
echo "  tar -xzf ~/$ARCHIVE_NAME"
echo "  cp -r $TIMESTAMP/* ."
