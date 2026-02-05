from __future__ import annotations

from datetime import datetime

from driver.edmi_enums import EDMI_ERROR_CODE
from driver.frames_codec.generics import edmi_pre_process, edmi_validate_crc, wake_up_seq
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
    EDMIProfileSpec,
    EDMIReadFile,
    EDMISearchFile,
    EDMISearchFileDir,
    EDMISurvey,
)
from driver.meters_config import PASWORD, SERIAL_NUMBER, USERNAME
from driver.serial_settings import BAUD, PORT, TIMEOUT_S
from driver.transport.serial_transport import SerialConfig, SerialTransport
from driver.utils import bytes_to_hex, combine_packets


def main() -> None:
    cfg = SerialConfig(
        port=PORT,
        baudrate=BAUD,
        timeout_s=TIMEOUT_S,
        write_timeout_s=TIMEOUT_S,
        exclusive=True,
    )
    transport = SerialTransport(cfg)
    transport.connect()

    try:
        survey = int(EDMISurvey.LS01)
        from_dt = datetime.strptime("2026-01-18 00:30:00", "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime("2026-01-20 10:00:00", "%Y-%m-%d %H:%M:%S")

        wake_up = wake_up_seq()
        login_packet = edmi_create_login_packet(
            username=USERNAME,
            password=PASWORD,
            serial=SERIAL_NUMBER,
        )
        wlogin_packet = combine_packets(wake_up, login_packet)

        transport.write_packet(wlogin_packet)
        received = transport.read_edmi_packet()
        payload = edmi_pre_process(received)
        ret = edmi_validate_crc(payload)
        if ret != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")
        ret = edmi_parse_login_answer(payload)
        if ret != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Login failed. ERROR: {ret}")

        info_regs = edmi_get_file_info_regs(survey)
        info_regs_packet = edmi_create_read_registers_packet(
            serial=SERIAL_NUMBER,
            regs=[int(reg.Address) for reg in info_regs],
        )
        transport.write_packet(info_regs_packet)
        received = transport.read_edmi_packet()
        payload = edmi_pre_process(received)
        ret = edmi_validate_crc(payload)
        if ret != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")
        err = edmi_parse_read_registers_answer(payload, info_regs)
        if err != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Read registers error: {err}")

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
            raise RuntimeError(f"Profile info error: {err}")

        info_access_packet = edmi_create_read_profile_info_access_packet(
            SERIAL_NUMBER,
            survey,
        )
        transport.write_packet(info_access_packet)
        received = transport.read_edmi_packet()
        payload = edmi_pre_process(received)
        ret = edmi_validate_crc(payload)
        if ret != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")
        err = edmi_parse_read_profile_info_access_payload(payload, file_info)
        if err != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Profile access error: {err}")

        channels: list[EDMIFileChannelInfo] = []
        if file_info.ChannelsCount > 0:
            for ch in range(file_info.ChannelsCount):
                ch_regs = edmi_get_file_channel_regs(survey, ch)
                ch_regs_packet = edmi_create_read_registers_packet(
                    serial=SERIAL_NUMBER,
                    regs=[int(reg.Address) for reg in ch_regs],
                )
                transport.write_packet(ch_regs_packet)
                received = transport.read_edmi_packet()
                payload = edmi_pre_process(received)
                ret = edmi_validate_crc(payload)
                if ret != EDMI_ERROR_CODE.NONE:
                    raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")
                err = edmi_parse_read_registers_answer(payload, ch_regs)
                if err != EDMI_ERROR_CODE.NONE:
                    raise RuntimeError(f"Read channel error: {err}")

                ch_info = EDMIFileChannelInfo(
                    Type=0,
                    UnitCode=0,
                    ScalingCode=0,
                    ScalingFactor=0.0,
                    Name="",
                )
                err = edmi_set_file_channel_info(ch_info, ch_regs)
                if err != EDMI_ERROR_CODE.NONE:
                    raise RuntimeError(f"Channel info error: {err}")

                channels.append(ch_info)

        profile_spec = EDMIProfileSpec(
            Survey=int(survey),
            Interval=file_info.Interval,
            FromDateTime=EDMIDateTime(0, 0, 0, 0, 0, 0, True),
            ToDateTime=EDMIDateTime(0, 0, 0, 0, 0, 0, True),
            RecordsCount=0,
            ChannelsCount=file_info.ChannelsCount or len(channels),
            ChannelsInfo=channels,
            Name=file_info.Name,
        )

        def _search(dt: EDMIDateTime) -> EDMISearchFile:
            search = EDMISearchFile(
                StartRecord=file_info.StartRecord,
                DateTime=dt,
                DirOrResult=EDMISearchFileDir.EDMI_SEARCH_FILE_DIR_FORM_START_RECORD_BACKWARD,
            )
            packet = edmi_create_search_profile_packet(
                SERIAL_NUMBER,
                survey,
                search.StartRecord,
                search.DateTime,
                search.DirOrResult,
            )
            transport.write_packet(packet)
            received = transport.read_edmi_packet()
            payload = edmi_pre_process(received)
            ret = edmi_validate_crc(payload)
            if ret != EDMI_ERROR_CODE.NONE:
                raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")
            err = edmi_parse_search_profile_payload(payload, search)
            if err != EDMI_ERROR_CODE.NONE:
                raise RuntimeError(f"Search error: {err}")
            return search

        from_dt_edmi = EDMIDateTime(from_dt.year % 100, from_dt.month, from_dt.day, from_dt.hour, from_dt.minute, from_dt.second, False)
        to_dt_edmi = EDMIDateTime(to_dt.year % 100, to_dt.month, to_dt.day, to_dt.hour, to_dt.minute, to_dt.second, False)

        from_search = _search(from_dt_edmi)
        to_search = _search(to_dt_edmi)

        record_count = to_search.StartRecord - from_search.StartRecord + 1
        if record_count < 1:
            record_count = 1

        read = EDMIReadFile(
            StartRecord=from_search.StartRecord,
            RecordsCount=record_count,
            RecordOffset=0,
            RecordSize=file_info.RecordSize,
        )
        read_packet = edmi_create_read_profile_packet(
            SERIAL_NUMBER,
            survey,
            read.StartRecord,
            read.RecordsCount,
            read.RecordOffset,
            read.RecordSize,
        )
        transport.write_packet(read_packet)
        received = transport.read_edmi_packet()
        payload = edmi_pre_process(received)
        ret = edmi_validate_crc(payload)
        if ret != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Corrupted data. CRC not match. ERROR: {ret}")
        fields, err = edmi_parse_read_profile_payload(payload, read, profile_spec)
        if err != EDMI_ERROR_CODE.NONE:
            raise RuntimeError(f"Read profile error: {err}")

        print(f"Records read: {len(fields)}")
        print(bytes_to_hex(payload))

    finally:
        transport.close()


if __name__ == "__main__":
    main()
