import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

from app.benchmark.pipeline import execute_pipeline

if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    os.environ["BENCHMARK_MODE"] = "evaluate"
    execute_pipeline(os.environ["BENCHMARK_PROJECT"])
