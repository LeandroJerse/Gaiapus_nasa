# Gaiapus — Field Shift (NASA Space Apps 2026)

Gaiapus lets a grower choose a place, see what the evidence for that place covers, open a season simulation and compare
management strategies by what they do to soil water. Everything shown is labelled as observation, simulation or
research estimate, with its source, period and limits.

This file describes what is in the repositories today. Where something is not built or not verified, it says so.

## The problem

After the soybean harvest in central Brazil a field can go to second-crop maize and then stay bare through the dry
season, carry a cover (brachiaria) with the maize, or stay fallow until the next soybean. The choice changes how many
days the soil has a crop or cover on it and where the rain goes (used by plants, drained, or held for the next sowing).
Growers weigh that against production and margin. The challenge asks for NASA data, soil and crops combined with the
grower's priorities; Gaiapus answers with a transparent water balance rather than a recommendation engine.

## The journey in the app

| Step | Route | What it shows |
|---|---|---|
| Choose a place | `/#/explorar` | World map; Brazil and the United States have data; other countries stay visible without data |
| See the data of the place | `/#/municipio?mun=<IBGE code>` | Climate, soil-water history and imagery where published; a "simulate here" action |
| Simulate the coming season | `/#/simulador?modo=planejamento&local=<BR:ibge:…\|US:fips:…>` | 2027/2028 season under climatological scenarios (historical analog years), with crop and year pickers |
| Compare crops of the same place | same screen, "Compare crops" card | Water satisfaction per climate member, whole-cycle and reproductive stress on separate rows |
| Compare management strategies | `/#/field-shift` | Three historical strategies in Sorriso (MT), priority of the grower, rain sensitivity |
| Past seasons | `/#/simulador?modo=historico` | Sorriso, 2015/16 to 2020/21, day by day |

The 2027/2028 planning uses climatological scenarios. It is not a daily weather forecast. Yield values for US counties
are research estimates at county scale and are labelled as such; no model was promoted in this work.

## Data actually used

| Source | Used for | Notes |
|---|---|---|
| NASA POWER daily, point query | Rain, temperature, radiation, wind, humidity, pressure for the water balance | Meteorology at roughly 0.5° × 0.625°; the package records the source string `NASA POWER daily point (IMERG rain), retrospective`. A grid point is not a municipal mean nor a field measurement |
| FAO-56 Penman–Monteith | Reference evapotranspiration (ET0) and crop coefficients | ET0 is derived from weather data; it is not a direct satellite measurement |
| IBGE PAM, USDA NASS | Municipal and county yield history for the yield products | Not used by the Field Shift water indicators |
| HLS (Landsat/Sentinel, 30 m) | Field imagery and NDVI where a field is published | NDVI = (NIR − red)/(NIR + red), from −1 to 1; greenness does not prove yield |

Probe readings in the app are synthetic illustrations and are labelled so; they are not validation data.

## Method of the Field Shift comparison

- One daily single-layer soil-water bucket (FAO-56 crop coefficients and depletion fraction, fixed root zone) run continuously over six agricultural years,
  2015/16 to 2020/21, with the retrospective NASA POWER series of Sorriso.
- Three strategies with the same soybean, calendar and soil hypothesis:
  A soybean → second-crop maize → bare fallow (reference of the demonstration);
  B soybean → maize with brachiaria → cover until desiccation;
  C soybean → bare fallow until the next soybean (no maize; fewer crops, not a diversified rotation).
- Strategies are compared on windows that are identical for all of them (the agricultural year and the period after
  the soybean harvest). Differences are paired year by year; the range reported is the range across the six seasons,
  not a prediction interval.
- Rain sensitivity: every daily rain multiplied by 0.8, 1.0 and 1.2 on the same day sequence, all three strategies
  recomputed. It is a sensitivity test on historical weather, not a climate forecast.
- Results are published as one immutable package (`manifest.json`, `summary.json`, `series.json`) whose id derives from
  inputs, code and parameters. The app shows the summary and the daily series only when both belong to that package.

Numbers for slides and video: [field-shift-numbers.md](field-shift-numbers.md), generated from the package by
`scripts/field_shift_numbers.py`. Traceability from requirement to route, evidence and commit:
[field-shift-traceability.md](field-shift-traceability.md).

## Limits

- Simulation under stated hypotheses (soil class AD4 with 55 mm of available water, fixed sowing date, bare-fallow Kc
  0.30, cover Kc 0.80, straw factor 0.80). No field measurement of these effects in Sorriso.
- Not simulated: yield, soil carbon, nitrogen, infiltration, weeds, pests and nematodes, costs and margin. No yield or
  margin effect of a strategy is claimed. Margin is not calculated because the scenario has no yield.
- "Soil covered" is a yes/no state of each day (a crop or cover is present), not a measured ground-cover percentage.
- Drainage is water leaving the modelled root zone; it does not measure erosion, leaching or infiltration.
- Temperature sensitivity is not calculated. A fourth strategy with a legume cover (crotalaria) was not calculated for
  lack of validated parameters.
- The qualitative agronomy table says "not assessed" for every cell: no source applicable to soybean in Mato Grosso was
  verified for those aspects.

## Run it

Science (Python 3.13):

```bash
python -m pytest tests/test_field_shift_forcing.py tests/test_field_shift_extension.py tests/test_water_balance.py
python -m data.ingest.field_shift_v2          # writes data/processed/field_shift_v2/<package id>/, never overwrites
python scripts/field_shift_numbers.py data/processed/field_shift_v2/<package id> docs/submission/field-shift-numbers.md
```

The producer needs the cached NASA POWER file of Sorriso (`power/power_5107925.json` under the raw-data directory).
`data/processed/` is not versioned here; the published package is the copy pinned in the app. In the app the crop
comparison metric described above is labelled "Water supply".

App (Node 22, pnpm 10.14; repository `LuksbellN/Gaiapus-APP`):

```bash
pnpm install --frozen-lockfile
pnpm --filter @gaiapus/contracts build
pnpm dev:api                                   # API on 3001
pnpm --filter @gaiapus/web dev                 # web on 5173
pnpm --filter @gaiapus/web test
```

## Use of AI assistance

The code, tests and documents of this delivery were written with AI coding assistants (Anthropic Claude and OpenAI
Codex) under the direction and review of the team. Scientific results come from the code in this repository run on the
cited data; no number was generated by a language model. Check the rules of the event for how this must be declared in
the submission form.
