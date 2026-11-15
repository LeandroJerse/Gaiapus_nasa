import unittest
from datetime import datetime,timezone


class AvailabilityTests(unittest.TestCase):
    def test_extracted_csv_cannot_override_receipt_or_rain(self):
        from data.ingest.report_imerg_operational import validate_extracted_row
        canonical={'rain_mm':2.,'first_seen_at':'2026-01-01T00:00:00Z','complete_48_half_hours':True}
        row={k:str(v) for k,v in canonical.items()}
        validate_extracted_row(row,canonical)
        for key,value in [('rain_mm','99.0'),('first_seen_at','2020-01-01T00:00:00Z')]:
            tampered={**row,key:value}
            with self.assertRaises(ValueError):validate_extracted_row(tampered,canonical)

    def test_all_revisions_reach_asof_selection(self):
        from data.ingest.report_imerg_operational import availability_records
        from data.ingest.operational_availability import select_asof
        a={'run':'Early','municipality_code':'3170206','day_utc':'2021-01-01','complete_48_half_hours':'True','rain_mm':'2.0','observation_end':'2021-01-02T00:00:00Z','first_seen_at':'2021-01-02T05:00:00Z','sha256':'a'*64}
        b={**a,'first_seen_at':'2024-01-01T00:00:00Z','rain_mm':'99.0','sha256':'b'*64}
        history=availability_records([a,b])['Early']
        self.assertEqual(len(history),2)
        self.assertEqual(select_asof(history,'2021-01-03T00:00:00Z')['3170206:2021-01-01:rain_mm']['value'],2.)

    def test_old_observation_is_not_old_availability(self):
        from data.ingest.operational_availability import select_asof
        row={'key':'rain:cell:2021-01-01','observation_end':'2021-01-02T00:00:00Z','first_seen_at':'2026-11-14T12:00:00Z','sha256':'a'*64,'value':10.}
        self.assertEqual(select_asof([row],'2021-01-03T00:00:00Z'),{})
        self.assertEqual(select_asof([row],'2026-11-15T00:00:00Z')[row['key']]['value'],10.)

    def test_future_revision_does_not_replace_available_version(self):
        from data.ingest.operational_availability import select_asof
        a={'key':'rain:cell:2026-01-01','observation_end':'2026-01-02T00:00:00Z','first_seen_at':'2026-01-02T15:00:00Z','sha256':'a'*64,'value':10.}
        b={**a,'first_seen_at':'2026-02-01T00:00:00Z','sha256':'b'*64,'value':99.}
        self.assertEqual(select_asof([a,b],'2026-01-03T00:00:00Z')[a['key']]['value'],10.)

    def test_nominal_latency_and_catalog_update_are_not_evidence(self):
        from data.ingest.operational_availability import select_asof
        row={'key':'x','observation_end':'2021-01-02T00:00:00Z','cmr_updated_at':'2021-01-03T00:00:00Z','nominal_latency_hours':4,'sha256':'a'*64,'value':1.}
        self.assertEqual(select_asof([row],'2021-01-03T00:00:00Z'),{})

    def test_utc_required_and_missing_feature_blocks_training(self):
        from data.ingest.operational_availability import utc,training_gate
        with self.assertRaises(ValueError):utc('2021-01-01')
        self.assertEqual(training_gate([],['rain','et0'],'2021-01-03T00:00:00Z',None,'2020-01-01T00:00:00Z')['eligible'],False)

    def test_training_cannot_use_feature_decision_after_fit(self):
        from data.ingest.operational_availability import training_gate
        row={'key':'rain','observation_end':'2021-01-02T00:00:00Z','first_seen_at':'2021-01-02T10:00:00Z','sha256':'a'*64,'value':1.}
        self.assertFalse(training_gate([row],['rain'],'2021-01-03T00:00:00Z','2020-01-01T00:00:00Z','2021-01-01T00:00:00Z')['eligible'])

    def test_hydro_builder_requires_all_dependencies_asof(self):
        from data.ingest.operational_availability import build_asof_hydro_features
        from datetime import date,timedelta
        sowing=date(2020,11,5);records=[]
        values={'rain_mm':4.,'t_min_c':20.,'t_max_c':30.,'radiation_mj_m2':20.,'rh2m_pct':70.,'ws2m_m_s':2.,'dewpoint_c':18.,'pressure_kpa':95.}
        for i in range(60):
            day=sowing+timedelta(days=i);end=(day+timedelta(days=1)).isoformat()+'T00:00:00Z'
            for field,value in values.items():records.append({'key':f'3170206:{day}:{field}','observation_end':end,'first_seen_at':end,'sha256':'a'*64,'value':value})
        result=build_asof_hydro_features(records,'3170206','soy',sowing,60,'2021-01-04T18:00:00Z',-19.,800.)
        self.assertEqual(result['rain_sum_mm'],240.)
        records[-1]['first_seen_at']='2024-01-01T00:00:00Z'
        with self.assertRaises(ValueError):build_asof_hydro_features(records,'3170206','soy',sowing,60,'2021-01-04T18:00:00Z',-19.,800.)
