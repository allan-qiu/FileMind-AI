"""Repository launcher; run setup.ps1 once before using this entrypoint."""

from filemind.app import main

if __name__ == "__main__":
    raise SystemExit(main())
