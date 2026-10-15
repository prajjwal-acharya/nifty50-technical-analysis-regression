"""A small check that the shared project environment is installed."""

from importlib import import_module
from importlib.metadata import version
from sys import version_info


def main() -> None:
    if version_info[:2] != (3, 12):
        raise SystemExit("Use Python 3.12 as described in README.md")

    packages = {
        "numpy": "numpy",
        "pandas": "pandas",
        "scikit-learn": "sklearn",
        "matplotlib": "matplotlib",
        "yfinance": "yfinance",
        "openpyxl": "openpyxl",
        "PyYAML": "yaml",
    }
    for distribution, module in packages.items():
        import_module(module)
        print(f"{distribution}: {version(distribution)}")
    print("Environment OK. Run python -m nifty50_ta.data_pipeline to rebuild processed data.")


if __name__ == "__main__":
    main()
