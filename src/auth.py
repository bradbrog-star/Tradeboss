import os

import pyotp
import robin_stocks.robinhood as r
from dotenv import load_dotenv

from .logger import get_logger

log = get_logger(__name__)


def login():
    load_dotenv()
    username = os.environ.get("ROBINHOOD_USERNAME")
    password = os.environ.get("ROBINHOOD_PASSWORD")
    totp_secret = os.environ.get("ROBINHOOD_TOTP_SECRET", "").strip()

    if not username or not password:
        raise RuntimeError(
            "ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD not set. Copy "
            ".env.example to .env and fill it in."
        )

    mfa_code = None
    if totp_secret:
        mfa_code = pyotp.TOTP(totp_secret).now()

    log.info("Logging in to Robinhood as %s", username)
    r.login(
        username=username,
        password=password,
        mfa_code=mfa_code,
        store_session=True,
    )
    log.info("Login OK")
