"""Crop registry: labels, calendar, harvest-year convention and water parameters per crop.

One entry drives collection, cleaning and training for that crop. Sowing dates are fixed
hypothetical calendars per state, not observed dates. Water parameters for the new crops
are FAO-56 tabulated values (Allen et al. 1998, tables 11, 12 and 22), not local
calibrations; paddy rice has none because a free-drainage bucket does not represent a
flooded field, so it uses weather features only.
"""
from data.models.water_balance import CROPS as CALIBRATED

AD = {'AD2': 32.0, 'AD4': 55.0, 'AD6': 95.0}  # same soil-water classes as soy; hypothesis for the new crops

FAO56 = {
    'maize1': {'phase_days': (30, 40, 50, 30), 'kc_initial': .3, 'kc_mid': 1.2, 'kc_end': .35, 'p_base': .55, 'cad_mm': AD},
    'wheat': {'phase_days': (20, 25, 60, 30), 'kc_initial': .3, 'kc_mid': 1.15, 'kc_end': .3, 'p_base': .55, 'cad_mm': AD},
    'bean': {'phase_days': (20, 30, 40, 20), 'kc_initial': .4, 'kc_mid': 1.15, 'kc_end': .35, 'p_base': .45, 'cad_mm': AD},
}

# sowing: {state: (month, day)}; year_offset: sowing year relative to the PAM harvest year.
REGISTRY = {
    'soy': {'name': 'Soja', 'table': 5457, 'classification': 'c782', 'category': '40124', 'first_year': 2001,
            'season': 'summer', 'year_offset': -1, 'water': CALIBRATED['soy'], 'water_source': 'v6 calibrated set',
            'sowing': {'51': (10, 5), '11': (10, 5), '50': (10, 15), '41': (10, 15), '35': (10, 15), '52': (10, 25),
                       '53': (10, 25), '31': (11, 5), '42': (11, 10), '43': (11, 10), '29': (11, 20), '17': (11, 20),
                       '21': (11, 20), '22': (11, 20), '15': (11, 20)},
            'min_train_years': 10, 'min_mean_area_ha': 5000, 'system': 'rainfed_dominant'},
    'maize2': {'name': 'Milho 2ª safra', 'table': 839, 'classification': 'c81', 'category': '114254', 'first_year': 2003,
               'season': 'second', 'year_offset': 0, 'water': CALIBRATED['maize2'], 'water_source': 'v6 calibrated set',
               'sowing': {'51': (1, 5), '31': (1, 5), '11': (1, 20), '52': (2, 1), '53': (2, 1), '17': (2, 1), '21': (2, 1),
                          '22': (2, 1), '29': (2, 1), '15': (2, 1), '50': (2, 15), '41': (2, 15), '35': (2, 15),
                          '42': (2, 15), '43': (2, 15)},
               'min_train_years': 8, 'min_mean_area_ha': 2000, 'system': 'rainfed_dominant'},
    'maize1': {'name': 'Milho 1ª safra', 'table': 839, 'classification': 'c81', 'category': '114253', 'first_year': 2003,
               'season': 'summer', 'year_offset': -1, 'water': FAO56['maize1'], 'water_source': 'FAO-56 generic',
               'sowing': {'43': (9, 15), '42': (9, 15), '41': (9, 20), '35': (10, 15), '50': (10, 15), '31': (11, 1),
                          '52': (11, 1), '53': (11, 1), '29': (11, 20), '22': (11, 20), '21': (11, 20), '17': (11, 20)},
               'min_train_years': 8, 'min_mean_area_ha': 2000, 'system': 'rainfed_dominant'},
    'wheat': {'name': 'Trigo', 'table': 5457, 'classification': 'c782', 'category': '40127', 'first_year': 2001,
              'season': 'winter', 'year_offset': 0, 'water': FAO56['wheat'], 'water_source': 'FAO-56 generic',
              'sowing': {'41': (5, 15), '43': (6, 10), '42': (6, 10), '35': (4, 25), '50': (4, 20), '31': (3, 10),
                         '52': (3, 10), '53': (3, 10), '29': (5, 1)},
              'min_train_years': 10, 'min_mean_area_ha': 1000, 'system': 'rainfed_south_irrigated_share_in_cerrado_unknown'},
    'rice': {'name': 'Arroz', 'table': 5457, 'classification': 'c782', 'category': '40102', 'first_year': 2001,
             'season': 'summer', 'year_offset': -1, 'water': None, 'water_source': 'none: flooded paddy not represented',
             'sowing': {'43': (10, 15), '42': (10, 1), '41': (10, 15), '51': (11, 15), '11': (11, 15), '15': (12, 1),
                        '21': (12, 15), '17': (11, 15), '22': (12, 15), '52': (11, 15), '50': (11, 1)},
             'irrigated_states': ['43', '42'],
             'min_train_years': 10, 'min_mean_area_ha': 500, 'system': 'flooded_irrigated_in_RS_SC_upland_elsewhere'},
    'bean1': {'name': 'Feijão 1ª safra (águas)', 'table': 1002, 'classification': 'c81', 'category': '117991', 'first_year': 2003,
              'season': 'summer', 'year_offset': -1, 'water': FAO56['bean'], 'water_source': 'FAO-56 generic',
              'sowing': {'41': (9, 1), '42': (9, 1), '43': (9, 1), '35': (10, 15), '31': (10, 15), '52': (10, 15),
                         '53': (10, 15), '50': (10, 15), '51': (10, 15)},
              'min_train_years': 8, 'min_mean_area_ha': 500, 'system': 'rainfed_dominant',
              'calendar_note': 'harvest-year convention assumed: sown in the second half of the year before the PAM year'},
    'bean2': {'name': 'Feijão 2ª safra (seca)', 'table': 1002, 'classification': 'c81', 'category': '117992', 'first_year': 2003,
              'season': 'second', 'year_offset': 0, 'water': FAO56['bean'], 'water_source': 'FAO-56 generic',
              'sowing': {'41': (1, 20), '42': (1, 20), '43': (1, 20), '35': (2, 10), '31': (2, 10), '52': (2, 10),
                         '53': (2, 10), '50': (2, 10), '51': (2, 10)},
              'min_train_years': 8, 'min_mean_area_ha': 500, 'system': 'rainfed_dominant'},
}

