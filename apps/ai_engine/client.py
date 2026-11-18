import json
from urllib.request import Request, urlopen


class PrivateLLMClient:
    """HTTP client restricted to a private Ollama or vLLM network endpoint."""

    def __init__(self, base_url: str, timeout: int = 20):
        if not base_url.startswith(("http://ollama.", "http://vllm.", "http://localhost", "http://127.0.0.1")):
            raise ValueError("LLM endpoint must be internal.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def infer(self, prompt: str) -> dict:
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps({"prompt": prompt, "stream": False}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())
