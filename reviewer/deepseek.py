import os

from openai import OpenAI

from app.providers.deepseek import DeepSeekProvider


class DeepSeekReviewModel:
    def __init__(self):
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        self.name = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        client = OpenAI(api_key=key, base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                        timeout=60.0, max_retries=0)
        self.provider = DeepSeekProvider(client=client, model=self.name, thinking_enabled=False)

    def generate_json(self, *, messages, max_tokens, schema):
        return self.provider.generate_json(messages=messages, max_tokens=max_tokens, schema=schema)
