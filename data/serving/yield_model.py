"""Load portable v7 yield models (JSON + state_dict) and predict on CPU.

Formats written by data.ingest.train_v7: 'gaiapus-ridge-v1' (history Ridge plus residual
Ridge per cutoff) and 'gaiapus-mlp-v1' (history Ridge per cutoff plus a seed ensemble of
MLPs on the residual, with the cutoff as input). Only numpy and torch are needed; no
pickled project classes are loaded.
"""
import json
from pathlib import Path


def _linear(spec, row):
    import numpy as np
    x = np.array([row.get(f) for f in spec['features']], dtype=float)
    x = np.where(np.isnan(x), spec['imputer_median'], x)
    z = (x - np.array(spec['scaler_mean'])) / np.array(spec['scaler_scale'])
    return float(z @ np.array(spec['coef']) + spec['intercept'])


def _missing(spec_features, row):
    return [f for f in spec_features if f not in row]


def _hgb(spec, row):
    """Gradient-boosted trees exported from scikit-learn: preprocessing, then the sum of leaf values."""
    import numpy as np
    x = np.array([row.get(f) for f in spec['features']], dtype=float)
    x = np.where(np.isnan(x), spec['imputer_median'], x)
    z = (x - np.array(spec['scaler_mean'])) / np.array(spec['scaler_scale'])
    total = spec['baseline']
    for tree in spec['trees']:
        node = 0
        while not tree['is_leaf'][node]:
            value = z[tree['feature_idx'][node]]
            if np.isnan(value):
                node = tree['left'][node] if tree['missing_go_to_left'][node] else tree['right'][node]
            else:
                node = tree['left'][node] if value <= tree['threshold'][node] else tree['right'][node]
        total += tree['value'][node]
    return float(total)