# Inventoried for later rounds; reasons they are not trained now.
CATALOG_ONLY = {
    'bean3': {'name': 'Feijão 3ª safra (inverno)', 'table': 1002, 'category': '117993',
              'gap': 'Mostly irrigated in the Cerrado; no reliable irrigation flag per municipality-year yet'},
    'sorghum': {'name': 'Sorgo', 'table': 5457, 'category': '40125', 'gap': 'Second-crop calendar and grain/forage split unverified'},
    'barley': {'name': 'Cevada', 'table': 5457, 'category': '40109', 'gap': '~90 thousand ha in two states; too few municipalities'},
    'cotton': {'name': 'Algodão herbáceo', 'table': 5457, 'category': '40099',
               'gap': 'First and second-crop cotton not separated in PAM; cycle 150-180 days'},
    'cassava': {'name': 'Mandioca', 'table': 5457, 'category': '40119',
                'gap': '12-24 month cycle and harvest timing decoupled from the PAM year'},
    'sugarcane': {'name': 'Cana-de-açúcar', 'gap': 'Semi-perennial; needs its own ratoon and harvest windows'},
    'coffee': {'name': 'Café', 'gap': 'Perennial with biennial bearing; needs its own phenology'},
}

LABEL_UNITS = {'216': ('area_harvested_ha', '1006'), '214': ('production_t', '1017'), '112': ('yield_kg_ha', '33')}


def sowing_date(crop, code, harvest_year):
    from datetime import date
    entry = REGISTRY[crop]
    month, day = entry['sowing'][code[:2]]
    return date(harvest_year + entry['year_offset'], month, day)


def cycle_days(crop):
    water = REGISTRY[crop]['water']
    return sum(water['phase_days']) if water else 120


def horizons(crop):
    """Cutoffs from 30 days, every 15, strictly inside the cycle and at most 105."""
    return tuple(h for h in range(30, 106, 15) if h < cycle_days(crop))


def irrigated_system(crop, code):
    return code[:2] in REGISTRY[crop].get('irrigated_states', [])
