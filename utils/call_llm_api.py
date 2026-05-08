import os
import time
import json
import requests
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from graph.utils.logger import logger

_LLM_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_DEFAULT_LLM_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "llm_config.yaml"


def _load_llm_config() -> Dict[str, Any]:
    global _LLM_CONFIG_CACHE
    if _LLM_CONFIG_CACHE is not None:
        return _LLM_CONFIG_CACHE

    config_path = Path(os.getenv("LLM_CONFIG_PATH", str(_DEFAULT_LLM_CONFIG_PATH)))
    if not config_path.exists():
        raise FileNotFoundError(f"LLM config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("llm_config.yaml must contain a top-level object")
    _LLM_CONFIG_CACHE = data
    return _LLM_CONFIG_CACHE


def _resolve_service_config(llm_config: Dict[str, Any]) -> Dict[str, Any]:
    service = llm_config.get("service", {}) or {}
    base_url = os.getenv("LLM_SERVICE_BASE_URL", str(service.get("base_url", "")).strip())
    completion_path = str(service.get("completion_path", "/llm/complete"))
    timeout_seconds = int(service.get("timeout_seconds", 60))
    max_retries = int(service.get("max_retries", 3))
    retry_backoff_seconds = float(service.get("retry_backoff_seconds", 1.5))
    if not base_url:
        raise ValueError("doc processing base URL is required in llm_config.yaml or LLM_SERVICE_BASE_URL")
    return {
        "base_url": base_url.rstrip("/"),
        "completion_path": completion_path,
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "retry_backoff_seconds": retry_backoff_seconds,
    }


def _resolve_use_case_config(llm_config: Dict[str, Any], use_case: str) -> Dict[str, Any]:
    use_cases = llm_config.get("use_cases", {}) or {}
    cfg = use_cases.get(use_case)
    if not isinstance(cfg, dict):
        raise ValueError(f"LLM use case not configured in llm_config.yaml: {use_case}")
    provider = str(cfg.get("provider", "")).strip()
    if not provider:
        raise ValueError(f"LLM use case '{use_case}' must define provider")
    return cfg

class LLMCompletionCall:
    def __init__(
        self,
        llm_model: str = "",
        llm_base_url: str = "",
        llm_api_key: str = "",
        temperature: float = 0.001,
        use_case: Optional[str] = None,
    ):
        self.temperature = temperature
        if llm_model or llm_base_url or llm_api_key:
            logger.warning(
                "Direct LLM credentials were provided but are ignored; using doc_processing /llm/complete only."
            )
        llm_config = _load_llm_config()
        self.service_config = _resolve_service_config(llm_config)
        self.default_use_case = use_case or str(llm_config.get("default_use_case", "query_answering"))
        self.llm_config = llm_config

    def _build_request_body(self, content: str, use_case: str) -> Dict[str, Any]:
        cfg = _resolve_use_case_config(self.llm_config, use_case)
        payload: Dict[str, Any] = {
            "provider": cfg["provider"],
            "messages": [{"role": "user", "content": content}],
        }
        model = cfg.get("model")
        if model:
            payload["model"] = model
        reasoning_effort = cfg.get("reasoning_effort")
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        response_format = cfg.get("response_format")
        if response_format:
            payload["response_format"] = response_format
        return payload

    def call_api(self, content: str, use_case: Optional[str] = None) -> str:
        """
        Call doc_processing /llm/complete API with retry mechanism.

        Args:
            content: Prompt content
            use_case: Optional llm_config use case override

        Returns:
            Generated text response
        """
        selected_use_case = use_case or self.default_use_case
        endpoint = f"{self.service_config['base_url']}{self.service_config['completion_path']}"
        llm_data = self._build_request_body(content, selected_use_case)

        for i in range(self.service_config["max_retries"]):
            try:
                headers = {"Content-Type": "application/json"}
                response = requests.post(
                    endpoint,
                    json=llm_data,
                    headers=headers,
                    timeout=self.service_config["timeout_seconds"],
                )
                if response.status_code != 200:
                    raise RuntimeError(f"LLM service http {response.status_code}: {response.text}")
                result_data = json.loads(response.text)
                raw = str(result_data.get("content", "") or "")
                clean_completion = self._clean_llm_content(raw)
                return clean_completion
            except Exception as e:
                if i < self.service_config["max_retries"] - 1:
                    time.sleep(self.service_config["retry_backoff_seconds"] * (2 ** i))
                else:
                    logger.error(
                        f"LLM service call failed for use_case={selected_use_case}. Error: {e}"
                    )
                    raise e

    def _clean_llm_content(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        t = re.sub(r"[\u200B-\u200D\uFEFF]", "", t)
        end_think_re = re.compile(r"</\s*think\s*>", re.IGNORECASE)
        m_end = end_think_re.search(t)
        if m_end:
            t = t[m_end.end():].strip()
        fence_re = re.compile(r"^\s*```(?:\s*\w+)?\s*\n(?P<body>[\s\S]*?)\n\s*```\s*$", re.MULTILINE)
        m = fence_re.match(t)
        if m:
            t = m.group("body").strip()
        else:
            if t.startswith("```") and t.endswith("```") and len(t) >= 6:
                t = t[3:-3].strip()

        if t.lower().startswith("json\n"):
            t = t.split("\n", 1)[1].strip()

        return t
