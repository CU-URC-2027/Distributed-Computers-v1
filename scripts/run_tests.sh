#!/usr/bin/env bash
set -eo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
workspace_root="$project_root"
if [[ -d "$project_root/ros2_ws/src" ]]; then workspace_root="$project_root/ros2_ws"; fi
source /opt/ros/jazzy/setup.bash
source "$workspace_root/install/setup.bash"
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
# Isolate tests from the operational domain 92. All motor links are PTYs.
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-97}
python3 -m pytest -q \
  "$workspace_root/src/mini_rover_control/test" \
  "$workspace_root/src/mini_rover_oc/test" \
  "$workspace_root/src/mini_rover_c2/test" \
  "$workspace_root/src/mini_rover_bridge/test" \
  "$workspace_root/src/mini_rover_c2/mini_rover_c2/legacy"/test_*.py \
  "$workspace_root/src/mini_rover_c2/mini_rover_c2/legacy/gui_regression.py"
check_binary=$(mktemp /tmp/mini-rover-guard-test.XXXXXX)
trap 'rm -f "$check_binary"' EXIT
g++ -std=c++11 -Wall -Wextra -pedantic \
  "$project_root/reference/c2_firmware/tests/guard_test.cpp" -o "$check_binary"
"$check_binary"

firmware_root=${ROVER_FIRMWARE_ROOT:-/home/liamolczak/mini-rover-prototype}
ROVER_WORKSPACE="$workspace_root" "$firmware_root/scripts/test_firmware.sh"
