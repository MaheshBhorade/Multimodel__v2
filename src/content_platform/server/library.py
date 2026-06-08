from sqlalchemy.orm import Session


def seed_reference_library(db: Session) -> None:
    """
    Prototype mode.

    No fake data.
    No automatic content.

    Library will be populated only
    through reference_ingestor.py.
    """
    return