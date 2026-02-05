# driver/interface/media.py
from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, Iterable, Tuple

from driver.edmi_enums import EDMI_ERROR_CODE
from driver.frames_codec.generics import (

    edmi_pre_process,
    edmi_validate_crc,
    wake_up_seq,
)
from driver.frames_codec.edmi_profile_frame import edmi_coerce_datetime
from driver.frames_codec.login_frame import edmi_create_login_packet, edmi_parse_login_answer
from driver.frames_codec.read_profile_frame import (
    edmi_create_read_profile_info_access_packet,
    edmi_create_read_profile_packet,
    edmi_create_search_profile_packet,
    edmi_get_file_channel_regs,
    edmi_get_file_info_regs,
    edmi_parse_read_profile_info_access_payload,
    edmi_parse_read_profile_payload,
    edmi_parse_search_profile_payload,
    edmi_set_file_channel_info,
    edmi_set_profile_info,
)
from driver.frames_codec.read_registers_frame import (
    edmi_create_read_registers_packet,
    edmi_parse_read_registers_answer,
)
from driver.interface.edmi_structs import (
    EDMIDateTime,
    EDMIFileChannelInfo,
    EDMIFileInfo,
    EDMIFileField,
    EDMIProfileSpec,
    EDMIReadFile,
    EDMISearchFile,
    EDMISearchFileDir,
    EDMISurvey,
)
from driver.transport.serial_transport import SerialNotReadyError, SerialTransport
from driver.utils import bytes_to_hex, combine_packets

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
log.propagate = True


