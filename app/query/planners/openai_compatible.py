import json
import logging
from datetime import date

import openai
from pydantic import ValidationError

from app.core.config import settings
from app.query.plan import PlannerOutput
from app.query.planner import SYSTEM_PROMPT, QueryPlanner, user_message
from app.utils.error import PlannerError

logger = logging.getLogger(__name__)

_JSON_INSTRUCTIONS = (
    "\n\nReply with a single JSON object and nothing else, matching this JSON schema:\n"
    + json.dumps(PlannerOutput.model_json_schema())
)


class OpenAICompatiblePlanner(QueryPlanner):
    def __init__(self) -> None:
        self.model = settings.LLM_MODEL
        self._client = openai.AsyncOpenAI(
            api_key=settings.LLM_API_KEY.get_secret_value() if settings.LLM_API_KEY else None,
            base_url=settings.LLM_BASE_URL or None,  # empty -> the provider's default (OpenAI)
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=2,
        )

    async def plan(self, question: str, today: date) -> PlannerOutput:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT + _JSON_INSTRUCTIONS
                    },
                    {
                        "role": "user",
                        "content": user_message(question, today)
                    },
                ],
                response_format={"type": "json_object"},
            )
        except openai.APIStatusError as ex:
            raise PlannerError(f"model API error {ex.status_code}: {ex.message}") from ex
        except openai.APIConnectionError as ex:  # includes timeouts
            raise PlannerError("could not reach the model") from ex

        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise PlannerError("the model's answer was cut off")
        try:
            return PlannerOutput.model_validate_json(choice.message.content or "")
        except ValidationError as ex:
            raise PlannerError(f"the model returned an invalid plan: {ex.errors()[0]['msg']}") from ex
