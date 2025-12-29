#!/bin/bash
# Backup all SQLite databases for CharlieMovieBot
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

echo "=== CharlieMovieBot Database Backup ==="
echo "Project: $PROJECT_DIR"
echo "Backup:  $BACKUP_DIR"
echo ""

# List of databases to backup
DATABASES=(
    "movie_data.db"           # Main bot data (watchlists, reviews, anime, games)
    "price_checker.db"        # Price checker data
    "data/sf6/sf6_trivia.db"  # SF6 trivia data
)

BACKED_UP=0
SKIPPED=0

for DB in "${DATABASES[@]}"; do
    DB_PATH="$PROJECT_DIR/$DB"

    if [ -f "$DB_PATH" ]; then
        # Create subdirectory if needed
        DB_BACKUP_DIR="$BACKUP_DIR/$(dirname "$DB")"
        mkdir -p "$DB_BACKUP_DIR"

        # Use sqlite3 .backup for safe copy (handles locks)
        BACKUP_FILE="$BACKUP_DIR/$DB"

        if command -v sqlite3 &> /dev/null; then
            sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
            echo "✅ $DB ($(du -h "$BACKUP_FILE" | cut -f1))"
        else
            # Fallback to cp if sqlite3 not available
            cp "$DB_PATH" "$BACKUP_FILE"
            echo "✅ $DB (copied, $(du -h "$BACKUP_FILE" | cut -f1))"
        fi

        ((BACKED_UP++))
    else
        echo "⏭️  $DB (not found, skipping)"
        ((SKIPPED++))
    fi
done

echo ""
echo "=== Backup Complete ==="
echo "Backed up: $BACKED_UP databases"
echo "Skipped:   $SKIPPED databases"
echo "Location:  $BACKUP_DIR"
echo ""

# Create a compressed archive
ARCHIVE_NAME="charlie_backup_$TIMESTAMP.tar.gz"
cd "$PROJECT_DIR/backups"
tar -czf "$ARCHIVE_NAME" "$TIMESTAMP"
echo "📦 Archive created: backups/$ARCHIVE_NAME"

# Show total size
echo "📊 Total size: $(du -h "$ARCHIVE_NAME" | cut -f1)"
echo ""
echo "To restore on new Pi:"
echo "  1. Copy $ARCHIVE_NAME to new Pi"
echo "  2. Extract: tar -xzf $ARCHIVE_NAME"
echo "  3. Copy databases to project directory"
