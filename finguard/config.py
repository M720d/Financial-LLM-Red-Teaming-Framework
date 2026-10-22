import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
CLIENTS_FILE = DATA_DIR / "clients.json"
ATTACKS_DIR = ROOT / "attacks"
REPORTS_DIR = ROOT / "reports"

load_dotenv(ROOT / ".env")

PROVIDER = os.getenv("FINGUARD_PROVIDER", "groq").strip().lower()
# Empty uses the provider's default model.
MODEL_NAME = os.getenv("FINGUARD_MODEL", "").strip()
# Empty string omits the parameter, for models that only accept their default temperature.
TEMPERATURE = os.getenv("FINGUARD_TEMPERATURE", "0")
RETRIEVAL_K = int(os.getenv("FINGUARD_RETRIEVAL_K", "4"))
MAX_TOOL_ROUNDS = 4
