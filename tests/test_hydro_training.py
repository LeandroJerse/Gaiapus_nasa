import unittest
from datetime import date,timedelta


class HydroTrainingTests(unittest.TestCase):
    def test_future_perturbation_and_truncation_invariant(self):
        from data.ingest.hydro_training_features import features_at_cutoff
        start=date(2020,11,5)
        weather=[{'day_utc':(start+timedelta(days=i)).isoformat(),'rain_mm':4.,'et0_mm':5.,'t_min_c':20.,'t_max_c':30.,'radiation_mj_m2':20.,'rh2m_pct':70.,'ws2m_m_s':2.} for i in range(115)]
        a=features_at_cutoff(weather,'soy',start,60)
        self.assertEqual(a,features_at_cutoff(weather[:60],'soy',start,60))
        for row in weather[60:]:row.update(rain_mm=9999.,et0_mm=9999.)
        self.assertEqual(a,features_at_cutoff(weather,'soy',start,60))
        self.assertIsNone(a['hydro_maturation_deficit_mm'])
        self.assertEqual(a['hydro_reproductive_observed_days'],10)

    def test_missing_observed_day_rejected(self):
        from data.ingest.hydro_training_features import features_at_cutoff
        with self.assertRaises(ValueError):features_at_cutoff([],'soy',date(2020,11,5),60)

    def test_training_excludes_embargo_validation_test_and_holdout(self):
        from data.ingest.train_hydro_v3 import training_eligible
        self.assertTrue(training_eligible(2016,'3170206'))
        for year in range(2017,2025):self.assertFalse(training_eligible(year,'3170206'))
        for code in ('3170107','5107909'):self.assertFalse(training_eligible(2010,code))

    def test_prefix_matches_full_physics_without_future_access(self):
        from data.ingest.hydro_training_features import features_at_cutoff
        from data.models.water_balance import CROPS,simulate
        for crop in ('soy','maize2'):
            start=date(2020,1,5)
            weather=[{'day_utc':(start+timedelta(days=i)).isoformat(),'rain_mm':20. if i%7==0 else 0.,'et0_mm':5.,'t_min_c':20.,'t_max_c':30.,'radiation_mj_m2':20.,'rh2m_pct':70.,'ws2m_m_s':2.} for i in range(sum(CROPS[crop]['phase_days']))]
            full=simulate(weather,crop,CROPS[crop]['cad_mm']['AD4'],.7)
            for horizon in (60,90):
                features=features_at_cutoff(weather[:horizon],crop,start,horizon)
                self.assertAlmostEqual(features['hydro_storage_end_mm'],full[horizon-1]['storage_end_mm'])
                deficit=sum(value for key,value in features.items() if key.endswith('_deficit_mm') and value is not None)
                self.assertAlmostEqual(deficit,sum(r['et_deficit_mm'] for r in full[:horizon]))
                with self.assertRaises(ValueError):features_at_cutoff(weather[:horizon],crop,start,horizon,operational=True)

    def test_operational_gate_rejects_unversioned_final(self):
        from data.ingest.hydro_training_features import require_asof_availability
        with self.assertRaises(ValueError):require_asof_availability([{'day_utc':'2020-01-01'}],date(2020,1,2))
        with self.assertRaises(ValueError):require_asof_availability([{'day_utc':'2020-01-01','available_at':'2020-05-01'}],date(2020,1,2))
        require_asof_availability([{'day_utc':'2020-01-01','available_at':'2020-01-02'}],date(2020,1,2))


if __name__=='__main__':unittest.main()
