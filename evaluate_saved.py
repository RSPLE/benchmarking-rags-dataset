import os
from pathlib import Path

from dotenv import load_dotenv

from benchmark_pipeline import execute_pipeline

if __name__ == "__main__":
    load_dotenv(Path(__file__).parent / ".env", override=False)
    os.environ["BENCHMARK_MODE"] = "evaluate"
    execute_pipeline(os.environ["BENCHMARK_PROJECT"])
