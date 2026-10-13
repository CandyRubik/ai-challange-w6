import hashlib
import json
import os

from openai import OpenAI

from .generation import display_payload, ground_response, protocol, response_type


class DeepSeekReviewModel:
    def __init__(self):
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")
        self.name = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.last_usage = None
        self.last_input = None
        self.client = OpenAI(api_key=key, base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                             timeout=60.0, max_retries=0)

    def generate_json(self, *, messages, max_tokens, schema):
        return self.generate_bounded_json(messages=messages, max_tokens=max_tokens, schema=schema, context_bytes=48_000)

    def generate_bounded_json(self, *, messages, max_tokens, schema, context_bytes):
        self.last_usage = None
        self.last_input = None
        payload = json.loads(messages[-1]["content"])
        displayed, aliases = display_payload(payload)
        messages = [dict(m) for m in messages]
        messages[-1]["content"] = json.dumps(displayed, ensure_ascii=False)
        messages[0]["content"] += "\n" + protocol(schema) + "\nOUTPUT_JSON_SCHEMA:\n" + json.dumps(response_type(schema).model_json_schema(), ensure_ascii=False)
        wire = json.dumps(messages, ensure_ascii=False).encode()
        self.last_input = {"sha256": hashlib.sha256(wire).hexdigest(), "bytes": len(wire), "protocol": "line-assessments-v3", "request_sent": False, "reference_resolutions": []}
        if len(wire) > context_bytes:
            raise RuntimeError("DeepSeek wire context exceeds budget")
        try:
            self.last_input["request_sent"] = True
            response = self.client.chat.completions.create(model=self.name, messages=messages, max_tokens=max_tokens,
                response_format={"type": "json_object"}, extra_body={"thinking": {"type": "disabled"}})
            if response.usage:
                self.last_usage = response.usage.model_dump()
            choice = response.choices[0]
            if choice.finish_reason != "stop" or not choice.message.content:
                raise ValueError("Incomplete response")
            if len(choice.message.content.encode()) > max_tokens * 16:
                raise ValueError("Model response exceeds budget")
            return ground_response(choice.message.content, schema, payload, aliases, self.last_input["reference_resolutions"])
        except Exception as error:
            raise RuntimeError("DeepSeek review request failed") from error
