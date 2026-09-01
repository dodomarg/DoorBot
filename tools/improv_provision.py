#!/usr/bin/env python3
"""Provision Wi-Fi credentials over USB using the Improv Serial protocol.

The ESP32 is already running DoorBot firmware with `improv_serial:` enabled, so
new credentials can be pushed over the same USB cable used for flashing. No
reflash is required, and the credentials are stored in the device's NVS
partition rather than being compiled into the image.

Frame layout, taken from Improv/src/improv.cpp:

    'I' 'M' 'P' 'R' 'O' 'V' | version | type | data_len | data... | checksum | '\n'

`checksum` is the low byte of the sum of every byte from 'I' through the final
data byte. An RPC payload is [command, payload_len, *args], and WIFI_SETTINGS
args are [ssid_len, *ssid, password_len, *password].

Usage:
    improv_provision.py --port /dev/ttyACM0 --env ~/.config/doorbot/wifi.env
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required: pip install pyserial")

HEADER = b"IMPROV"
VERSION = 1

TYPE_CURRENT_STATE = 0x01
TYPE_ERROR_STATE = 0x02
TYPE_RPC_RESPONSE = 0x04

CMD_WIFI_SETTINGS = 0x01
CMD_GET_CURRENT_STATE = 0x02

STATES = {
    0x00: "STOPPED",
    0x01: "AWAITING_AUTHORIZATION",
    0x02: "AUTHORIZED",
    0x03: "PROVISIONING",
    0x04: "PROVISIONED",
}

ERRORS = {
    0x00: "NONE",
    0x01: "INVALID_RPC",
    0x02: "UNKNOWN_RPC",
    0x03: "UNABLE_TO_CONNECT",
    0x04: "NOT_AUTHORIZED",
    0x05: "BAD_HOSTNAME",
    0xFF: "UNKNOWN",
}


def build_frame(msg_type: int, data: bytes) -> bytes:
    body = HEADER + bytes([VERSION, msg_type, len(data)]) + data
    checksum = sum(body) & 0xFF
    return body + bytes([checksum, 0x0A])


def wifi_settings_frame(ssid: str, password: str) -> bytes:
    ssid_b = ssid.encode()
    pass_b = password.encode()
    if len(ssid_b) > 32:
        raise ValueError(f"SSID is {len(ssid_b)} bytes; the 802.11 maximum is 32")
    if len(pass_b) > 63:
        raise ValueError(f"password is {len(pass_b)} bytes; the WPA maximum is 63")
    args = bytes([len(ssid_b)]) + ssid_b + bytes([len(pass_b)]) + pass_b
    payload = bytes([CMD_WIFI_SETTINGS, len(args)]) + args
    return build_frame(0x03, payload)


def rpc_frame(command: int) -> bytes:
    return build_frame(0x03, bytes([command, 0x00]))


class FrameReader:
    """Extracts Improv frames from a stream that also carries plain log text."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes):
        self._buf.extend(chunk)
        # Bound memory when the device is chatty and no frames arrive.
        if len(self._buf) > 8192:
            del self._buf[:-1024]
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                break
            # A frame needs header(6) + version + type + len before data.
            if len(self._buf) < start + 9:
                break
            version = self._buf[start + 6]
            if version != VERSION:
                del self._buf[: start + 1]
                continue
            msg_type = self._buf[start + 7]
            data_len = self._buf[start + 8]
            end = start + 9 + data_len + 1
            if len(self._buf) < end:
                break
            frame = bytes(self._buf[start:end])
            expected = sum(frame[:-1]) & 0xFF
            if expected != frame[-1]:
                # Not a real frame -- 'IMPROV' appeared inside log output.
                del self._buf[: start + 1]
                continue
            del self._buf[:end]
            yield msg_type, frame[9 : 9 + data_len]


def describe(msg_type: int, data: bytes) -> str:
    if msg_type == TYPE_CURRENT_STATE and data:
        return f"state={STATES.get(data[0], hex(data[0]))}"
    if msg_type == TYPE_ERROR_STATE and data:
        return f"error={ERRORS.get(data[0], hex(data[0]))}"
    if msg_type == TYPE_RPC_RESPONSE:
        # [command, payload_len, (entry_len, entry...)*]
        urls = []
        pos = 2
        while pos < len(data):
            n = data[pos]
            urls.append(data[pos + 1 : pos + 1 + n].decode(errors="replace"))
            pos += 1 + n
        return f"rpc_response urls={urls}" if urls else "rpc_response"
    return f"type={hex(msg_type)} data={data.hex()}"


def read_env(path: str) -> tuple[str, str]:
    ssid = password = None
    with open(path) as fh:
        for line in fh:
            key, _, value = line.rstrip("\n").partition("=")
            if key == "SSID":
                ssid = value
            elif key == "PASS":
                password = value
    if not ssid:
        raise SystemExit(f"no SSID found in {path}")
    return ssid, password or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--env", help="file containing SSID=... and PASS=... lines")
    ap.add_argument("--ssid")
    ap.add_argument("--password", default="")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--verbose", action="store_true", help="echo device log lines")
    args = ap.parse_args()

    if args.env:
        ssid, password = read_env(args.env)
    elif args.ssid:
        ssid, password = args.ssid, args.password
    else:
        return ap.error("one of --env or --ssid is required")

    frame = wifi_settings_frame(ssid, password)
    print(f"SSID {len(ssid)} chars, password {len(password)} chars -> {args.port}")

    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        # The USB-Serial/JTAG peripheral drops buffered input across a reopen.
        ser.reset_input_buffer()
        reader = FrameReader()

        ser.write(rpc_frame(CMD_GET_CURRENT_STATE))
        ser.flush()
        time.sleep(0.5)
        ser.write(frame)
        ser.flush()

        deadline = time.monotonic() + args.timeout
        provisioned = False
        while time.monotonic() < deadline:
            chunk = ser.read(512)
            if not chunk:
                continue
            if args.verbose:
                sys.stderr.write(chunk.decode(errors="replace"))
            for msg_type, data in reader.feed(chunk):
                print(f"  <- {describe(msg_type, data)}")
                if msg_type == TYPE_ERROR_STATE and data and data[0] != 0x00:
                    print(f"FAILED: {ERRORS.get(data[0], hex(data[0]))}")
                    return 1
                if msg_type == TYPE_CURRENT_STATE and data and data[0] == 0x04:
                    provisioned = True
                if msg_type == TYPE_RPC_RESPONSE:
                    provisioned = True
                    print("PROVISIONED")
                    return 0
        if provisioned:
            print("PROVISIONED")
            return 0
        print("TIMEOUT: no confirmation from device")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
