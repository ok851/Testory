# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import AsyncMock, MagicMock

from web_capture.validator import evaluate_locator_async


def test_test_locator_unique():
    page = MagicMock()
    loc = MagicMock()
    loc.count = AsyncMock(return_value=1)
    item = MagicMock()
    item.inner_text = AsyncMock(return_value="hello")
    item.bounding_box = AsyncMock(return_value={"x": 0, "y": 0, "width": 10, "height": 10})
    loc.nth.return_value = item
    page.locator.return_value = loc

    result = asyncio.run(evaluate_locator_async(page, "css", "#kw"))
    assert result["success"] is True
    assert result["unique"] is True
    assert result["match_count"] == 1
