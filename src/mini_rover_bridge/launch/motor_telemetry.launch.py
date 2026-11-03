from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('port', description='Arduino serial device, preferably /dev/serial/by-id/...'),
        DeclareLaunchArgument('baud', default_value='115200'),
        Node(
            package='mini_rover_bridge', executable='motor_bridge', output='screen',
            parameters=[{
                'port': ParameterValue(LaunchConfiguration('port'), value_type=str),
                'baud': ParameterValue(LaunchConfiguration('baud'), value_type=int),
            }],
        ),
    ])
