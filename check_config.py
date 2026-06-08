import sys

sys.path.insert(0, "D:\\Development_alma\\anw")
from config_loader import load_config

cfg = load_config()
print("api_key:", cfg.deepseek_api_key[:20] + "..." if cfg.deepseek_api_key else "EMPTY")
print("mock:", cfg.data.get("deepseek", {}).get("mock"))
print("dry_run:", cfg.is_dry_run)
