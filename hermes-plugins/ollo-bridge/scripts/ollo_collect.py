"""Install with the three entry scripts directly in the owning home's scripts/."""
import importlib.util
from pathlib import Path
import sys


def main(kind):
    home = Path(__file__).resolve().parent.parent
    source = home / 'plugins/ollo-bridge/collector.py'
    # Load only the collector package, without plugin registration/discovery.
    # Its gateway reads use this owning home's bridge credential, never a CLI argument.
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
