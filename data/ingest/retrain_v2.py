"""Paired v1/v2 spatial-support comparison plus trend ablation; never evaluate yield test."""
import json
from pathlib import Path
from data.ingest.v2_common import OUT,sha,save_json
from data.ingest.training_baseline import FEATURES,training_mask


def main():
    import joblib
    import numpy as np
    import pandas as pd
    import sklearn
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import Ridge
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error,mean_squared_error
    files={'point_v1':Path('data/processed/training_v1/research_samples.csv'),'weighted_v2':OUT/'research_samples.csv'}
    frames={k:pd.read_csv(p).set_index('sample_id',drop=False).sort_index() for k,p in files.items()}
    a,b=frames.values()
    assert a.index.equals(b.index)
    for key in ('yield_kg_ha','feature_cutoff','split_time','spatial_holdout'):
        assert a[key].equals(b[key]),f'Paired cohort changed: {key}'
    models=Path('data/models/research_v2');models.mkdir(parents=True,exist_ok=True)
    reports=[];predictions=[];group_reports=[];feature_contracts={}
    for crop in ('soy','maize2'):
        for variant,frame in frames.items():
            cohort=frame[frame.crop==crop];train=cohort[cohort.apply(training_mask,axis=1)]
            definitions={'ridge':(FEATURES,make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=10))),
                         'hist_gradient_boosting':(FEATURES,make_pipeline(SimpleImputer(strategy='median'),HistGradientBoostingRegressor(max_iter=100,max_leaf_nodes=7,min_samples_leaf=15,l2_regularization=10,random_state=42)))}
            if variant=='point_v1':
                definitions['trend_history_only']=(['harvest_year','historical_yield_lag2to4_mean'],make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),Ridge(alpha=10)))
            for name,(features,model) in definitions.items():
                model.fit(train[features],train.yield_kg_ha)
                artifact=f'{crop}_{variant}_{name}.joblib'
                joblib.dump(model,models/artifact)
                feature_contracts[artifact]=features
                for scope,ev in [('temporal_validation',cohort[(cohort.split_time=='validation') & ~cohort.spatial_holdout]),
                                 ('spatiotemporal_validation',cohort[(cohort.split_time=='validation') & cohort.spatial_holdout])]:
                    predicted=model.predict(ev[features]);truth=ev.yield_kg_ha.to_numpy()
                    reports.append({'crop':crop,'variant':variant,'model':name,'scope':scope,'train_n':len(train),'evaluation_n':len(ev),
                                    'mae_kg_ha':float(mean_absolute_error(truth,predicted)),
                                    'rmse_kg_ha':float(np.sqrt(mean_squared_error(truth,predicted))),
                                    'bias_kg_ha':float(np.mean(predicted-truth))})
                    for state in sorted(ev.state_code.unique()):
                        mask=(ev.state_code==state).to_numpy()
                        group_reports.append({'crop':crop,'variant':variant,'model':name,'scope':scope,'state_code':int(state),
                                              'n':int(mask.sum()),'mae_kg_ha':float(mean_absolute_error(truth[mask],predicted[mask]))})
                    for (_,row),value in zip(ev.iterrows(),predicted):
                        predictions.append({'sample_id':row.sample_id,'crop':crop,'variant':variant,'model':name,'scope':scope,
                                            'actual_kg_ha':row.yield_kg_ha,'predicted_kg_ha':float(value)})
    report=pd.DataFrame(reports);report.to_csv(OUT/'model_comparison.csv',index=False)
    pd.DataFrame(group_reports).to_csv(OUT/'model_comparison_by_state.csv',index=False)
    pd.DataFrame(predictions).to_csv(OUT/'validation_predictions.csv',index=False)
    save_json(models/'manifest.json',{'datasets':{k:sha(p) for k,p in files.items()},'features':FEATURES,
             'paired_samples':len(a),'test_evaluated':False,'test_years':[2022,2023,2024],
             'training_end':2018,'validation_years':[2019,2020,2021],'spatial_holdout':['3170107','5107909'],
             'versions':{'sklearn':sklearn.__version__,'numpy':np.__version__,'pandas':pd.__version__},
             'models':{p.name:sha(p) for p in models.glob('*.joblib')},'features_by_artifact':feature_contracts,'production_approved':False,
             'rain_gauge_bias_correction':False,'mask_species_specific':False})
    print(report.to_string(index=False))


if __name__=='__main__':main()
