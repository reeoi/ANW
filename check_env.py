import os

print("API_KEY:", bool(os.getenv("DEEPSEEK_API_KEY")))
print("BASE_URL:", os.getenv("DEEPSEEK_BASE_URL"))