class YieldModel:
    def __init__(self, path):
        path = Path(path)
        self.spec = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        self.format = self.spec['format']
        if self.format not in ('gaiapus-ridge-v1', 'gaiapus-mlp-v1', 'gaiapus-hgb-v1', 'gaiapus-reference-v1', 'gaiapus-v8-v1'):
            raise ValueError(f'Unsupported model format: {self.format}')
        if self.format == 'gaiapus-v8-v1':
            self._init_v8(path)
            return
        if self.format == 'gaiapus-reference-v1':
            self.cutoffs = sorted(int(h) for h in self.spec['cutoffs'])
            return
        self.cutoffs = sorted(int(h) for h in self.spec['history_by_horizon'])
        if self.format == 'gaiapus-mlp-v1':
            import torch
            weights = torch.load(path.with_suffix('.pt'), map_location='cpu', weights_only=True)
            self.networks = []
            for index in range(len(self.spec['seeds'])):
                state = {k.split('.', 1)[1]: v for k, v in weights.items() if k.startswith(f'seed{index}.')}
                layers, previous = [], len(self.spec['features'])
                for size in self.spec['hidden']:
                    layers += [torch.nn.Linear(previous, size), torch.nn.ReLU()]
                    if self.spec['dropout']:
                        layers.append(torch.nn.Dropout(self.spec['dropout']))
                    previous = size
                network = torch.nn.Sequential(*layers, torch.nn.Linear(previous, 1))
                network.load_state_dict(state)
                network.eval()
                self.networks.append(network)

    def _init_v8(self, path):
        """v8: base (history Ridge, published reference or training mean) + shrink x residual correction."""
        import torch
        self.cutoffs = sorted(int(h) for h in self.spec['cutoffs'])
        residual = self.spec['residual']
        self.networks = []
        if residual and residual['learner'] == 'mlp':
            weights = torch.load(path.with_suffix('.pt'), map_location='cpu', weights_only=True)
            for index in range(len(residual['seeds'])):
                state = {k.split('.', 1)[1]: v for k, v in weights.items() if k.startswith(f'seed{index}.')}
                layers, previous = [], len(residual['features'])
                for size in residual['hidden']:
                    layers += [torch.nn.Linear(previous, size), torch.nn.ReLU()]
                    if residual['dropout']:
                        layers.append(torch.nn.Dropout(residual['dropout']))
                    previous = size
                network = torch.nn.Sequential(*layers, torch.nn.Linear(previous, 1))
                network.load_state_dict(state)
                network.eval()
                self.networks.append(network)

    def _v8_required(self, horizon):
        base, residual = self.spec['base'], self.spec['residual']
        needed = set()
        if base['kind'] == 'history':
            needed |= set(base['history_by_horizon'][str(horizon)]['features'])
        elif base['kind'] == 'reference':
            needed.add(base['column'])
        if residual:
            features = residual['by_horizon'][str(horizon)]['features'] if residual['learner'] != 'mlp' else residual['features']
            needed |= set(features) - {'horizon_days'}
        return sorted(needed)

    def _v8_predict(self, row, horizon):
        base, residual = self.spec['base'], self.spec['residual']
        if base['kind'] == 'history':
            value = _linear(base['history_by_horizon'][str(horizon)], row)
        elif base['kind'] == 'reference':
            ref = row.get(base['column'])
            if ref is None or ref != ref:
                state = str(row.get('municipality_code') or '')[:2]
                ref = base.get('fallback_by_state', {}).get(state, base['fallback_kg_ha'])
            value = float(ref)
        else:
            value = float(base['value_kg_ha'])
        if not residual:
            return value
        if residual['learner'] == 'ridge':
            correction = _linear(residual['by_horizon'][str(horizon)], row)
        elif residual['learner'] == 'hgb':
            correction = _hgb(residual['by_horizon'][str(horizon)], row)
        else:
            import numpy as np
            import torch
            x = np.array([row.get(f) for f in residual['features']], dtype=float)
            x = np.where(np.isnan(x), residual['imputer_median'], x)
            z = torch.tensor((x - np.array(residual['scaler_mean'])) / np.array(residual['scaler_scale']), dtype=torch.float32)[None]
            with torch.no_grad():
                correction = float(np.mean([net(z).item() for net in self.networks])) * residual['residual_scale']
        return value + self.spec['shrink'] * correction

    def required_features(self, horizon):
        if self.format == 'gaiapus-v8-v1':
            return self._v8_required(horizon)
        if self.format == 'gaiapus-reference-v1':
            return [self.spec['column']]
        history = self.spec['history_by_horizon'][str(horizon)]['features']
        if self.format in ('gaiapus-ridge-v1', 'gaiapus-hgb-v1'):
            return sorted(set(history) | set(self.spec['residual_by_horizon'][str(horizon)]['features']))
        return sorted(set(history) | set(self.spec['features']) - {'horizon_days'})

    def predict(self, row):
        """`row` holds the cohort features for one municipality, crop and cutoff (`horizon_days`)."""
        horizon = int(row['horizon_days'])
        if horizon not in self.cutoffs:
            raise ValueError(f'Cutoff {horizon} not supported; supported: {self.cutoffs}')
        missing = _missing(self.required_features(horizon), row)
        if missing:
            raise ValueError(f'Missing required features: {missing}')
        if self.format == 'gaiapus-v8-v1':
            return self._v8_predict(row, horizon)
        if self.format == 'gaiapus-reference-v1':
            value = row.get(self.spec['column'])
            return float(self.spec['fallback_kg_ha'] if value is None or value != value else value)
        history = _linear(self.spec['history_by_horizon'][str(horizon)], row)
        if self.format == 'gaiapus-ridge-v1':
            return history + _linear(self.spec['residual_by_horizon'][str(horizon)], row)
        if self.format == 'gaiapus-hgb-v1':
            return history + _hgb(self.spec['residual_by_horizon'][str(horizon)], row)
        import numpy as np
        import torch
        x = np.array([row.get(f) for f in self.spec['features']], dtype=float)
        x = np.where(np.isnan(x), self.spec['imputer_median'], x)
        z = torch.tensor((x - np.array(self.spec['scaler_mean'])) / np.array(self.spec['scaler_scale']), dtype=torch.float32)[None]
        with torch.no_grad():
            residual = float(np.mean([net(z).item() for net in self.networks])) * self.spec['residual_scale']
        return history + residual
