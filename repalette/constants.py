import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))[:-9]
BASE_DATA_DIR = os.path.join(ROOT_DIR, "data")
RAW_DATA_DIR = os.path.join(BASE_DATA_DIR, "raw")
DATA_DIR = os.path.join(BASE_DATA_DIR, "data")
MODELS_DIR = os.path.join(BASE_DATA_DIR, "models")
PL_LOGS_DIR = os.path.join(BASE_DATA_DIR, "pl_logs")
DATABASE_PATH = os.path.join(BASE_DATA_DIR, "sqlite.db")
IMAGE_SIZE = (432, 288)
LIGHTNINGS_LOGS_DIR = "lightning_logs"
DEFAULT_LR = 0.0002
DEFAULT_BETAS = (0.5, 0.999)
