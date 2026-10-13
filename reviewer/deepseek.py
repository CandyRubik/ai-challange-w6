import hashlib
import json
import os

from openai import APIResponseValidationError, OpenAI

from .semantic_models import InvalidModelResponse, REPAIR_MARKER
from .generation import ground_response, prepare_messages


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

    @staticmethod
    def measure_input(*, messages, schema):
        prepared, _, _ = prepare_messages(messages, schema)
        return len(json.dumps(prepared, ensure_ascii=False).encode())

    def generate_bounded_json(self, *, messages, max_tokens, schema, context_bytes):
        self.last_usage = None
        self.last_input = None
        messages, payload, aliases = prepare_messages(messages, schema)
        wire = json.dumps(messages, ensure_ascii=False).encode()
        self.last_input = {"sha256": hashlib.sha256(wire).hexdigest(), "bytes": len(wire), "protocol": "line-assessments-v3", "request_sent": False, "reference_resolutions": []}
        if len(wire) > context_bytes:
            raise RuntimeError("DeepSeek wire context exceeds budget")
        options = {"temperature": 0.0} if REPAIR_MARKER in messages[0]["content"] else {}
        self.last_input["temperature"] = options.get("temperature")
        try:
            self.last_input["request_sent"] = True
            response = self.client.chat.completions.create(model=self.name, messages=messages, max_tokens=max_tokens,
                response_format={"type": "json_object"}, extra_body={"thinking": {"type": "disabled"}}, **options)
        except (APIResponseValidationError, json.JSONDecodeError) as error:
            raise InvalidModelResponse("DeepSeek API envelope is invalid") from error
        except Exception as error:
            raise RuntimeError("DeepSeek review request failed") from error
        try:
            usage = getattr(response, "usage", None)
            if hasattr(usage, "model_dump"):
                counts = usage.model_dump()
                if all(type(counts.get(key)) is int and counts[key] >= 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
                    self.last_usage = counts
            choices = getattr(response, "choices", None)
            if not isinstance(choices, list) or not choices:
                raise ValueError("No completion choices")
            choice = choices[0]
            content = getattr(getattr(choice, "message", None), "content", None)
            if getattr(choice, "finish_reason", None) != "stop" or not isinstance(content, str) or not content:
                raise ValueError("Incomplete response")
            if len(content.encode()) > max_tokens * 16:
                raise ValueError("Model response exceeds budget")
            return ground_response(content, schema, payload, aliases, self.last_input["reference_resolutions"])
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise InvalidModelResponse("DeepSeek structured response is invalid") from error
