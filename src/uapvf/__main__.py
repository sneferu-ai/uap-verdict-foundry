"""Allow `python -m uapvf` to invoke the CLI."""
from uapvf.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
