# driver/transport/serial_transport.py
from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass
from typing import Optional

import serial

from driver.edmi_enums import EDMI_DLE_IDEN, EDMI_ETX_IDEN, EDMI_STX_IDEN
from driver.serial_settings import MAX_PACKET_LENGTH

log = logging.getLogger(__name__)


class SerialNotReadyError(ConnectionError):
    """Raised when serial is not connected/ready."""


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    timeout_s: float
    write_timeout_s: float
    exclusive: bool = True


class SerialTransport:
    """
    Synchronous serial transport.

    - Owns pyserial.Serial lifecycle.
    - Provides TVL and EDMI framed reads.
    - Single in-flight I/O guarded by a threading.Lock.
    """

    def __init__(self, cfg: SerialConfig) -> None:
        self._cfg = cfg
        self._ser: Optional[serial.Serial] = None
        self._io_lock = threading.Lock()

    # ----------------------------
    # Lifecycle
    # ----------------------------

    def connect(self) -> None:
        if self._ser is not None and getattr(self._ser, "is_open", False):
            return
        self._ser = serial.Serial(
            port=self._cfg.port,
            baudrate=self._cfg.baudrate,
            timeout=self._cfg.timeout_s,
            write_timeout=self._cfg.write_timeout_s,
            exclusive=self._cfg.exclusive,
        )

    def close(self) -> None:
        ser = self._ser
        self._ser = None
        if ser is None:
            return
        try:
            ser.close()
        except Exception:
            pass

    def is_ready(self) -> bool:
        return bool(self._ser is not None and getattr(self._ser, "is_open", False))

    # ----------------------------
    # Public I/O
    # ----------------------------

    def write_packet(self, payload: bytes) -> None:
        self._validate_payload(payload)
        with self._io_lock:
            ser = self._get_ready_serial()
            try:
                ser.write(payload)
            except serial.SerialTimeoutException as e:
                self.close()
                raise TimeoutError("serial write timeout") from e
            except (serial.SerialException, OSError) as e:
                self.close()
                raise OSError("serial write failed") from e

    def read_tvl_packet(self) -> bytes:
        with self._io_lock:
            ser = self._get_ready_serial()
            try:
                header = self._read_exact(ser, 2)
                (length,) = struct.unpack(">H", header)
                if length == 0:
                    return b""
                return self._read_exact(ser, length)
            except TimeoutError:
                raise
            except (serial.SerialException, OSError) as e:
                self.close()
                raise OSError("serial read failed") from e

    def read_edmi_packet(self) -> bytes:
        with self._io_lock:
            ser = self._get_ready_serial()
            try:
                return self._read_edmi_packet_sync(ser)
            except TimeoutError:
                raise
            except (serial.SerialException, OSError) as e:
                self.close()
                raise OSError("serial read failed") from e

    def flush_input(self) -> None:
        with self._io_lock:
            ser = self._get_ready_serial()
            try:
                ser.reset_input_buffer()
            except (serial.SerialException, OSError):
                self.close()

    # ----------------------------
    # Internals
    # ----------------------------

    @staticmethod
    def _validate_payload(payload: bytes) -> None:
        if len(payload) > MAX_PACKET_LENGTH:
            raise ValueError("payload too large")

    def _get_ready_serial(self) -> serial.Serial:
        if not self.is_ready():
            raise SerialNotReadyError("serial not connected")
        if self._ser is None:
            raise SerialNotReadyError("serial not connected")
        return self._ser

    @staticmethod
    def _read_exact(ser: serial.Serial, n: int) -> bytes:
        buf = bytearray(n)
        mv = memoryview(buf)
        read = 0

        while read < n:
            try:
                chunk = ser.read(n - read)
            except (serial.SerialException, OSError) as e:
                raise OSError("serial read failed") from e

            if not chunk:
                raise TimeoutError("serial read timeout")

            mv[read : read + len(chunk)] = chunk
            read += len(chunk)

        return bytes(buf)

    @staticmethod
    def _read_edmi_packet_sync(ser: serial.Serial) -> bytes:
        buf = bytearray()
        in_frame = False

        while True:
            n = getattr(ser, "in_waiting", 0)
            chunk = ser.read(n if n > 0 else 1)

            if not chunk:
                raise TimeoutError("serial read timeout")

            if not in_frame:
                pos = chunk.find(bytes((EDMI_STX_IDEN,)))
                if pos < 0:
                    continue
                in_frame = True
                buf.extend(chunk[pos:])
            else:
                buf.extend(chunk)

            for i in range(1, len(buf)):
                if buf[i] == EDMI_ETX_IDEN and buf[i - 1] != EDMI_DLE_IDEN:
                    return bytes(buf[: i + 1])
