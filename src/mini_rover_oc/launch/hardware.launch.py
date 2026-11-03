"""Physical USB route: OC and optional C2 only; never starts a simulator."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, OpaqueFunction, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.substitutions import ExecutableInPackage


def hardware_nodes(context):
    port = LaunchConfiguration('port').perform(context)
    if not port.startswith('/dev/serial/by-id/'):
        raise ValueError('Hardware port must be an explicit /dev/serial/by-id/ device; verify v2 firmware first')
    return [Node(package='mini_rover_oc', executable='oc',
        namespace=LaunchConfiguration('namespace'), output='screen',
        parameters=[{'port': port, 'baud': 115200, 'simulated': False, 'pwm_limit': 63}])]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('port', description='Verified v2 board stable /dev/serial/by-id/ path'),
        DeclareLaunchArgument('gui', default_value='true'),
        DeclareLaunchArgument('namespace', default_value='rover1'),
        SetEnvironmentVariable('ROS_DOMAIN_ID', '92'),
        SetEnvironmentVariable('ROS_AUTOMATIC_DISCOVERY_RANGE', 'LOCALHOST'),
        OpaqueFunction(function=hardware_nodes),
        ExecuteProcess(cmd=[ExecutableInPackage(package='mini_rover_c2', executable='c2_gui'),
            '--namespace', LaunchConfiguration('namespace')],
            condition=IfCondition(LaunchConfiguration('gui')), output='screen'),
    ])
