"""One event loop shared by in-process async test modules. Motor binds to the first loop it is used on, so modules that each
created their own loop failed when run in the same pytest process."""
import asyncio

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)
