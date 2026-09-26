import logging

LOG_FORMAT = "logtime::%(asctime)s level::%(levelname)s logname::%(name)s message::%(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)

    logging.getLogger("aiormq").setLevel(logging.WARNING)
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
