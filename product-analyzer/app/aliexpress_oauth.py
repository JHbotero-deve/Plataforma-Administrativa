"""Retired AliExpress OAuth integration.

Central imports products from public product URLs and does not call marketplace
OAuth, affiliate, search, or catalog APIs.
"""
from fastapi import APIRouter

router = APIRouter()


def ensure_aliexpress_oauth_storage():
    return None
