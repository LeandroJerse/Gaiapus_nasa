import unittest
from datetime import datetime, timezone

READING = {'node_id': 0xA1B2C3D4, 'seq': 128, 'raw_15': 18342, 'raw_40': 21011, 't_solo_15': 24.31, 't_solo_40': 23.1,
           't_ar': 29.8, 'ur_ar': 54, 'chuva_pulsos': 2, 'vbat': 3285, 'idade_min': 0, 'flags': 0}


class FakeTelemetryTests(unittest.TestCase):
    def test_uplink_is_25_bytes_and_round_trips(self):
        from data.fake.telemetry import decode_uplink, encode_uplink
        frame = encode_uplink(READING)
        self.assertEqual(len(frame), 25)
        self.assertEqual(frame[0], 1)
        self.assertEqual(frame[1:5], bytes.fromhex('d4c3b2a1'))  # little-endian node_id
        decoded = decode_uplink(frame)
        self.assertEqual({k: decoded[k] for k in READING}, READING)
        with self.assertRaises(ValueError):
            decode_uplink(frame[:24])
        with self.assertRaises(ValueError):
            decode_uplink(bytes([2]) + frame[1:])

    def test_gateway_matches_the_protocol_example(self):
        from data.fake.telemetry import decode_uplink, encode_uplink, gateway_message
        received = datetime(2026, 11, 14, 13, 30, 2, tzinfo=timezone.utc)
        message = gateway_message(decode_uplink(encode_uplink({**READING, 'idade_min': 0})), received, -97, 7.5)
        self.assertEqual(message['device_id'], 'a1b2c3d4')
        self.assertEqual(message['observed_at'], '2026-11-14T13:30:02Z')
        self.assertEqual(message['rain_mm'], 0.4)
        self.assertEqual([r['depth_cm'] for r in message['readings']], [15, 40])
        resent = gateway_message(decode_uplink(encode_uplink({**READING, 'idade_min': 90})), received, -97, 7.5)
        self.assertEqual(resent['observed_at'], '2026-11-14T12:00:02Z')

    def test_events_are_idempotent_and_flag_quality(self):
        from data.fake.telemetry import FLAG_SENSOR_ERROR, app_events, decode_uplink, encode_uplink, gateway_message
        received = datetime(2026, 11, 14, 13, 30, 2, tzinfo=timezone.utc)
        message = gateway_message(decode_uplink(encode_uplink(READING)), received, -97, 7.5)
        events = app_events(message)
        self.assertEqual([e['event_id'] for e in events], ['a1b2c3d4-128-15', 'a1b2c3d4-128-40'])
        self.assertTrue(all(e['synthetic'] and e['quality'] == 'ok' and e['moisture_m3_m3'] is not None for e in events))
        broken = app_events({**message, 'flags': FLAG_SENSOR_ERROR})
        self.assertTrue(all(e['quality'] == 'sensor_error' and e['moisture_m3_m3'] is None for e in broken))
        self.assertTrue(all(e['moisture_m3_m3'] is None for e in app_events(message, calibrated=False)))

    def test_generated_moisture_follows_the_bucket_and_includes_resends(self):
        from datetime import date
        from data.fake.telemetry import generate
        frames, messages, events, truth = generate('5107925', 'soy', date(2019, 10, 5), devices=1, seed=3)
        ids = [e['event_id'] for e in events]
        self.assertGreater(len(ids), len(set(ids)))  # duplicates exist and are detectable
        self.assertTrue(any(m['observed_at'] != m['received_at'] for m in messages))  # buffer resends
        self.assertTrue(any(t['lost'] for t in truth))
        received = {(m['device_id'], m['seq']) for m in messages}
        self.assertTrue(all((t['device_id'], t['seq']) in received for t in truth if not t['lost']))
        ordered = sorted(truth, key=lambda t: t['bucket_fraction'])
        quarter = len(ordered) // 4
        dry, wet = ordered[:quarter], ordered[-quarter:]
        self.assertLess(sum(t['theta_15_true'] for t in dry) / quarter, sum(t['theta_15_true'] for t in wet) / quarter)


if __name__ == '__main__':
    unittest.main()
