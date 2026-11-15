"""Fixed-horizon hydrology ablation, preserving the sealed 2022-2024 yield test."""
import csv
import json
from datetime import date,timedelta
from pathlib import Path
from data.ingest.water_balance_run import ROOT,sha,save_json,write_csv
from data.ingest.hydro_training_features import WEATHER_FEATURES,HYDRO_FEATURES,features_at_cutoff

OUT=ROOT/'data/processed/training_v3'
MODELS=ROOT/'data/models/research_v3'
HOLDOUT={'3170107','5107909'}


def training_eligible(year,code):
    return 2002<=int(year)<=2016 and str(code) not in HOLDOUT


def verified_inputs():
    selected={}
    for version,names in {
        'training_v1':['data/processed/training_v1/research_samples.csv','data/processed/training_v1/weather_daily.csv'],
        'training_v2':['data/processed/training_v2/model_comparison.csv'],
        'water_balance_v1':['data/processed/water_balance_v1/et0_daily.csv']}.items():
        frozen=json.loads((ROOT/f'data/processed/{version}/delivery_manifest.json').read_text(encoding='utf-8'))['files']
        for name in names:
            digest=sha(ROOT/name)
            if frozen[name]['sha256']!=digest:raise ValueError(f'Input integrity failure: {name}')
            selected[name]=digest
    return selected


def build_samples():
    weather={}
    numeric=('rain_mm','t_min_c','t_max_c','radiation_mj_m2','rh2m_pct','ws2m_m_s')
    with (ROOT/'data/processed/training_v1/weather_daily.csv').open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            code=row['municipality_code'];day=row['day_utc']
            weather.setdefault(code,{})[day]={'day_utc':day,**{key:float(row[key]) for key in numeric}}
    with (ROOT/'data/processed/water_balance_v1/et0_daily.csv').open(encoding='utf-8',newline='') as stream:
        for row in csv.DictReader(stream):weather[row['municipality_code']][row['day_utc']]['et0_mm']=float(row['et0_mm'])
    results=[];audit=[]
    with (ROOT/'data/processed/training_v1/research_samples.csv').open(encoding='utf-8-sig',newline='') as stream:
        for source in csv.DictReader(stream):
            year=int(source['harvest_year'])
            # Do not create features or copy labels for the sealed test or embargo years.
            if year>2021 or year in (2017,2018):continue
            code=source['municipality_code'];crop=source['crop']
            sowing=date(year-1,11 if code.startswith('31') else 10,5) if crop=='soy' else date(year,1,5)
            for horizon in (60,90):
                cutoff=sowing+timedelta(days=horizon-1)
                prefix=[weather[code][(sowing+timedelta(days=i)).isoformat()] for i in range(horizon)]
                values=features_at_cutoff(prefix,crop,sowing,horizon)
                results.append({'sample_id':source['sample_id'],'municipality_code':code,'state_code':code[:2],'crop':crop,
                                'harvest_year':year,'horizon_days':horizon,'sowing_date':sowing.isoformat(),'feature_cutoff':cutoff.isoformat(),
                                'split':'train' if training_eligible(year,code) else ('validation' if year>=2019 else 'unused_spatial_history'),
                                'spatial_holdout':code in HOLDOUT,'yield_kg_ha':float(source['yield_kg_ha']),**values})
                audit.append({'sample_id':source['sample_id'],'horizon_days':horizon,'max_observation_date':prefix[-1]['day_utc'],'cutoff':cutoff.isoformat(),
                              'post_cutoff_rows_used':0,'observed_days':len(prefix),'publication_vintage_verified':False,'operational_eligible':False})
    return results,audit


