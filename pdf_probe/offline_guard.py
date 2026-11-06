"""Run analyze_label.main with outbound network primitives blocked.

Verifies the prototype needs no network access: any connect/connect_ex/
create_connection/getaddrinfo call raises immediately. Usage:
    offline_guard.py [same args as analyze_label.py]
"""

import socket
import sys


def _blocked(*args, **kwargs):
    raise RuntimeError("Netzwerkzugriff blockiert (Offline-Testlauf)")


def install_guard():
    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked


if __name__ == "__main__":
    install_guard()
    import analyze_label

    sys.exit(analyze_label.main(sys.argv[1:]))
