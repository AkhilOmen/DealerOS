from app.db.models import QueryLog
from app.db.repositories.base import AsyncBaseRepository


class QueryLogRepository(AsyncBaseRepository[QueryLog]):
    pass


query_log_repository = QueryLogRepository(QueryLog)
