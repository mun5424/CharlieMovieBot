import json
import os
import asyncio
import threading
from functools import partial
from config import DATA_FILE

# Locks to prevent race conditions during concurrent access
_async_lock = None  # Lazily initialized to avoid issues before event loop exists
_sync_lock = threading.Lock()


def _get_async_lock():
    """Get or create the async lock (lazy initialization)"""
    global _async_lock
    if _async_lock is None:
        _async_lock = asyncio.Lock()
    return _async_lock


def _load_data_sync():
    """Synchronous file load - internal use only"""
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE) as f:
        return json.load(f)


def _save_data_sync(data):
    """Synchronous file save with atomic write"""
    temp_file = DATA_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=2)
    # Atomic rename to prevent corruption
    os.replace(temp_file, DATA_FILE)


async def load_data_async():
    """Async version with locking - use in async contexts to avoid blocking"""
    async with _get_async_lock():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _load_data_sync)


async def save_data_async(data):
    """Async version with locking - use in async contexts to avoid blocking"""
    async with _get_async_lock():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(_save_data_sync, data))


def load_data():
    """Sync wrapper with locking - for backwards compatibility.

    Note: This may block in async contexts. Prefer load_data_async when possible.
    """
    with _sync_lock:
        return _load_data_sync()


def save_data(data):
    """Sync wrapper with locking - for backwards compatibility.

    Note: This may block in async contexts. Prefer save_data_async when possible.
    """
    with _sync_lock:
        _save_data_sync(data)