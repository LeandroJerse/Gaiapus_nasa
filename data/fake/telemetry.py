"""Synthetic probe telemetry following protocolo-dados.md (plan 2: models with and without hardware).

Every record is SYNTHETIC. Soil water comes from the physical bucket driven by real
NASA POWER weather of a municipality, so readings react to rain and crop demand; the
raw oscillator counts invert a made-up calibration curve. Resends, duplicates, gaps,
battery decay and sensor errors are injected at known rates so the ingestion and the
data-quality rules can be tested. Never mix these rows with real labels or sensor data.
"""
import argparse
import json
import math
import random
import struct
from datetime import date, datetime, timedelta, timezone

from data.ingest.water_balance_run import ROOT, save_json, write_csv
from data.ingest.water_projection import advance
from data.models.water_balance import CROPS

OUT = ROOT / 'data/fake/telemetry_v1'
FRAME = struct.Struct('<BIHHHhhhBHHHB')  # 25 bytes, little-endian, see protocolo-dados.md
assert FRAME.size == 25
MM_PER_PULSE = 0.2
FLAG_BUTTON, FLAG_LOW_BATTERY, FLAG_SENSOR_ERROR = 1, 2, 4
# Synthetic calibration: volumetric water (m3/m3) = a + b * raw. Not a real sensor curve.
CALIBRATION = {15: (0.62, -2.1e-5), 40: (0.66, -2.2e-5)}
WILTING, FIELD_CAPACITY = 0.12, 0.34


def encode_uplink(reading):
    return FRAME.pack(1, reading['node_id'], reading['seq'], reading['raw_15'], reading['raw_40'],
                      round(reading['t_solo_15'] * 100), round(reading['t_solo_40'] * 100), round(reading['t_ar'] * 100),
                      reading['ur_ar'], reading['chuva_pulsos'], reading['vbat'], reading['idade_min'], reading['flags'])


def decode_uplink(frame):
    if len(frame) != FRAME.size:
        raise ValueError('Uplink must be 25 bytes')
    (version, node_id, seq, raw_15, raw_40, t15, t40, tar, rh, pulses, vbat, age, flags) = FRAME.unpack(frame)
    if version != 1:
        raise ValueError('Unsupported uplink version')
    return {'versao': version, 'node_id': node_id, 'seq': seq, 'raw_15': raw_15, 'raw_40': raw_40, 't_solo_15': t15 / 100,
            't_solo_40': t40 / 100, 't_ar': tar / 100, 'ur_ar': rh, 'chuva_pulsos': pulses, 'vbat': vbat,
            'idade_min': age, 'flags': flags}


def gateway_message(decoded, received_at, rssi, snr):
    """Gateway JSON: observed_at = received_at - idade_min; rain = pulses x mm per pulse."""
    observed = received_at - timedelta(minutes=decoded['idade_min'])
    return {'device_id': f'{decoded["node_id"]:08x}', 'seq': decoded['seq'],
            'observed_at': observed.strftime('%Y-%m-%dT%H:%M:%SZ'), 'received_at': received_at.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'readings': [{'depth_cm': 15, 'raw_moisture': decoded['raw_15'], 'soil_temp_c': decoded['t_solo_15']},
                         {'depth_cm': 40, 'raw_moisture': decoded['raw_40'], 'soil_temp_c': decoded['t_solo_40']}],
            'air_temp_c': decoded['t_ar'], 'air_rh_pct': decoded['ur_ar'], 'rain_mm': round(decoded['chuva_pulsos'] * MM_PER_PULSE, 2),
            'battery_mv': decoded['vbat'], 'rssi': rssi, 'snr': snr, 'flags': decoded['flags']}


def app_events(message, calibrated=True):
    """One event per depth; event_id makes resends idempotent."""
    events = []
    for item in message['readings']:
        a, b = CALIBRATION[item['depth_cm']]
        moisture = a + b * item['raw_moisture'] if calibrated else None
        valid = moisture is None or 0.0 <= moisture <= 0.6
        error = bool(message['flags'] & FLAG_SENSOR_ERROR)
        events.append({'event_id': f'{message["device_id"]}-{message["seq"]}-{item["depth_cm"]}', 'device_id': message['device_id'],
                       'observed_at': message['observed_at'], 'depth_cm': item['depth_cm'], 'raw_moisture': item['raw_moisture'],
                       'moisture_m3_m3': None if moisture is None or error or not valid else round(moisture, 4),
                       'soil_temp_c': item['soil_temp_c'], 'battery_mv': message['battery_mv'],
                       'quality': 'sensor_error' if error else ('out_of_range' if not valid else
                                                                ('low_battery' if message['flags'] & FLAG_LOW_BATTERY else 'ok')),
                       'air_temp_c': message['air_temp_c'], 'air_rh_pct': message['air_rh_pct'], 'rain_mm': message['rain_mm'],
                       'rssi': message['rssi'], 'snr': message['snr'], 'synthetic': True})
    return events


def raw_from_moisture(moisture, depth):
    a, b = CALIBRATION[depth]
    return max(0, min(65535, round((moisture - a) / b)))


