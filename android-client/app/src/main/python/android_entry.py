import asyncio

from helper import configure_runtime_logging, load_config, main_async


def run_helper(config_path):
    config = load_config(config_path)
    configure_runtime_logging(config_path, config)
    asyncio.run(main_async(config))
