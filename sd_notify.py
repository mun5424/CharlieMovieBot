"""Minimal systemd sd_notify client - no external dependency required.

Talks to the socket systemd hands us via $NOTIFY_SOCKET. Used to report
readiness (READY=1) and watchdog keep-alives (WATCHDOG=1) so systemd can
detect a hung process (one that's still running but stopped making
progress) and restart it, which a plain process-exit check cannot catch.
"""
import os
import socket


def notify(message: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return  # not running under systemd (e.g. local dev) - no-op

    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstract namespace socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
    try:
        sock.connect(addr)
        sock.sendall(message.encode())
    except OSError:
        pass  # best-effort; never let notification failures affect the bot
    finally:
        sock.close()
