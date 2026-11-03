#!/usr/bin/env bash
set -eo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
workspace_root="$project_root"
if [[ -d "$project_root/ros2_ws/src" ]]; then workspace_root="$project_root/ros2_ws"; fi
source /opt/ros/jazzy/setup.bash
source "$workspace_root/install/setup.bash"
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-92}
exec ros2 launch mini_rover_oc demo.launch.py "$@"
