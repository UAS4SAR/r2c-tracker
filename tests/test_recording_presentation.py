import unittest
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

from recording_presentation import recording_labels


class RecordingPresentationTest(unittest.TestCase):
    def clip(self, session, seconds=0, **changes):
        when = datetime(2026, 9, 14, 17, 28, tzinfo=UTC) + timedelta(seconds=seconds)
        fields = dict(session_id=session, organization_id="org", device_credential_id="ipad",
                      drone_designator="Drone", media_kind="recording",
                      recorded_at=when, recorded_at_local=when)
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_short_and_long_clips_have_distinct_labels_without_changing_identity(self):
        first, second = self.clip("runt"), self.clip("long", 150)
        labels = recording_labels([second, first])
        self.assertEqual({"runt": "Drone-1", "long": "Drone-2"}, labels)
        self.assertEqual("Drone", first.drone_designator)
        self.assertEqual("runt", first.session_id)
        self.assertEqual(labels, recording_labels([first, second]))

    def test_numbering_never_combines_tablets_organizations_days_or_live_sources(self):
        clips = [self.clip("one"), self.clip("tablet", device_credential_id="android"),
                 self.clip("org", organization_id="other"), self.clip("day", 86400),
                 self.clip("live", media_kind="live"), self.clip("unknown", recorded_at=None)]
        self.assertEqual({}, recording_labels(clips))

    def test_case_variants_and_equal_times_are_deterministic(self):
        self.assertEqual({"a": "Drone-1", "b": "drone-2"}, recording_labels([
            self.clip("b", drone_designator="drone"), self.clip("a")]))
