"""Install with the three entry scripts directly in the owning home's scripts/."""
import importlib.util
from pathlib import Path
import sys


def main(kind):
    home = Path(__file__).resolve().parent.parent
    source = home / 'plugins/ollo-bridge/collector.py'
    # Load the stdlib-only collector as a package without executing plugin
    # __init__.py, discovering plugins, or reading any authentication key.
    spec = importlib.util.spec_from_file_location('_ollo_cron_collector', source,
                                                submodule_search_locations=[str(source.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module.main(kind, home)
    except (OSError, ImportError):
        sys.stderr.write('Ollo collection failed: collector_not_installed\n')
        return 1
