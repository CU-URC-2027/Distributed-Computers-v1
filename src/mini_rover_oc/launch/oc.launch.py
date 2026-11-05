"""OC-only deployment; inherit the caller's ROS domain and discovery settings."""
import os
from pathlib import Path
import re

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def deployment_settings(context):
    """Validate provenance and PWM before creating any process actions."""
    port = LaunchConfiguration('port').perform(context)
    simulated_text = LaunchConfiguration('simulated').perform(context).lower()
    if simulated_text not in ('true', 'false'):
        raise ValueError('simulated must be true or false')
    simulated = simulated_text == 'true'
    if simulated:
        # A simulator may expose a /tmp symlink to its PTY. Require the PTY
        # itself to exist, avoiding an accidental connection to physical USB.
        resolved = Path(port).resolve()
        if not re.fullmatch(r'/dev/pts/[0-9]+', str(resolved)) or not resolved.is_char_device():
            raise ValueError('simulated:=true requires an existing simulator PTY or a symlink to it')
    elif not port.startswith('/dev/serial/by-id/'):
        raise ValueError('Hardware port must be an explicit /dev/serial/by-id/ device; verify v2 firmware first')
    pwm_text = LaunchConfiguration('pwm_limit').perform(context)
    try:
        pwm_limit = int(pwm_text)
    except ValueError as exc:
        raise ValueError('pwm_limit must be an integer from 1 to 63') from exc
    if not 1 <= pwm_limit <= 63:
        raise ValueError('pwm_limit must be an integer from 1 to 63')
    return {
        'port': port, 'baud': 115200, 'simulated': simulated, 'pwm_limit': pwm_limit,
        'log_directory': os.path.expanduser(LaunchConfiguration('log_directory').perform(context)),
    }


def oc_nodes(context):
    from mini_rover_inspection.launching import inspection_handler

    target = Node(package='mini_rover_oc', executable='oc',
                  namespace=LaunchConfiguration('namespace'), output='screen',
                  parameters=[deployment_settings(context)])
    # Register first so even a rapidly starting process gets its exact PID
    # attached to the companion monitor.
    return [inspection_handler(target, role='oc'), target]


def generate_launch_description():
    from mini_rover_inspection.launching import inspection_launch_arguments

    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='rover1'),
        DeclareLaunchArgument('port', description='Verified v2 board by-id path, or an existing simulator PTY'),
        DeclareLaunchArgument('simulated', default_value='false',
                              description='Use true only for an already running PTY simulator'),
        DeclareLaunchArgument('pwm_limit', default_value='63'),
        DeclareLaunchArgument('log_directory', default_value=os.path.expanduser('~/weeny_ws/run_logs')),
        *inspection_launch_arguments(role='oc'),
        OpaqueFunction(function=oc_nodes),
    ])
