from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glaka.config import get_settings

engine = create_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine)
