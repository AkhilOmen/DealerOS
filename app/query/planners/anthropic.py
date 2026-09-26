import logging
from datetime import date

import anthropic
from pydantic import ValidationError

from app.core.config import settings
from app.query.plan import PlannerOutput
from app.query.planner import SYSTEM_PROMPT, QueryPlanner, user_message
from app.utils.error import PlannerError

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicPlanner(QueryPlanner):
    def __init__(self) -> None:
        self.model = settings.LLM_MODEL
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.LLM_API_KEY.get_secret_value() if settings.LLM_API_KEY else None,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=2,
        )

    async def plan(self, question: str, today: date) -> PlannerOutput:
        try:
            response = await self._client.beta.messages.parse(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": user_message(question, today)
                    }
                ],
                output_format=PlannerOutput,
                output_config={"effort": settings.LLM_EFFORT},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError as ex:
            raise PlannerError("model rate limited") from ex
        except anthropic.APIStatusError as ex:
            raise PlannerError(f"model API error {ex.status_code}: {ex.message}") from ex
        except anthropic.APIConnectionError as ex:  # includes timeouts
            raise PlannerError("could not reach the model") from ex
        except ValidationError as ex:  # parse() validates the reply against PlannerOutput
            raise PlannerError(f"the model returned an invalid plan: {ex.errors()[0]['msg']}") from ex

        if response.stop_reason == "refusal":
            raise PlannerError("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise PlannerError("the model's answer was cut off")
        if response.parsed_output is None:
            raise PlannerError("the model returned no plan")
        logger.info("planned with %s request_id=%s", response.model, response._request_id)
        return response.parsed_output
