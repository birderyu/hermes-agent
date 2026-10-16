"""Export the bridge contract and its runtime-only public schema entrypoints.

Run with the Hermes development environment's Python, passing the Ollo contracts
directory. This is a maintenance command, never imported by the plugin runtime.
"""
import argparse
import json
from pathlib import Path

from ruamel.yaml import YAML

PUBLIC_ROOTS = ('GTDResult', 'GTDSuggestion', 'OlloCards', 'OlloReportCardsInput', 'DayPlan')


def bundle(contracts):
    yaml = YAML(typ='safe')
    documents = {
        prefix: yaml.load((contracts / filename).read_text(encoding='utf-8'))['components']['schemas']
        for prefix, filename in [('B', 'bridge.openapi.yaml'), ('P', 'openapi.yaml')]
    }
    definitions = {}

    def include(prefix, name):
        key = prefix + '_' + name
        if key not in definitions:
            # Reserve before descending so recursive models remain valid refs.
            definitions[key] = None
            definitions[key] = rewrite(documents[prefix][name], prefix)
        return '#/$defs/' + key

    def rewrite(value, prefix):
        if isinstance(value, list):
            return [rewrite(child, prefix) for child in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, child in value.items():
            if key == '$ref':
                source, pointer = child.split('#', 1)
                if source not in ('', './openapi.yaml') or not pointer.startswith('/components/schemas/'):
                    raise ValueError(f'Unsupported schema reference: {child}')
                result[key] = include('P' if source else prefix, pointer.removeprefix('/components/schemas/'))
            else:
                result[key] = rewrite(child, prefix)
        return result

    for name in documents['B']:
        include('B', name)
    # These are requested directly by Python; bridge $refs alone cannot find them.
    for name in PUBLIC_ROOTS:
        include('P', name)
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$defs': definitions}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('contracts', type=Path, help='Ollo contracts directory')
    args = parser.parse_args()
    output = Path(__file__).with_name('schema.json')
    output.write_text(json.dumps(bundle(args.contracts), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
