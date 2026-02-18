"""
Точка входа для запуска бота.
"""
import asyncio
import sys

# На Windows aiodns требует SelectorEventLoop (по умолчанию ProactorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from bot import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
