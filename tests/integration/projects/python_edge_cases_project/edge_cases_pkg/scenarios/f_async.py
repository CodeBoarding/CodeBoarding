"""Async functions and awaited call chains."""

# Baseline (this file): references=4 classes=0 nodes=4 outgoing_edges=3 incoming_edges=3

import asyncio


async def async_leaf() -> int:
    return 1


async def async_mid() -> int:
    return await async_leaf()


async def async_root() -> int:
    return await async_mid()


def run_async_sync() -> int:
    return asyncio.run(async_root())
