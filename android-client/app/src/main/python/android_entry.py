import asyncio

from helper import (
    configure_listen_state_path,
    configure_runtime_logging,
    load_config,
    main_async,
    remove_listen_state_file,
    request_stop,
)


def run_helper(config_path):
    config = load_config(config_path)
    configure_runtime_logging(config_path, config)
    configure_listen_state_path(config)
    remove_listen_state_file()
    asyncio.run(main_async(config))


def stop_helper():
    return request_stop()
