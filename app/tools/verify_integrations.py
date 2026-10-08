import os
import subprocess

from app.paths import ROOT


def main():
    env = {**os.environ, "RAGAS_DO_NOT_TRACK": "true", "ANONYMIZED_TELEMETRY": "false"}
    failures = []
    for project in sorted((ROOT / "app" / "rags").iterdir()):
        if not project.is_dir():
            continue
        command = [
            str(project / ".venv/bin/python"),
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_integrations.py",
            "-v",
        ]
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=90
        )
        output = result.stdout + result.stderr
        print(f"{project.name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
        if result.returncode:
            log = ROOT / "tmp" / f"integration-{project.name}.log"
            log.parent.mkdir(exist_ok=True)
            log.write_text(output)
            print(output[-6000:])
            print(f"Full log: {log}")
            failures.append(project.name)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
