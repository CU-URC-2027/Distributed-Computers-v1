#!/usr/bin/env bash
set -eo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
workspace_root="$project_root"
if [[ -d "$project_root/ros2_ws/src" ]]; then workspace_root="$project_root/ros2_ws"; fi
source /opt/ros/jazzy/setup.bash
cd "$workspace_root"
colcon build --symlink-install "$@"
