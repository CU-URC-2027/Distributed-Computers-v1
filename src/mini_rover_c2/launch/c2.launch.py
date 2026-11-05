"""Deploy the C2 GUI with optional inspection, preserving ROS network settings."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, ExecuteProcess, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.substitutions import ExecutableInPackage

from mini_rover_inspection.launching import inspection_handler, inspection_launch_arguments


def generate_launch_description():
    c2 = ExecuteProcess(
        cmd=[ExecutableInPackage(package='mini_rover_c2', executable='c2_gui'),
             '--namespace', LaunchConfiguration('namespace')],
        output='screen',
    )
    return LaunchDescription([
        DeclareLaunchArgument('namespace', default_value='rover1'),
        *inspection_launch_arguments(role='c2'),
        inspection_handler(c2, role='c2'),
        RegisterEventHandler(OnProcessExit(
            target_action=c2,
            on_exit=[EmitEvent(event=Shutdown(reason='C2 GUI exited'))],
        )),
        c2,
    ])