class Media:
    """
    Synchronous Media API.

    Threading:
      - Media is protected with threading.Lock to preserve existing behavior.
      - Serial single-in-flight is also protected inside SerialTransport.
    """

    def __init__(self, serial_transport: SerialTransport, debug: bool = True) -> None:
        self.transport = serial_transport
        self._lock = threading.Lock()
        self.debug = debug
        self._is_connected = False  # retained for interface compatibility
        self._profile_read_limit_cache: dict[tuple[int, int], int] = {}

        self.username: str = ""
        self.password: str = ""
        self.serial_number: str = ""

    # ----------------------------
    # Compatibility helpers (old interface)
    # ----------------------------

    def _ensure_connected(self) -> None:
        # Old code opened the port here. New connector manages lifecycle.
        # We only reflect "connected" status for legacy callers.
        self._is_connected = bool(getattr(self.transport, "is_ready", lambda: False)())

    def _safe_disconnect(self) -> None:
        # Old code closed the port. New connector keeps retry loop + owns lifecycle.
        # keep_open semantics is handled at protocol level now, so this is a no-op.
        self._is_connected = False

    def bind(self, username: str, password: str, serial_number: str) -> None:
        self.username = username
        self.password = password
        self.serial_number = serial_number

    # ----------------------------
    # Public API
    # ----------------------------

    def edmi_login(
        self,
        username: str,
        password: str,
        serial_number: str,
        *,
        keep_open: bool = False,
    ):
        wake_up = wake_up_seq()
        login_packet = edmi_create_login_packet(
            username=username,
            password=password,
            serial=serial_number,
        )
        wlogin_packet = combine_packets(wake_up, login_packet)

        first_err = EDMI_ERROR_CODE.NONE
        with self._lock:
            try:
                self._ensure_connected()
                try:
                    self.transport.flush_input()
                except SerialNotReadyError:
                    return EDMI_ERROR_CODE.GET_METER_ATTENTION_FAILED

                self.transport.write_packet(wlogin_packet)
                if self.debug:
                    log.info("TX <- %s", bytes_to_hex(wlogin_packet))

                received = self.transport.read_edmi_packet()
                payload = edmi_pre_process(received)
                if self.debug:
                    log.info("RX <- %s", bytes_to_hex(payload))

                ret = edmi_validate_crc(payload)
                if ret != EDMI_ERROR_CODE.NONE:
                    return ret

                return edmi_parse_login_answer(payload)

            except SerialNotReadyError:
                return EDMI_ERROR_CODE.GET_METER_ATTENTION_FAILED
            except Exception:
                log.error("Login operation failed", exc_info=True)
                self._safe_disconnect()
                raise
            finally:
                # keep_open is legacy; transport/connector manage lifetime.
                # We only mimic the old "connected" flag.
                if not keep_open:
                    self._safe_disconnect()

    def edmi_read_registers(
        self,
        username: str,
        password: str,
        serial_number: str,
        regs: Iterable[Any],
        *,
        keep_open: bool = False,
        do_login: bool = True,
    ) -> Tuple[Iterable[Any], Any]:
        wake_up = wake_up_seq()
        login_packet = edmi_create_login_packet(
            username=username,
            password=password,
            serial=serial_number,
        )
        wlogin_packet = combine_packets(wake_up, login_packet)

        read_regs_packet = edmi_create_read_registers_packet(
            serial=serial_number,
            regs=[int(reg.Address) for reg in regs],
        )

        with self._lock:
            try:
                self._ensure_connected()

                if do_login:
                    self.transport.write_packet(wlogin_packet)
                    if self.debug:
                        log.info("TX <- %s", bytes_to_hex(wlogin_packet))

                    received = self.transport.read_edmi_packet()
                    payload = edmi_pre_process(received)
                    if self.debug:
                        log.info("RX <- %s", bytes_to_hex(payload))

                    ret = edmi_validate_crc(payload)
                    if ret != EDMI_ERROR_CODE.NONE:
                        raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                    ret = edmi_parse_login_answer(payload)
                    if ret != EDMI_ERROR_CODE.NONE:
                        raise RuntimeError(f"Login failed. ERROR: {ret}")

                self.transport.write_packet(read_regs_packet)
                if self.debug:
                    log.info("TX <- %s", bytes_to_hex(read_regs_packet))

                received = self.transport.read_edmi_packet()
                payload = edmi_pre_process(received)
                if self.debug:
                    log.info("RX <- %s", bytes_to_hex(payload))

                ret = edmi_validate_crc(payload)
                if ret != EDMI_ERROR_CODE.NONE:
                    raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                err = edmi_parse_read_registers_answer(payload, regs=regs)
                return regs, err

            except SerialNotReadyError:
                raise
            except Exception:
                log.error("Read registers operation failed", exc_info=True)
                self._safe_disconnect()
                raise
            finally:
                if not keep_open:
                    self._safe_disconnect()

    def flush_input(self) -> None:
        with self._lock:
            try:
                self._ensure_connected()
                self.transport.flush_input()
            except SerialNotReadyError:
                raise
            except Exception:
                log.error("Flush operation failed", exc_info=True)
                self._safe_disconnect()
                raise

    def edmi_test_login_meters(self, meters: Iterable[Any]) -> list[int]:
        ok_serials: list[int] = []
        for meter in meters:
            serial = getattr(meter, "serial_number", None)
            if isinstance(serial, tuple):
                serial = serial[0] if serial else None
            if serial is None:
                continue

            try:
                err = self.edmi_login(
                    username=getattr(meter, "username", ""),
                    password=getattr(meter, "password", ""),
                    serial_number=int(serial),
                    keep_open=False,
                )
            except TimeoutError:
                continue
            except Exception:
                continue

            if err == EDMI_ERROR_CODE.NONE:
                ok_serials.append(int(serial))

        return ok_serials

    def edmi_read_profile(
        self,
        username: str,
        password: str,
        serial_number: str,
        survey: int,
        from_datetime: datetime | EDMIDateTime,
        to_datetime: datetime | EDMIDateTime,
        *,
        max_records: int | None = None,
        profile_spec: EDMIProfileSpec | None = None,
        keep_open: bool = False,
        do_login: bool = True,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> tuple[EDMIProfileSpec, list[EDMIFileField], EDMI_ERROR_CODE]:
        wake_up = wake_up_seq()
        login_packet = edmi_create_login_packet(
            username=username,
            password=password,
            serial=serial_number,
        )
        wlogin_packet = combine_packets(wake_up, login_packet)
        if profile_spec is None:
            profile_spec = EDMIProfileSpec(
                Survey=int(survey),
                Interval=0,
                FromDateTime=EDMIDateTime(0, 0, 0, 0, 0, 0, True),
                ToDateTime=EDMIDateTime(0, 0, 0, 0, 0, 0, True),
                RecordsCount=0,
                ChannelsCount=0,
                ChannelsInfo=[],
                Name="",
            )

        first_err = EDMI_ERROR_CODE.NONE
        with self._lock:
            try:
                self._ensure_connected()

                if do_login:
                    self.transport.write_packet(wlogin_packet)
                    if self.debug:
                        log.info("TX <- %s", bytes_to_hex(wlogin_packet))

                    received = self.transport.read_edmi_packet()
                    payload = edmi_pre_process(received)
                    if self.debug:
                        log.info("RX <- %s", bytes_to_hex(payload))

                    ret = edmi_validate_crc(payload)
                    if ret != EDMI_ERROR_CODE.NONE:
                        raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                    ret = edmi_parse_login_answer(payload)
                    if ret != EDMI_ERROR_CODE.NONE:
                        raise RuntimeError(f"Login failed. ERROR: {ret}")

                info_regs = edmi_get_file_info_regs(survey)
                info_regs_packet = edmi_create_read_registers_packet(
                    serial=serial_number,
                    regs=[int(reg.Address) for reg in info_regs],
                )
                self.transport.write_packet(info_regs_packet)
                if self.debug:
                    log.info("TX <- %s", bytes_to_hex(info_regs_packet))

                received = self.transport.read_edmi_packet()
                payload = edmi_pre_process(received)
                if self.debug:
                    log.info("RX <- %s", bytes_to_hex(payload))

                ret = edmi_validate_crc(payload)
                if ret != EDMI_ERROR_CODE.NONE:
                    raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                err = edmi_parse_read_registers_answer(payload, info_regs)
                if err != EDMI_ERROR_CODE.NONE:
                    if first_err == EDMI_ERROR_CODE.NONE:
                        first_err = err

                file_info = EDMIFileInfo(
                    Interval=0,
                    ChannelsCount=0,
                    StartRecord=0,
                    RecordsCount=0,
                    RecordSize=0,
                    Type=0,
                    Name="",
                    ValueLen=0,
                )
                err = edmi_set_profile_info(file_info, info_regs)
                if err != EDMI_ERROR_CODE.NONE:
                    if first_err == EDMI_ERROR_CODE.NONE:
                        first_err = err
                    if err == EDMI_ERROR_CODE.REGISTER_NOT_FOUND:
                        stale_keys = [key for key in self._profile_read_limit_cache if key[0] == int(survey)]
                        for key in stale_keys:
                            del self._profile_read_limit_cache[key]
                        return profile_spec, [], err

                info_access_packet = edmi_create_read_profile_info_access_packet(
                    serial_number,
                    survey,
                )
                self.transport.write_packet(info_access_packet)
                if self.debug:
                    log.info("TX <- %s", bytes_to_hex(info_access_packet))

                received = self.transport.read_edmi_packet()
                payload = edmi_pre_process(received)
                if self.debug:
                    log.info("RX <- %s", bytes_to_hex(payload))

                ret = edmi_validate_crc(payload)
                if ret != EDMI_ERROR_CODE.NONE:
                    raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                err = edmi_parse_read_profile_info_access_payload(payload, file_info)
                if err != EDMI_ERROR_CODE.NONE:
                    return profile_spec, [], err
                if self.debug:
                    log.info(
                        "FILE_INFO survey=%s start=%s records=%s size=%s interval=%s channels=%s",
                        int(survey),
                        file_info.StartRecord,
                        file_info.RecordsCount,
                        file_info.RecordSize,
                        file_info.Interval,
                        file_info.ChannelsCount,
                    )

                channels: list[EDMIFileChannelInfo] = []
                if file_info.ChannelsCount > 0:
                    for ch in range(file_info.ChannelsCount):
                        ch_regs = edmi_get_file_channel_regs(survey, ch)
                        ch_regs_packet = edmi_create_read_registers_packet(
                            serial=serial_number,
                            regs=[int(reg.Address) for reg in ch_regs],
                        )
                        self.transport.write_packet(ch_regs_packet)
                        if self.debug:
                            log.info("TX <- %s", bytes_to_hex(ch_regs_packet))

                        received = self.transport.read_edmi_packet()
                        payload = edmi_pre_process(received)
                        if self.debug:
                            log.info("RX <- %s", bytes_to_hex(payload))

                        ret = edmi_validate_crc(payload)
                        if ret != EDMI_ERROR_CODE.NONE:
                            raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                        err = edmi_parse_read_registers_answer(payload, ch_regs)
                        if err != EDMI_ERROR_CODE.NONE:
                            if first_err == EDMI_ERROR_CODE.NONE:
                                first_err = err

                        ch_info = EDMIFileChannelInfo(
                            Type=0,
                            UnitCode=0,
                            ScalingCode=0,
                            ScalingFactor=0.0,
                            Name="",
                        )
                        err = edmi_set_file_channel_info(ch_info, ch_regs)
                        if err != EDMI_ERROR_CODE.NONE:
                            if first_err == EDMI_ERROR_CODE.NONE:
                                first_err = err

                        channels.append(ch_info)
                else:
                    channels = list(profile_spec.ChannelsInfo)

                profile_spec.Survey = int(survey)
                if file_info.Interval:
                    profile_spec.Interval = file_info.Interval
                profile_spec.FromDateTime = EDMIDateTime(0, 0, 0, 0, 0, 0, True)
                profile_spec.ToDateTime = EDMIDateTime(0, 0, 0, 0, 0, 0, True)
                profile_spec.RecordsCount = 0
                if file_info.ChannelsCount:
                    profile_spec.ChannelsCount = file_info.ChannelsCount
                elif profile_spec.ChannelsCount <= 0:
                    profile_spec.ChannelsCount = len(channels)
                if channels:
                    profile_spec.ChannelsInfo = channels
                if file_info.Name:
                    profile_spec.Name = file_info.Name

                from_dt = edmi_coerce_datetime(from_datetime)
                to_dt = edmi_coerce_datetime(to_datetime)

                def _search(dt: EDMIDateTime) -> EDMISearchFile:
                    search = EDMISearchFile(
                        StartRecord=file_info.StartRecord,
                        DateTime=dt,
                        DirOrResult=EDMISearchFileDir.EDMI_SEARCH_FILE_DIR_FORM_START_RECORD_BACKWARD,
                    )
                    packet = edmi_create_search_profile_packet(
                        serial_number,
                        survey,
                        search.StartRecord,
                        search.DateTime,
                        search.DirOrResult,
                    )
                    self.transport.write_packet(packet)
                    if self.debug:
                        log.info("TX <- %s", bytes_to_hex(packet))

                    received = self.transport.read_edmi_packet()
                    payload = edmi_pre_process(received)
                    if self.debug:
                        log.info("RX <- %s", bytes_to_hex(payload))

                    ret = edmi_validate_crc(payload)
                    if ret != EDMI_ERROR_CODE.NONE:
                        raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                    err = edmi_parse_search_profile_payload(payload, search)
                    if err != EDMI_ERROR_CODE.NONE:
                        nonlocal first_err
                        if first_err == EDMI_ERROR_CODE.NONE:
                            first_err = err
                    if self.debug:
                        log.info(
                            "FILE_SEARCH dt=%s start=%s result=%s",
                            search.DateTime,
                            search.StartRecord,
                            search.DirOrResult,
                        )
                    return search

                def _read_records(
                    start_record: int,
                    count: int,
                    total_records: int,
                    progress_cb: Callable[[int, int], None] | None,
                ) -> tuple[list[EDMIFileField], EDMI_ERROR_CODE]:
                    read = EDMIReadFile(
                        StartRecord=start_record,
                        RecordsCount=count,
                        RecordOffset=0,
                        RecordSize=file_info.RecordSize,
                    )
                    cache_key = (
                        int(survey),
                        read.RecordSize,
                        profile_spec.ChannelsCount,
                    )
                    cached_limit = self._profile_read_limit_cache.get(cache_key)
                    if int(survey) == int(EDMISurvey.LS01):
                        per_read_limit = 59
                    elif int(survey) == int(EDMISurvey.LS03):
                        per_read_limit = 288
                    elif profile_spec.Interval > 0:
                        per_read_limit = max(1, int(math.ceil(86400 / profile_spec.Interval)))
                    else:
                        per_read_limit = 48
                    if cached_limit:
                        per_read_limit = min(per_read_limit, cached_limit)
                    # if file_info.RecordsCount > 0:
                    #     per_read_limit = min(per_read_limit, file_info.RecordsCount)

                    fields_all: list[EDMIFileField] = []
                    remaining = read.RecordsCount
                    next_start = read.StartRecord
                    records_read = 0
                    while remaining > 0:
                        chunk = min(remaining, per_read_limit)
                        if self.debug:
                            log.info(
                                "FILE_READ start=%s count=%s size=%s",
                                next_start,
                                chunk,
                                read.RecordSize,
                            )

                        read_packet = edmi_create_read_profile_packet(
                            serial_number,
                            survey,
                            next_start,
                            chunk,
                            read.RecordOffset,
                            read.RecordSize,
                        )
                        self.transport.write_packet(read_packet)
                        if self.debug:
                            log.info("TX <- %s", bytes_to_hex(read_packet))

                        received = self.transport.read_edmi_packet()
                        payload = edmi_pre_process(received)
                        if self.debug:
                            log.info("RX <- %s", bytes_to_hex(payload))

                        ret = edmi_validate_crc(payload)
                        if ret != EDMI_ERROR_CODE.NONE:
                            raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")

                        read_resp = EDMIReadFile(
                            StartRecord=next_start,
                            RecordsCount=chunk,
                            RecordOffset=read.RecordOffset,
                            RecordSize=read.RecordSize,
                        )
                        fields, err = edmi_parse_read_profile_payload(payload, read_resp, profile_spec)
                        if err != EDMI_ERROR_CODE.NONE:
                            return fields_all, err

                        fields_all.extend(fields)
                        channels_count = profile_spec.ChannelsCount
                        if self.debug:
                            data_len = max(0, len(payload) - 3)
                            log.info(
                                "FILE_READ_RESP req_count=%s resp_count=%s payload_len=%s data_len=%s record_size=%s channels=%s fields=%s",
                                chunk,
                                read_resp.RecordsCount,
                                len(payload),
                                data_len,
                                read_resp.RecordSize,
                                channels_count,
                                len(fields),
                            )
                        if read_resp.RecordsCount > 0 and read_resp.RecordsCount < chunk:
                            per_read_limit = min(per_read_limit, read_resp.RecordsCount)
                            self._profile_read_limit_cache[cache_key] = per_read_limit
                            if self.debug:
                                log.info(
                                    "FILE_READ_LIMIT learned=%s key=%s",
                                    per_read_limit,
                                    cache_key,
                                )
                        if channels_count <= 0:
                            return fields_all, EDMI_ERROR_CODE.REQUEST_WRONG_LENGTH
                        records_returned = len(fields) // channels_count
                        if self.debug:
                            log.info("Number of records: %s", records_returned)
                        if records_returned <= 0:
                            return fields_all, EDMI_ERROR_CODE.RESPONSE_WRONG_LENGTH
                        records_read += records_returned
                        if progress_cb is not None:
                            progress_cb(records_read, total_records)
                        remaining -= records_returned
                        next_start += records_returned
                    return fields_all, EDMI_ERROR_CODE.NONE

                from_search = _search(from_dt)
                to_search = _search(to_dt)

                record_count = to_search.StartRecord - from_search.StartRecord + 1
                if record_count < 1:
                    record_count = 1
                if max_records is not None:
                    record_count = min(record_count, max_records)

                fields_all, err = _read_records(
                    from_search.StartRecord,
                    record_count,
                    record_count,
                    progress_cb,
                )
                if err != EDMI_ERROR_CODE.NONE:
                    final_err = first_err if first_err != EDMI_ERROR_CODE.NONE else err
                    return profile_spec, fields_all, final_err

                channels_count = profile_spec.ChannelsCount or len(profile_spec.ChannelsInfo)
                if channels_count <= 0:
                    return profile_spec, fields_all, EDMI_ERROR_CODE.REQUEST_WRONG_LENGTH

                profile_spec.StartRecord = from_search.StartRecord
                profile_spec.FromDateTime = from_dt
                profile_spec.ToDateTime = to_search.DateTime
                profile_spec.RecordsCount = len(fields_all) // channels_count

                final_err = first_err if first_err != EDMI_ERROR_CODE.NONE else EDMI_ERROR_CODE.NONE
                return profile_spec, fields_all, final_err

            except SerialNotReadyError:
                raise
            except Exception:
                log.error("Read profile operation failed", exc_info=True)
                self._safe_disconnect()
                raise
            finally:
                if not keep_open:
                    self._safe_disconnect()