def weather_series(code, start, days):
    import csv
    wanted = {(start + timedelta(days=i)).isoformat() for i in range(days)}
    rows = {}
    with (ROOT / 'data/processed/training_v1/weather_daily.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['municipality_code'] == code and row['day_utc'] in wanted:
                rows[row['day_utc']] = row
    with (ROOT / 'data/processed/water_balance_v1/et0_daily.csv').open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['municipality_code'] == code and row['day_utc'] in rows:
                rows[row['day_utc']]['et0_mm'] = row['et0_mm']
    return [rows[d] for d in sorted(rows)]


def generate(code='5107925', crop='soy', sowing=date(2023, 10, 5), devices=3, interval_min=30, seed=7,
             resend_rate=0.04, loss_rate=0.02, error_rate=0.003):
    rng = random.Random(seed)
    cycle = sum(CROPS[crop]['phase_days'])
    weather = weather_series(code, sowing, cycle)
    capacity = CROPS[crop]['cad_mm']['AD4']
    states = advance(0.7 * capacity, crop, 0, [{'rain_mm': float(w['rain_mm']), 'et0_mm': float(w['et0_mm'])} for w in weather])
    frames, messages, events, truth = [], [], [], []
    for device in range(devices):
        node_id = 0xA1B2C300 + device
        offset = rng.uniform(-0.03, 0.03)  # field heterogeneity around the municipal bucket
        battery, seq, buffer = 3600.0, 0, []
        for day_index, (day, state) in enumerate(zip(weather, states)):
            fraction = state['available_water_fraction']
            rain_today = float(day['rain_mm'])
            for slot in range(24 * 60 // interval_min):
                observed = datetime.combine(sowing + timedelta(days=day_index), datetime.min.time(), timezone.utc) \
                    + timedelta(minutes=slot * interval_min)
                hour = observed.hour + observed.minute / 60
                diurnal = math.sin((hour - 9) / 24 * 2 * math.pi)
                t_min, t_max = float(day['t_min_c']), float(day['t_max_c'])
                t_air = (t_min + t_max) / 2 + (t_max - t_min) / 2 * diurnal + rng.gauss(0, 0.3)
                theta15 = WILTING + (FIELD_CAPACITY - WILTING) * fraction + offset + rng.gauss(0, 0.004)
                theta40 = WILTING + (FIELD_CAPACITY - WILTING) * min(1.0, fraction + 0.08) + offset * 0.7 + rng.gauss(0, 0.003)
                pulses = round(rain_today / MM_PER_PULSE / 48 * (1 + rng.uniform(-0.5, 0.5))) if rain_today > 0.5 else 0
                battery -= 0.012 + rng.uniform(0, 0.004)
                flags = (FLAG_LOW_BATTERY if battery < 3300 else 0) | (FLAG_SENSOR_ERROR if rng.random() < error_rate else 0)
                reading = {'node_id': node_id, 'seq': seq % 65536, 'raw_15': raw_from_moisture(theta15, 15),
                           'raw_40': raw_from_moisture(theta40, 40), 't_solo_15': round(t_air - 2 + rng.gauss(0, 0.2), 2),
                           't_solo_40': round((t_min + t_max) / 2 - 1.5 + rng.gauss(0, 0.1), 2), 't_ar': round(t_air, 2),
                           'ur_ar': max(0, min(100, round(float(day['rh2m_pct']) - 15 * diurnal + rng.gauss(0, 3)))),
                           'chuva_pulsos': pulses, 'vbat': int(battery), 'idade_min': 0, 'flags': flags}
                truth.append({'device_id': f'{node_id:08x}', 'seq': reading['seq'], 'observed_at': observed.isoformat(),
                              'theta_15_true': round(theta15, 4), 'theta_40_true': round(theta40, 4),
                              'bucket_fraction': round(fraction, 4), 'lost': False, 'synthetic': True})
                seq += 1
                if rng.random() < loss_rate:
                    buffer.append((reading, observed))  # not received now; resent later from the probe buffer
                    truth[-1]['lost'] = True
                    continue
                pending = [(reading, observed)]
                if buffer and rng.random() < 0.5:
                    pending.extend(buffer)
                    buffer = []
                for item, item_observed in pending:
                    received = observed + timedelta(seconds=rng.randint(1, 4))
                    item = {**item, 'idade_min': int((received - item_observed).total_seconds() // 60)}
                    copies = 2 if rng.random() < resend_rate else 1  # gateway duplicate
                    for _ in range(copies):
                        frame = encode_uplink(item)
                        message = gateway_message(decode_uplink(frame), received, rssi=rng.randint(-118, -85), snr=round(rng.uniform(-5, 10), 1))
                        frames.append({'device_id': message['device_id'], 'received_at': message['received_at'], 'hex': frame.hex()})
                        messages.append(message)
                        events.extend(app_events(message))
    return frames, messages, events, truth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--municipality', default='5107925')
    parser.add_argument('--crop', default='soy', choices=('soy', 'maize2'))
    parser.add_argument('--sowing', default='2023-10-05')
    parser.add_argument('--devices', type=int, default=3)
    args = parser.parse_args()
    frames, messages, events, truth = generate(args.municipality, args.crop, date.fromisoformat(args.sowing), args.devices)
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / 'uplink_frames.csv', frames)
    (OUT / 'gateway_messages.jsonl').write_text('\n'.join(json.dumps(m) for m in messages) + '\n', encoding='utf-8')
    write_csv(OUT / 'app_events.csv', events)
    write_csv(OUT / 'ground_truth.csv', truth)
    unique = {e['event_id'] for e in events}
    save_json(OUT / 'manifest.json', {'synthetic': True, 'protocol': 'protocolo-dados.md v1 (25-byte uplink)',
              'municipality_code': args.municipality, 'crop': args.crop, 'sowing': args.sowing, 'devices': args.devices,
              'frames': len(frames), 'events': len(events), 'unique_event_ids': len(unique), 'duplicates': len(events) - len(unique),
              'calibration': {str(k): v for k, v in CALIBRATION.items()}, 'mm_per_pulse': MM_PER_PULSE,
              'warning': 'Synthetic data for ingestion and model-switch testing only; not a measurement'})
    print(json.dumps({'frames': len(frames), 'events': len(events), 'duplicates': len(events) - len(unique)}))


if __name__ == '__main__':
    main()
