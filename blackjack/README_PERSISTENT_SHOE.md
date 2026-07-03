# Blackjack persistent single-deck shoe

This version uses one persistent single-deck shoe per player.

- A player gets one active hand at a time globally.
- The same shuffled 52-card deck carries across hands.
- Cards dealt in completed hands move into the discard pile.
- The shoe is saved in `blackjack_shoes` inside `bot.db`.
- A fresh deck is shuffled before a hand if fewer than 26 cards remain.
- After a completed hand, the deck reshuffles if 18 or fewer cards remain.
- Insurance has a 10-second timeout and auto-skips.
- Player actions have a 30-second timeout and auto-stand.

New table:

```sql
CREATE TABLE IF NOT EXISTS blackjack_shoes (
    user_id INTEGER PRIMARY KEY,
    deck_json TEXT NOT NULL,
    discard_json TEXT NOT NULL,
    hands_played INTEGER NOT NULL DEFAULT 0,
    last_shuffle_reason TEXT NOT NULL DEFAULT 'new shoe',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```
