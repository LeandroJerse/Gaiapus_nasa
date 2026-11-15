"""Compare acquired snapshots and audit historical training eligibility honestly."""
import csv
import json
import math
from datetime import date,datetime,timedelta,timezone
from collections import defaultdict
from data.ingest.imerg_operational import RAW,OUT,ROOT,now,extract
from data.ingest.water_balance_run import sha,save_json,write_csv
from data.ingest.operational_availability import select_asof,training_gate


def validate_extracted_row(row,canonical):
    expected={key:'' if value is None else str(value) for key,value in canonical.items()}
    if row!=expected:raise ValueError('Extracted CSV disagrees with verified snapshot/receipt/catalog')


def availability_records(rows):
    result=defaultdict(list)
    for row in rows:
        if row['complete_48_half_hours']!='True' or not row['rain_mm']:continue
        result[row['run']].append({'key':f'{row["municipality_code"]}:{row["day_utc"]}:rain_mm','observation_end':row['observation_end'],
                                  'first_seen_at':row['first_seen_at'],'sha256':row['sha256'],'value':float(row['rain_mm'])})
    return result


def main():
    rows=[]
    for path in sorted((OUT/'extractions').glob('*.csv')):
        with path.open(encoding='utf-8',newline='') as stream:rows.extend(csv.DictReader(stream))
    with (OUT/'catalog.csv').open(encoding='utf-8',newline='') as stream:catalog=list(csv.DictReader(stream))
    by_filename={r['filename']:r for r in catalog};canonical={}
    # Reextract from content-verified payload and receipt, not from mutable CSV fields.
    for path,digest in {(r['snapshot_path'],r['sha256']) for r in rows}:
        source=ROOT/path
        if sha(source)!=digest:raise ValueError('Snapshot checksum mismatch')
        receipt=json.loads((source.parent/'receipt.json').read_text(encoding='utf-8'))
        if receipt['sha256']!=digest or receipt['path']!=path:raise ValueError('Receipt mismatch')
        for expected in extract(source,by_filename[source.name],receipt):canonical[(path,expected['municipality_code'])]=expected
    for row in rows:validate_extracted_row(row,canonical[(row['snapshot_path'],row['municipality_code'])])
    snapshots={}
    for row in rows:
        key=(row['municipality_code'],row['day_utc'],row['run'])
        if key not in snapshots or row['first_seen_at']>snapshots[key]['first_seen_at']:snapshots[key]=row
    groups=defaultdict(dict)
    for (code,day,run),row in snapshots.items():groups[(code,day)][run]=row
    pairs=[];availability=[]
    for (code,day),runs in groups.items():
        for run,row in runs.items():
            availability.append({'municipality_code':code,'day_utc':day,'run':run,'production_time':row['production_time'],
                                 'cmr_updated_at':row['catalog_updated_at'],'first_seen_at':row['first_seen_at'],
                                 'provider_first_published_at':None,'nominal_half_hour_latency_hours':4 if run=='Early' else (14 if run=='Late' else None),
                                 'complete_48_half_hours':row['complete_48_half_hours'],'sha256':row['sha256'],
                                 'historical_asof_2021_eligible':False})
        for run in ('Early','Late'):
            candidate=runs.get(run);final=runs.get('Final');valid=False
            if candidate and final:
                if abs(float(candidate['cell_lon'])-float(final['cell_lon']))>1e-5 or abs(float(candidate['cell_lat'])-float(final['cell_lat']))>1e-5:raise ValueError('Cell mismatch')
                valid=all(r['complete_48_half_hours']=='True' and r['rain_mm']!='' for r in (candidate,final))
            early=float(candidate['rain_mm']) if valid else None;reference=float(final['rain_mm']) if valid else None
            pairs.append({'municipality_code':code,'state_code':code[:2],'day_utc':day,'run':run,'matched_complete':valid,
                          'rain_mm':early,'final_mm':reference,'difference_mm':early-reference if valid else None,
                          'absolute_difference_mm':abs(early-reference) if valid else None,
                          'status':'paired_same_cell_UTC_not_field_validation' if valid else 'missing_or_incomplete_product'})
    if pairs:write_csv(OUT/'rain_comparison_daily.csv',pairs)
    if availability:write_csv(OUT/'availability_ledger.csv',availability)
    metrics=[]
    for run in ('Early','Late'):
        for state in ('31','51','all'):
            sample=[r for r in pairs if r['run']==run and r['matched_complete'] and (state=='all' or r['state_code']==state)]
            delta=[r['difference_mm'] for r in sample]
            metrics.append({'run':run,'state_code':state,'pairs':len(delta),'distinct_days':len({r['day_utc'] for r in sample}),
                            'mae_mm':sum(abs(x) for x in delta)/len(delta) if delta else None,
                            'rmse_mm':math.sqrt(sum(x*x for x in delta)/len(delta)) if delta else None,
                            'bias_mm':sum(delta)/len(delta) if delta else None,'scope':'pilot_not_climatological_skill'})
    write_csv(OUT/'rain_comparison_summary.csv',metrics)
    monthly=defaultdict(list)
    for row in pairs:
        if row['matched_complete']:
            monthly[(row['municipality_code'],row['day_utc'][:7],row['run'])].append(row)
    if monthly:
        write_csv(OUT/'rain_comparison_monthly.csv',[
            {'municipality_code':code,'month':month,'run':run,'paired_days':len(group),
             'run_mm_on_paired_days':sum(row['rain_mm'] for row in group),
             'final_mm_on_paired_days':sum(row['final_mm'] for row in group),
             'bias_mm_per_paired_day':sum(row['difference_mm'] for row in group)/len(group),
             'mae_mm_per_paired_day':sum(row['absolute_difference_mm'] for row in group)/len(group)}
            for (code,month,run),group in sorted(monthly.items())
        ])
    # This is a release-time gate, not a new yield model. Unknown label vintages fail closed.
    audit=[];records_by_run=availability_records(rows)
    with (ROOT/'data/processed/training_v3/research_samples.csv').open(encoding='utf-8',newline='') as stream:
        for sample in csv.DictReader(stream):
            if sample['split']!='train':continue
            start=date.fromisoformat(sample['sowing_date']);horizon=int(sample['horizon_days'])
            decision=datetime.combine(start+timedelta(days=horizon),datetime.min.time(),tzinfo=timezone.utc).isoformat()
            fields=('rain_mm','t_min_c','t_max_c','radiation_mj_m2','rh2m_pct','ws2m_m_s','dewpoint_c','pressure_kpa')
            required=[f'{sample["municipality_code"]}:{start+timedelta(days=i)}:{field}' for i in range(horizon) for field in fields]
            for run in ('Early','Late'):
                gate=training_gate(records_by_run[run],required,decision,None,'2018-01-01T00:00:00Z')
                audit.append({'sample_id':sample['sample_id'],'horizon_days':horizon,'run':run,'decision_at':decision,'eligible':gate['eligible'],
                              'missing_feature_keys':len(gate['missing_keys']),'reasons':'|'.join(gate['reasons'])})
    write_csv(OUT/'operational_training_gate.csv',audit)
    counts={run:sum(r['run']==run for r in catalog) for run in ('Early','Late','Final')}
    status={'updated_at':now(),'catalog_granules_by_run':counts,'downloaded_distinct_granules':len({r['sha256'] for r in rows}),
            'extracted_cells':len(snapshots),'rain_pairs':sum(r['matched_complete'] for r in pairs),'distinct_compared_days':len({r['day_utc'] for r in pairs if r['matched_complete']}),
            'training_candidates_checked':len(audit),'eligible_training_candidates':sum(r['eligible'] for r in audit),'model_fit_performed':False,
            'production_approved':False,'blockers':['Incomplete Early/Late time series','Historical product first-publication/vintages unverified','ET0 meteorology release vintages missing','Yield labels release vintages missing'],
            'test_yield_2022_2024_evaluated':False}
    save_json(OUT/'readiness.json',status);print(json.dumps(status,indent=2))


if __name__=='__main__':main()
