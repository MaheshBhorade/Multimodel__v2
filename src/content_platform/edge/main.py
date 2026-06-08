import logging

from content_platform.edge.agent import EdgeAgent


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    EdgeAgent().run_forever()
