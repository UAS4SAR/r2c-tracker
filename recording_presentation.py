"""Distinguish captured clips without changing aircraft or recording identities."""
from collections import defaultdict


def recording_labels(streams):
    """Number available clips per tablet, aircraft and local day, oldest first.

    A designator is not a flight identifier. These labels deliberately describe
    separate clips, not parts of a presumed continuous flight.
    """
    groups = defaultdict(list)
    for stream in streams:
        if stream.media_kind != "recording" or stream.recorded_at is None:
            continue
        local_time = stream.recorded_at_local or stream.recorded_at
        groups[(stream.organization_id, stream.device_credential_id,
                stream.drone_designator.strip().casefold(), local_time.date())].append(stream)
    labels = {}
    for clips in groups.values():
        if len(clips) < 2:
            continue
        for number, clip in enumerate(sorted(
                clips, key=lambda item: (item.recorded_at, item.session_id)), 1):
            labels[clip.session_id] = f"{clip.drone_designator}-{number}"
    return labels
