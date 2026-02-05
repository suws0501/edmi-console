from __future__ import annotations

import time
from datetime import datetime

from driver.edmi_enums import EDMI_TYPE
from driver.interface.edmi_structs import (
    EDMIDateTime,
    EDMIFileChannelInfo,
    EDMIProfileSpec,
    EDMISurvey,
)
from driver.interface.media import Media
from driver.meters_config import PASWORD, SERIAL_NUMBER, USERNAME
from driver.serial_settings import BAUD, PORT, TIMEOUT_S
from driver.transport.serial_transport import SerialConfig, SerialTransport


def main() -> None:
    t_start = time.perf_counter()

    cfg = SerialConfig(
        port=PORT,
        baudrate=BAUD,
        timeout_s=TIMEOUT_S,
        write_timeout_s=TIMEOUT_S,
        exclusive=True,
    )
    transport = SerialTransport(cfg)
    media = Media(serial_transport=transport, debug=True)

    try:
        transport.connect()

        from_dt_str = "2026-01-18 00:30:00"
        to_dt_str = "2026-01-20 10:00:00"
        from_dt = datetime.strptime(from_dt_str, "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime(to_dt_str, "%Y-%m-%d %H:%M:%S")

        print(f"Requested window: {from_dt_str} -> {to_dt_str} (survey {EDMISurvey.LS01.name})")
        channels = [
            EDMIFileChannelInfo(
                Type=EDMI_TYPE.FLOAT,
                UnitCode=0,
                ScalingCode=0,
                ScalingFactor=1.0,
                Name=f"CH{idx}",
            )
            for idx in range(EDMIProfileSpec.MAX_CHANNELS)
        ]
        profile_spec = EDMIProfileSpec(
            Survey=int(EDMISurvey.LS01),
            Interval=0,
            FromDateTime=EDMIDateTime(0, 0, 0, 0, 0, 0, True),
            ToDateTime=EDMIDateTime(0, 0, 0, 0, 0, 0, True),
            RecordsCount=0,
            ChannelsCount=len(channels),
            ChannelsInfo=channels,
            Name="",
        )
        profile_spec, fields, err = media.edmi_read_profile(
            username=USERNAME,
            password=PASWORD,
            serial_number=SERIAL_NUMBER,
            survey=EDMISurvey.LS03,
            from_datetime=from_dt,
            to_datetime=to_dt,
            max_records=None,
            profile_spec=profile_spec,
            keep_open=True,
            do_login=True,
        )

        print(
            "Profile metadata:",
            {
                "start_record": getattr(profile_spec, "StartRecord", None),
                "records_count": profile_spec.RecordsCount,
                "interval_sec": profile_spec.Interval,
            },
        )
        if err is not None:
            print(f"Profile read error: {err}")
        print(f"Fields returned: {len(fields)}")

    finally:
        transport.close()
        t_end = time.perf_counter()
        print(f"total time : {(t_end - t_start) * 1e3:.3f} ms")


if __name__ == "__main__":
    main()
