"""Named Sandbox Example

Demonstrates creating a sandbox with a custom name and using its real ID with
the lightweight ``yr-sandbox`` CLI.

Prerequisites:
  - YR_SERVER_ADDRESS and YR_TOKEN environment variables must be set.
  - Install the SDK: pip install -e sdk/python/

Usage:
  export YR_SERVER_ADDRESS=your-server.example.com
  export YR_TOKEN=your-token
  python named_sandbox.py
"""

import subprocess
import sys

from yr_sandbox import Sandbox


def main():
    name = "my-test-sandbox"

    with Sandbox(cpu=2000, memory=4096, name=name) as sb:
        print(f"Sandbox created with name: {name}")
        print(f"  id: {sb.id}")

        # Verify sandbox is working
        result = sb.commands.run("echo hello from named sandbox")
        print(f"  exec result: {result.stdout.strip()}")

        # Run ``yr-sandbox ls`` in a subprocess to show CLI consistency
        print("\n--- yr-sandbox ls / output ---")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "yr_sandbox.cli", "ls", sb.id, "/"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            print(proc.stdout)
            if proc.returncode == 0:
                print("[OK] yr-sandbox CLI can access the named sandbox")
            else:
                print(f"[WARN] yr-sandbox CLI returned rc={proc.returncode}")
        except Exception as e:
            print(f"  (could not run yr-sandbox ls: {e})")

    print("\nSandbox terminated.")


if __name__ == "__main__":
    main()
