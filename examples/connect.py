"""How the examples connect: ``--serial PORT`` (with ``--baud``, 115200 by default) or
``--tcp PORT`` (2323 by default). Serial needs pyserial: ``pip install pyserial``.

blaeck's examples listen on TCP port 23; a script on Linux or macOS can't open a port below 1024
without root, so these use 2323. Point Loggbok at whichever port you choose.
"""

import argparse
import socket


def open_transport(description: str):
    """The serial port or listening socket to hand to ``device.begin()``."""
    parser = argparse.ArgumentParser(description=description)
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--serial", metavar="PORT", help="serial port, such as /dev/ttyUSB0 or COM3")
    where.add_argument("--tcp", metavar="PORT", type=int, help="TCP port to listen on (default 2323)")
    parser.add_argument("--baud", type=int, default=115200, help="serial speed (default 115200)")
    args = parser.parse_args()
    if args.serial:
        import serial  # pyserial

        print(f"Connect Loggbok to {args.serial} at {args.baud} baud.")
        return serial.Serial(args.serial, args.baud, timeout=0)
    server = socket.create_server(("", 2323 if args.tcp is None else args.tcp))
    print(f"Connect Loggbok to this computer, TCP port {server.getsockname()[1]}.")
    return server
