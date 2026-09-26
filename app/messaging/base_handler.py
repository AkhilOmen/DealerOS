from abc import ABC, abstractmethod


class AsyncBaseHandler(ABC):

    @abstractmethod
    async def handle_message(self, message: dict, headers: dict) -> None: ...
