import json
import os

from openai import OpenAI


class DeepSeekReviewModel:
    def __init__(self):
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        self.name = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.last_usage = None
        self.client = OpenAI(api_key=key, base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                             timeout=60.0, max_retries=0)

    def generate_json(self, *, messages, max_tokens, schema):
        self.last_usage = None
        messages = [dict(m) for m in messages]
        messages[0]["content"] += "\nOUTPUT_JSON_SCHEMA:\n" + json.dumps(schema, ensure_ascii=False)
        try:
            response = self.client.chat.completions.create(model=self.name, messages=messages, max_tokens=max_tokens,
                response_format={"type": "json_object"}, extra_body={"thinking": {"type": "disabled"}})
            if response.usage:
                self.last_usage = response.usage.model_dump()
            choice = response.choices[0]
            if choice.finish_reason != "stop" or not choice.message.content:
                raise ValueError("Incomplete response")
            return choice.message.content
        except Exception as error:
            raise RuntimeError("DeepSeek review request failed") from error
