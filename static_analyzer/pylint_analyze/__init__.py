import logging


def _banner(msg: str, enable: bool) -> None:
    if enable:
        logging.info(f"[graphify] {msg}")
