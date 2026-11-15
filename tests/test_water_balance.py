import importlib.util
import unittest
from datetime import date,timedelta


class WaterBalanceTests(unittest.TestCase):
    def engine(self):
        self.assertIsNotNone(importlib.util.find_spec('data.models.water_balance'))
        from data.models import water_balance
        return water_balance

    def test_fao18_daily_et0(self):
        w=self.engine()
        result=w.et0_daily(date(2001,7,6),50.8,100,12.3,21.5,22.07,2.078,100.1,ea_kpa=1.409)
        self.assertAlmostEqual(result['et0_mm'],3.9,delta=.06)
        self.assertAlmostEqual(result['ra_mj_m2'],41.09,delta=.1)

    def test_mass_balance_overflow_and_dry_limit(self):
        w=self.engine()
        r=w.bucket_day(80,100,50,5,.5)
        self.assertAlmostEqual(r['drainage_mm'],30)
        self.assertAlmostEqual(r['storage_end_mm'],95)
        self.assertAlmostEqual(r['mass_residual_mm'],0)
        r=w.bucket_day(0,100,0,5,.5)
        self.assertEqual(r['eta_mm'],0)
        self.assertEqual(r['storage_end_mm'],0)

    def test_fao37_stress_example(self):
        w=self.engine()
        # FAO example 37: TAW 160 mm, initial depletion 55 mm, p .40.
        storage=105
        expected=[61,67,72.8,78.3,83.4]
        for depletion in expected:
            r=w.bucket_day(storage,160,0,6,.40)
            storage=r['storage_end_mm']
            self.assertAlmostEqual(160-storage,depletion,delta=.11)

    def test_invalid_and_missing_inputs_rejected(self):
        w=self.engine()
        with self.assertRaises(ValueError):w.bucket_day(50,100,-1,5,.5)
        with self.assertRaises(ValueError):w.bucket_day(101,100,1,5,.5)
        with self.assertRaises(ValueError):w.et0_daily(date(2024,1,1),-19,800,30,20,20,2,90,ea_kpa=2)
        with self.assertRaises(ValueError):w.bucket_day(50,100,None,5,.5)

    def test_phase_scenarios_and_conservation(self):
        w=self.engine()
        for crop,config in w.CROPS.items():
            weather=[{'day_utc':(date(2024,1,1)+timedelta(days=i)).isoformat(),'rain_mm':10. if i%3==0 else 0.,'et0_mm':5.} for i in range(sum(config['phase_days']))]
            base=w.simulate(weather,crop,55.,.7)
            for phase in w.PHASES:
                same=w.simulate(weather,crop,55.,.7,dry_phase=phase,rain_multiplier=1.)
                self.assertEqual(base,same)
                dry=w.simulate(weather,crop,55.,.7,dry_phase=phase,rain_multiplier=0.)
                for b,d in zip(base,dry):
                    self.assertAlmostEqual(d['mass_residual_mm'],0.)
                    self.assertLessEqual(d['storage_end_mm'],b['storage_end_mm']+1e-9)
                    self.assertEqual(d['rain_mm'],0. if d['phase']==phase else b['rain_mm'])
                self.assertGreaterEqual(sum(r['et_deficit_mm'] for r in dry),sum(r['et_deficit_mm'] for r in base))
            weather[1]['day_utc']=weather[0]['day_utc']
            with self.assertRaises(ValueError):w.simulate(weather,crop,55.,.7)

    def test_runoff_irrigation_balance(self):
        r=self.engine().bucket_day(20,100,20,5,.5,irrigation=10,runoff_fraction=.25)
        self.assertEqual(r['runoff_mm'],5)
        self.assertAlmostEqual(r['mass_residual_mm'],0)
        self.assertAlmostEqual(r['storage_end_mm'],40.5)


if __name__=='__main__':unittest.main()
