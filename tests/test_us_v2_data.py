import gzip
import io
import json

import pytest

from data.ingest import us_data as v1
from data.ingest import us_v2_data as d

HEADER = ['SOURCE_DESC', 'SECTOR_DESC', 'GROUP_DESC', 'COMMODITY_DESC', 'CLASS_DESC', 'PRODN_PRACTICE_DESC', 'UTIL_PRACTICE_DESC',
          'STATISTICCAT_DESC', 'UNIT_DESC', 'SHORT_DESC', 'DOMAIN_DESC', 'DOMAINCAT_DESC', 'AGG_LEVEL_DESC', 'STATE_ANSI', 'STATE_ALPHA',
          'ASD_CODE', 'ASD_DESC', 'COUNTY_ANSI', 'COUNTY_CODE', 'COUNTY_NAME', 'YEAR', 'FREQ_DESC', 'BEGIN_CODE', 'END_CODE',
          'REFERENCE_PERIOD_DESC', 'WEEK_ENDING', 'LOAD_TIME', 'VALUE', 'CV_%']


def row(**kw):
    base = {'SOURCE_DESC': 'SURVEY', 'SECTOR_DESC': 'CROPS', 'GROUP_DESC': 'FIELD CROPS', 'COMMODITY_DESC': 'CORN', 'CLASS_DESC': 'ALL CLASSES',
            'PRODN_PRACTICE_DESC': 'ALL PRODUCTION PRACTICES', 'UTIL_PRACTICE_DESC': 'GRAIN', 'STATISTICCAT_DESC': 'YIELD', 'UNIT_DESC': 'BU / ACRE',
            'SHORT_DESC': 'x', 'DOMAIN_DESC': 'TOTAL', 'DOMAINCAT_DESC': 'NOT SPECIFIED', 'AGG_LEVEL_DESC': 'COUNTY', 'STATE_ANSI': '19',
            'STATE_ALPHA': 'IA', 'ASD_CODE': '50', 'ASD_DESC': 'CENTRAL', 'COUNTY_ANSI': '169', 'COUNTY_CODE': '169', 'COUNTY_NAME': 'STORY',
            'YEAR': '2010', 'FREQ_DESC': 'ANNUAL', 'BEGIN_CODE': '00', 'END_CODE': '00', 'REFERENCE_PERIOD_DESC': 'YEAR', 'WEEK_ENDING': '',
            'LOAD_TIME': '2012-01-01 00:00:00', 'VALUE': '170.5', 'CV_%': ''}
    base.update(kw)
    return base


def bulk(rows):
    text = '\t'.join(HEADER) + '\n' + ''.join('\t'.join(r[h] for h in HEADER) + '\n' for r in rows)
    return io.BytesIO(gzip.compress(text.encode('utf-8')))


@pytest.mark.parametrize('kw', [{}, {'COMMODITY_DESC': 'SOYBEANS', 'UTIL_PRACTICE_DESC': 'ALL UTILIZATION PRACTICES', 'VALUE': '55'},
                                {'COMMODITY_DESC': 'WHEAT', 'CLASS_DESC': 'WINTER', 'UTIL_PRACTICE_DESC': 'ALL UTILIZATION PRACTICES',
                                 'STATE_ALPHA': 'KS', 'STATE_ANSI': '20', 'COUNTY_ANSI': '191', 'VALUE': '45.2'}])
def test_conversion_equals_v1(kw):
    r = {k.lower(): v for k, v in row(**kw).items()}
    a = v1.normalize_record(r)
    b = d.normalize_county(r)
    assert a['fips'] == b['fips'] and a['crop'] == b['crop']
    assert b['yield_kg_ha'] == pytest.approx(a['yield_kg_ha'], rel=0, abs=1e-9)


def test_practice_and_area_records_kept():
    r = {k.lower(): v for k, v in row(STATE_ALPHA='NE', STATE_ANSI='31', COUNTY_ANSI='185', PRODN_PRACTICE_DESC='IRRIGATED',
                                      STATISTICCAT_DESC='AREA HARVESTED', UNIT_DESC='ACRES', VALUE='12,300').items()}
    out = d.normalize_county(r)
    assert out['practice'] == 'IRRIGATED' and out['statistic'] == 'AREA HARVESTED' and out['value'] == 12300.0
    assert out['yield_kg_ha'] is None


def test_suppressed_is_missing_not_zero():
    out = d.normalize_county({k.lower(): v for k, v in row(VALUE='(D)').items()})
    assert out['value'] is None and out['yield_kg_ha'] is None and out['value_raw'] == '(D)'


@pytest.mark.parametrize('year,part', [(1990, 'development'), (2021, 'development'), (2024, 'development'),
                                       (2025, 'sealed_confirmation_2025'), (2026, 'sealed_future'), (1985, None)])
def test_route(year, part):
    assert d.route(year) == part


def test_extract_never_prints_sealed_values(tmp_path, capsys):
    rows = [row(), row(YEAR='2025', VALUE='187.25'), row(YEAR='2026', VALUE='191.75'),
            row(AGG_LEVEL_DESC='STATE', COUNTY_ANSI='', COUNTY_CODE='', COMMODITY_DESC='WHEAT', CLASS_DESC='WINTER',
                UTIL_PRACTICE_DESC='ALL UTILIZATION PRACTICES', STATE_ALPHA='KS', STATE_ANSI='20', STATISTICCAT_DESC='PROGRESS',
                UNIT_DESC='PCT PLANTED', FREQ_DESC='WEEKLY', REFERENCE_PERIOD_DESC='WEEK #40', WEEK_ENDING='2025-10-05', YEAR='2025', VALUE='33')]
    manifest = d.extract(bulk(rows), tmp_path, source='test')
    out = capsys.readouterr()
    text = out.out + out.err + json.dumps(manifest)
    assert '187.25' not in text and '191.75' not in text and '19169' not in text
    assert manifest['counts'] == {'development': 1, 'sealed_confirmation_2025': 1, 'sealed_future': 1, 'progress': 1}
    progress = [json.loads(x) for x in gzip.open(tmp_path / 'progress.jsonl.gz', 'rt')]
    assert progress[0]['unit'] == 'PCT PLANTED' and progress[0]['week_ending'] == '2025-10-05'


def test_load_development_rejects_confirmation_years(tmp_path):
    with gzip.open(tmp_path / 'development.jsonl.gz', 'wt') as f:
        f.write(json.dumps({'fips': '19169', 'crop': 'maize', 'year': 2025, 'statistic': 'YIELD', 'practice': 'ALL PRODUCTION PRACTICES', 'yield_kg_ha': 1.0}) + '\n')
    with pytest.raises(ValueError):
        d.load_yields(tmp_path)


def test_sealed_only_referenced_by_allowed_modules():
    import pathlib
    root = pathlib.Path(d.__file__).resolve().parents[2]
    allowed = {'us_v2_data.py', 'us_v2_confirm.py', 'test_us_v2_data.py', 'test_us_v2_confirm.py'}
    offenders = [p.name for p in list((root / 'data/ingest').glob('us_v2_*.py')) + list((root / 'scripts').glob('us_v2_*.py'))
                 if 'sealed_confirmation' in p.read_text(encoding='utf-8') and p.name not in allowed]
    assert offenders == []
