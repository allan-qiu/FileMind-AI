"""Read-only loopback health check, never discovers or changes other services."""

import argparse
import json
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("Invalid port")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{args.port}/api/health", timeout=5) as response:
        data = json.load(response)
    if data.get("service") != "filemind-central" or data.get("status") != "ok":
        raise SystemExit("FileMind health check failed")
    print(json.dumps({k: data[k] for k in ("service", "status", "version", "schema_version")}))


if __name__ == "__main__":
    main()