def main():
    import joblib
    import numpy as np
    import pandas as pd
    import sklearn
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error,mean_squared_error
    OUT.mkdir(parents=True,exist_ok=True);MODELS.mkdir(parents=True,exist_ok=True)
    inputs=verified_inputs();rows,audit=build_samples();write_csv(OUT/'research_samples.csv',rows);write_csv(OUT/'cutoff_audit.csv',audit)
    frame=pd.DataFrame(rows);reports=[];predictions=[];state_reports=[];contracts={}
    for crop in ('soy','maize2'):
        for horizon in (60,90):
            cohort=frame[(frame.crop==crop)&(frame.horizon_days==horizon)];train=cohort[cohort.split=='train']
            if train.harvest_year.max()>2016 or train.spatial_holdout.any():raise ValueError('Training isolation failed')
            groups={'trend':['harvest_year'],'weather':['harvest_year',*WEATHER_FEATURES],
                    'hydro':['harvest_year',*HYDRO_FEATURES],'weather_hydro':['harvest_year',*WEATHER_FEATURES,*HYDRO_FEATURES]}
            for variant,candidates in groups.items():
                # Drop all-missing/constant features using training rows only, not validation.
                features=[f for f in candidates if train[f].nunique(dropna=True)>1]
                if not features:raise ValueError('Empty feature contract')
                for algorithm in (('ridge',) if variant=='trend' else ('ridge','hgb')):
                    regressor=Ridge(alpha=10) if algorithm=='ridge' else HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=7,min_samples_leaf=15,l2_regularization=10,random_state=42,early_stopping=False)
                    model=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),regressor)
                    model.fit(train[features],train.yield_kg_ha)
                    artifact=f'{crop}_d{horizon}_{variant}_{algorithm}.joblib';joblib.dump(model,MODELS/artifact)
                    contracts[artifact]={'features':features,'dropped_train_constant_or_missing':[f for f in candidates if f not in features],'training_samples':list(train.sample_id),'training_end':2016,'operational_approved':False}
                    for scope,holdout in [('temporal_validation',False),('spatiotemporal_validation',True)]:
                        ev=cohort[(cohort.split=='validation')&(cohort.spatial_holdout==holdout)]
                        if ev.harvest_year.min()!=2019 or ev.harvest_year.max()!=2021:raise ValueError('Validation years changed')
                        truth=ev.yield_kg_ha.to_numpy();pred=model.predict(ev[features])
                        meta={'crop':crop,'horizon_days':horizon,'variant':variant,'model':algorithm,'scope':scope}
                        reports.append({**meta,'train_n':len(train),'evaluation_n':len(ev),'mae_kg_ha':float(mean_absolute_error(truth,pred)),
                                        'rmse_kg_ha':float(np.sqrt(mean_squared_error(truth,pred))),'bias_kg_ha':float(np.mean(pred-truth))})
                        for state in ('31','51'):
                            mask=(ev.state_code==state).to_numpy()
                            state_reports.append({**meta,'state_code':state,'n':int(mask.sum()),'mae_kg_ha':float(mean_absolute_error(truth[mask],pred[mask]))})
                        for (_,row),value in zip(ev.iterrows(),pred):
                            predictions.append({**meta,'sample_id':row.sample_id,'state_code':row.state_code,'feature_cutoff':row.feature_cutoff,'actual_kg_ha':row.yield_kg_ha,'predicted_kg_ha':float(value)})
    write_csv(OUT/'model_comparison.csv',reports);write_csv(OUT/'model_comparison_by_state.csv',state_reports);write_csv(OUT/'validation_predictions.csv',predictions)
    lookup={(r['crop'],r['horizon_days'],r['model'],r['scope'],r['variant']):r for r in reports}
    deltas=[]
    for r in reports:
        if r['variant']!='weather_hydro':continue
        base=lookup[(r['crop'],r['horizon_days'],r['model'],r['scope'],'weather')]
        delta=r['mae_kg_ha']-base['mae_kg_ha']
        deltas.append({k:r[k] for k in ('crop','horizon_days','model','scope')}|{'weather_mae_kg_ha':base['mae_kg_ha'],'weather_hydro_mae_kg_ha':r['mae_kg_ha'],'delta_mae_kg_ha':delta,'mae_reduction_pct':-100*delta/base['mae_kg_ha']})
    write_csv(OUT/'paired_hydro_effect.csv',deltas)
    with (ROOT/'data/processed/training_v2/model_comparison.csv').open(encoding='utf-8-sig') as stream:
        refs=[{**r,'comparison_status':'context_only_different_cutoffs_features_training_years'} for r in csv.DictReader(stream)]
    write_csv(OUT/'previous_models_reference.csv',refs)
    save_json(MODELS/'manifest.json',{'version':'research_v3_hydro','inputs':inputs,'features_by_artifact':contracts,
        'versions':{'sklearn':sklearn.__version__,'numpy':np.__version__,'pandas':pd.__version__},'models':{p.name:sha(p) for p in MODELS.glob('*.joblib')},
        'training_years':[2002,2016],'embargo_years':[2017,2018],'validation_years':[2019,2020,2021],'test_years':[2022,2023,2024],
        'test_evaluated':False,'spatial_holdout':sorted(HOLDOUT),'operational_approved':False,'publication_time_leakage_resolved':False,
        'event_time_future_leakage_checks_passed':True,'hyperparameter_search':False,'synthetic_drought_as_training_labels':False,
        'limitations':['Reprocessed POWER/IMERG Final not historical release vintages','Fixed AD4/70% water and hypothetical sowing; not field calibrated','Municipal yield not field yield','No observed dates of label publication; two-year embargo is not vintage reconstruction','Small validation sample; previous-model scores not a causal feature comparison']})
    save_json(OUT/'validation.json',{'rows':len(rows),'models':len(contracts),'metric_rows':len(reports),'prediction_rows':len(predictions),
        'test_rows':sum(r['harvest_year']>=2022 for r in rows),'future_event_rows_used':sum(r['post_cutoff_rows_used'] for r in audit),
        'feature_selection':'training_only','production_approved':False,'publication_vintages_verified':False})
    print(pd.DataFrame(deltas).to_string(index=False))


if __name__=='__main__':main()
