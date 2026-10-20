"""Compatibility import/CLI; dataset-specific implementation lives in examples."""
import importlib
import runpy
_impl = importlib.import_module('examples.clipboard.legacy')
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
if __name__ == "__main__":
    from local_analyst.cli import main; main()
