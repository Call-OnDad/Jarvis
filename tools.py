"""
AHAS -- tools.py
Homelab command handlers. Claude appends #command:<name>:<args> to responses.
parse_command() dispatches to the right function and feeds results back to Claude.
"""

import assist
import proxmox
from config import CONTAINER_NAMES


def parse_command(command: str):
    command = command.strip().lower()
    parts   = command.split(":")

    if not parts:
        return

    action = parts[0]

    if action == "proxmox_status":
        result = proxmox.get_host_status()
        _feed_back(result)

    elif action == "container_list":
        result = proxmox.get_container_list()
        _feed_back(result)

    elif action == "container_start" and len(parts) >= 2:
        vmid   = parts[1]
        result = proxmox.container_action(vmid, "start")
        _feed_back(result)

    elif action == "container_stop" and len(parts) >= 2:
        vmid   = parts[1]
        result = proxmox.container_action(vmid, "stop")
        _feed_back(result)

    elif action == "disk_health":
        result = proxmox.get_disk_usage()
        _feed_back(result)

    elif action == "service_status":
        result = proxmox.check_services()
        _feed_back(result)

    elif action == "clear_memory":
        assist.clear_memory()
        assist.TTS(f"Memory cleared, {_owner()}.")

    else:
        print(f"[AHAS] Unknown command: {command}")


def _feed_back(system_info: str):
    print(f"[AHAS tools] {system_info}")
    prompt   = f"System data: {system_info}"
    response = assist.ask_question_memory(prompt)
    print(f"[AHAS] {response}")
    assist.speak(response)
    if "#" in response and len(response.split("#")) > 1:
        second_command = response.split("#")[1]
        parse_command(second_command)


def _owner():
    from config import OWNER_NAME
    return OWNER_NAME
