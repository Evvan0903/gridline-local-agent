"""Compatibility import/CLI; dataset-specific implementation lives in examples."""
import importlib
import runpy
_impl = importlib.import_module('examples.clipboard.revenue')
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
if __name__ == "__main__":
    runpy.run_module('examples.clipboard.revenue', run_name="__main__")
